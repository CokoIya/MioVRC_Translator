# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Plumbing shared by the plain-HTTP translators (Edge, Google, MyMemory,
DeepL, LibreTranslate): one requests session per worker thread, a HEAD
prewarm, and a bounded retry loop that honours rate limits.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Mapping

import requests

from src.utils.http_session_pool import ThreadLocalSessionPool
from src.utils.provider_diagnostics import safe_exception_summary
from src.utils.provider_network import configure_requests_session_for_url
from src.utils.provider_warmup import warmup_requests_session

from .base import BaseTranslator

logger = logging.getLogger(__name__)

USER_AGENT = "MioTranslator/1.3"


class WebRateLimited(RuntimeError):
    """The service said to slow down; ``retry_after_s`` is its hint, if any."""

    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


def _retry_after_seconds(response: object) -> float | None:
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return None
    try:
        value = float(str(headers.get("Retry-After", "")).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


class WebTranslatorBase(BaseTranslator):
    """Base for the HTTP translators; subclasses set the class constants."""

    # Shown in logs and errors, e.g. "Google Web translation attempt failed".
    PROVIDER_LABEL = "Web"
    RETRY_BASE_DELAY_S = 0.25
    RETRY_MAX_DELAY_S = 1.0
    # Status codes that mean "slow down" for this service.
    RATE_LIMIT_STATUSES: tuple[int, ...] = (429,)

    _timeout_s: float
    _max_retries: int

    def _init_session_pool(
        self,
        base_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        configure_network: bool = False,
    ) -> None:
        """One requests session per worker thread, closed with the translator.

        ``configure_network`` applies Mio's proxy/TLS policy for the URL; the
        public web endpoints need it, a local or keyed API does not.
        """

        extra_headers = dict(headers or {})

        def session_factory():
            session = requests.Session()
            session.headers.update({"User-Agent": USER_AGENT, **extra_headers})
            if configure_network:
                return configure_requests_session_for_url(session, base_url)
            return session

        self._session_pool = ThreadLocalSessionPool(session_factory)

    def _prewarm_session(self, url: str, *, operation: str = "translation") -> bool:
        """Warm DNS/TCP/TLS with a short HEAD, never with synthetic text."""

        result = warmup_requests_session(
            self._session_pool.get(),
            url,
            method="HEAD",
            timeout_s=min(self._timeout_s, 3.0),
        )
        logger.log(
            logging.INFO if result.succeeded else logging.WARNING,
            "%s %s prewarm %s "
            "(probe_status=%s probe_route_accepted=%s elapsed_ms=%.0f error_type=%s method=HEAD)",
            self.PROVIDER_LABEL,
            operation,
            "transport reachable" if result.succeeded else "failed",
            result.status_code if result.status_code is not None else "unknown",
            bool(result.status_code is not None and 200 <= result.status_code < 400),
            result.elapsed_s * 1000.0,
            result.error_type or "none",
        )
        return result.succeeded

    def _check_status(self, response: requests.Response) -> None:
        """Raise for a service-specific status before the generic HTTP check."""

        if response.status_code in self.RATE_LIMIT_STATUSES:
            raise WebRateLimited(
                f"{self.PROVIDER_LABEL} rate limit reached",
                _retry_after_seconds(response),
            )

    def _retry_delay(self, attempt: int, exc: Exception) -> float | None:
        """Seconds to wait before the next attempt, or None to stop retrying.

        Linear backoff with jitter, so workers that failed together do not
        retry together. A rate limit that asks for longer than the backoff cap
        is not retried: a subtitle cannot wait that long, and asking again
        sooner only prolongs the limit.
        """

        delay = min(self.RETRY_BASE_DELAY_S * (attempt + 1), self.RETRY_MAX_DELAY_S)
        if isinstance(exc, WebRateLimited) and exc.retry_after_s is not None:
            if exc.retry_after_s > self.RETRY_MAX_DELAY_S:
                return None
            delay = max(delay, exc.retry_after_s)
        return delay * random.uniform(0.8, 1.2)

    def _send_with_retries(
        self,
        send: Callable[[requests.Session], requests.Response],
        parse: Callable[[requests.Response], str],
        *,
        operation: str = "translation",
    ) -> str:
        """Send, check, parse; retry up to ``_max_retries`` times on failure."""

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            started = time.perf_counter()
            try:
                response = send(self._session_pool.get())
                self._check_status(response)
                response.raise_for_status()
                result = parse(response)
                logger.info(
                    "%s %s finished (elapsed=%.2fs)",
                    self.PROVIDER_LABEL,
                    operation,
                    time.perf_counter() - started,
                )
                return result
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "%s %s attempt failed: %s",
                    self.PROVIDER_LABEL,
                    operation,
                    safe_exception_summary(exc),
                )
                if attempt >= self._max_retries:
                    break
                delay = self._retry_delay(attempt, exc)
                if delay is None:
                    break
                time.sleep(delay)
        raise RuntimeError(
            f"{self.PROVIDER_LABEL} {operation} failed: {last_exc}"
        ) from last_exc
