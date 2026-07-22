from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
import queue
import re
import threading
import time
import unicodedata

from pythonosc import udp_client


MAX_CHATBOX_CHARS = 144
_VALID_AVATAR_PARAM_RE = re.compile(r"^[A-Za-z0-9_]+$")
DEFAULT_MIN_SEND_INTERVAL_S = 0.8
SEND_QUEUE_MAXSIZE = 32
MAX_AVATAR_STATE_ENTRIES = 256
_ALLOWED_STANDALONE_PUNCTUATION = frozenset({"!?", "\uff01\uff1f"})
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _QueuedOSCMessage:
    address: str
    arguments: tuple[object, ...]
    rate_limited: bool = False
    queued_at: float = 0.0
    min_interval_s: float | None = None
    generation: int = 0
    source: str = ""
    session_id: object = None
    sequence: object = None
    upstream_started_at: float = 0.0
    ui_delivered_at: float = 0.0
    completion_callback: Callable[[Mapping[str, object]], None] | None = None


class VRCOSCSender:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9000,
        min_send_interval_s: float = DEFAULT_MIN_SEND_INTERVAL_S,
    ):
        host_text = str(host or "").strip() or "127.0.0.1"
        try:
            port_number = int(port)
        except (TypeError, ValueError):
            port_number = 9000
        if not 0 < port_number <= 65535:
            port_number = 9000
        self._client = udp_client.SimpleUDPClient(host_text, port_number)
        self._min_send_interval_s = max(float(min_send_interval_s), 0.0)
        self._queue: queue.Queue[_QueuedOSCMessage | None] = queue.Queue(
            maxsize=SEND_QUEUE_MAXSIZE
        )
        self._state_lock = threading.Lock()
        self._worker_lock = threading.Lock()
        self._enqueue_lock = threading.Lock()
        self._last_sent_at = 0.0
        self._avatar_state: dict[str, object] = {}
        self._chatbox_generation = 0
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_error = ""
        self._closed = False
        self._start_worker()

    def _start_worker(self) -> None:
        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._send_loop,
            daemon=True,
            name="vrc-osc-sender",
        )
        self._worker.start()

    def _ensure_worker_running(self) -> bool:
        with self._state_lock:
            if self._closed:
                return False
        worker = self._worker
        if worker is not None and worker.is_alive():
            return True

        # The queue itself survives a failed worker. Starting one replacement
        # directly preserves FIFO order; draining and requeueing here could let
        # a concurrent producer jump ahead of an older completed sentence.
        with self._worker_lock:
            with self._state_lock:
                if self._closed:
                    return False
            worker = self._worker
            if worker is not None and worker.is_alive():
                return True

            self._start_worker()
            return True

    @property
    def last_error(self) -> str:
        with self._state_lock:
            return self._last_error

    @staticmethod
    def _normalize_text(text: str) -> str:
        safe = str(text or "").strip()
        punctuation = [
            char
            for char in safe
            if not char.isspace()
            and unicodedata.category(char) not in {"Cf", "Mn", "Me"}
        ]
        if (
            safe not in _ALLOWED_STANDALONE_PUNCTUATION
            and punctuation
            and all(
                unicodedata.category(char).startswith("P") for char in punctuation
            )
        ):
            return ""
        if len(safe) > MAX_CHATBOX_CHARS:
            safe = safe[: MAX_CHATBOX_CHARS - 3] + "..."
        return safe

    def _chatbox_min_interval_s(self, text: str) -> float:
        del text
        # Preserve FIFO pacing with the configured VRChat-safe interval. Text
        # length must not create an additional hidden backlog after translation
        # completions are already ordered by the realtime scheduler.
        return self._min_send_interval_s

    def _send_loop(self) -> None:
        stop_event = getattr(self, "_stop_event", None)
        while True:
            payload = self._queue.get()
            if payload is None:
                return
            if stop_event is not None and stop_event.is_set():
                self._notify_payload_completion(
                    payload,
                    outcome="stopped",
                    dequeued_at=time.monotonic(),
                )
                return

            dequeued_at = time.monotonic()
            rate_wait_s = 0.0
            send_started_at = 0.0
            sent_at = 0.0
            outcome = "cancelled"
            error_type = ""
            try:
                if payload.rate_limited:
                    with self._state_lock:
                        if payload.generation != self._chatbox_generation:
                            outcome = "stale"
                            continue
                    min_interval_s = (
                        payload.min_interval_s
                        if payload.min_interval_s is not None
                        else self._min_send_interval_s
                    )
                    wait_s = min_interval_s - (dequeued_at - self._last_sent_at)
                    if wait_s > 0:
                        rate_wait_s = wait_s
                        if stop_event is not None:
                            if stop_event.wait(wait_s):
                                outcome = "stopped"
                                return
                        else:
                            time.sleep(wait_s)
                    with self._state_lock:
                        if self._closed or payload.generation != self._chatbox_generation:
                            outcome = "stale" if not self._closed else "stopped"
                            continue

                with self._state_lock:
                    if self._closed:
                        outcome = "stopped"
                        return

                send_started_at = time.monotonic()
                self._client.send_message(payload.address, list(payload.arguments))
                sent_at = time.monotonic()
                outcome = "success"
                with self._state_lock:
                    self._last_error = ""
                    if payload.rate_limited:
                        self._last_sent_at = sent_at
                queued_at = payload.queued_at or dequeued_at
                logger.info(
                    "OSC send finished (address=%s source=%s session_id=%s "
                    "sequence=%s rate_limited=%s queue_wait_ms=%.0f "
                    "rate_wait_ms=%.0f udp_send_ms=%.0f total_ms=%.0f "
                    "pipeline_total_ms=%s)",
                    payload.address,
                    payload.source or "unknown",
                    payload.session_id if payload.session_id is not None else "unknown",
                    payload.sequence if payload.sequence is not None else "unknown",
                    payload.rate_limited,
                    (dequeued_at - queued_at) * 1000.0,
                    rate_wait_s * 1000.0,
                    (sent_at - send_started_at) * 1000.0,
                    (sent_at - queued_at) * 1000.0,
                    (
                        f"{max(0.0, sent_at - payload.upstream_started_at) * 1000.0:.0f}"
                        if payload.upstream_started_at > 0
                        else "na"
                    ),
                )
            except Exception as exc:
                outcome = "failed"
                error_type = type(exc).__name__
                with self._state_lock:
                    self._last_error = str(exc).strip() or exc.__class__.__name__
                logger.warning(
                    "OSC send failed (address=%s rate_limited=%s): %s",
                    payload.address,
                    payload.rate_limited,
                    exc,
                )
            finally:
                self._notify_payload_completion(
                    payload,
                    outcome=outcome,
                    dequeued_at=dequeued_at,
                    rate_wait_s=rate_wait_s,
                    send_started_at=send_started_at,
                    sent_at=sent_at,
                    error_type=error_type,
                )

    @staticmethod
    def _request_context_fields(
        request_context: Mapping[str, object] | None,
    ) -> dict[str, object]:
        context = request_context if isinstance(request_context, Mapping) else {}

        def timestamp(name: str) -> float:
            try:
                return max(0.0, float(context.get(name, 0.0) or 0.0))
            except (TypeError, ValueError):
                return 0.0

        return {
            "source": str(context.get("source", "") or ""),
            "session_id": context.get("session_id"),
            "sequence": context.get("sequence"),
            "upstream_started_at": timestamp("upstream_started_at"),
            "ui_delivered_at": timestamp("ui_delivered_at"),
        }

    @staticmethod
    def _notify_payload_completion(
        payload: _QueuedOSCMessage,
        *,
        outcome: str,
        dequeued_at: float = 0.0,
        rate_wait_s: float = 0.0,
        send_started_at: float = 0.0,
        sent_at: float = 0.0,
        error_type: str = "",
    ) -> None:
        callback = payload.completion_callback
        if not callable(callback):
            return
        queued_at = float(payload.queued_at or dequeued_at or time.monotonic())
        terminal_at = float(sent_at or time.monotonic())
        dequeued = float(dequeued_at or terminal_at)
        send_started = float(send_started_at or terminal_at)
        diagnostics: dict[str, object] = {
            "outcome": str(outcome or "unknown"),
            "source": payload.source,
            "session_id": payload.session_id,
            "sequence": payload.sequence,
            "queued_at": queued_at,
            "dequeued_at": dequeued,
            "finished_at": terminal_at,
            "queue_wait_s": max(0.0, dequeued - queued_at),
            "rate_wait_s": max(0.0, float(rate_wait_s or 0.0)),
            "send_s": (
                max(0.0, terminal_at - send_started)
                if sent_at > 0 and send_started_at > 0
                else 0.0
            ),
            "osc_total_s": max(0.0, terminal_at - queued_at),
            "pipeline_total_s": (
                max(0.0, terminal_at - payload.upstream_started_at)
                if payload.upstream_started_at > 0
                else None
            ),
            "ui_to_osc_s": (
                max(0.0, terminal_at - payload.ui_delivered_at)
                if payload.ui_delivered_at > 0
                else None
            ),
            "error_type": str(error_type or ""),
        }
        try:
            callback(diagnostics)
        except Exception:
            logger.debug("OSC completion callback failed", exc_info=True)

    def _enqueue_payload(self, payload: _QueuedOSCMessage | None) -> bool:
        if payload is not None and not self._ensure_worker_running():
            self._notify_payload_completion(payload, outcome="stopped")
            return False
        enqueue_lock = getattr(self, "_enqueue_lock", None)
        if enqueue_lock is None:
            enqueue_lock = threading.Lock()
            self._enqueue_lock = enqueue_lock
        with enqueue_lock:
            if payload is not None:
                with self._state_lock:
                    if getattr(self, "_closed", False):
                        self._notify_payload_completion(payload, outcome="stopped")
                        return False
            try:
                self._queue.put_nowait(payload)
                return True
            except queue.Full:
                pass

            if payload is None:
                return self._replace_queued_payload(
                    payload,
                    lambda item: item is not None and not item.rate_limited,
                )

            if payload.rate_limited:
                # A completed sentence may replace stale avatar telemetry, but
                # never an earlier sentence. If the chatbox lane itself is
                # saturated, drop the newest sentence and preserve FIFO order.
                replaced = self._replace_queued_payload(
                    payload,
                    lambda item: item is not None and not item.rate_limited,
                )
                if replaced:
                    return True
                logger.warning(
                    "OSC chatbox queue full; preserving earlier sentences and "
                    "dropping newest payload"
                )
                self._notify_payload_completion(payload, outcome="queue_full")
                return False

            # Avatar parameters are latest-wins only for the same address. They
            # must never evict a queued chatbox sentence.
            replaced = self._replace_queued_payload(
                payload,
                lambda item: (
                    item is not None
                    and not item.rate_limited
                    and item.address == payload.address
                ),
            )
            if not replaced:
                logger.warning(
                    "OSC avatar queue full; dropping newest update (address=%s)",
                    payload.address,
                )
                self._notify_payload_completion(payload, outcome="queue_full")
            return replaced

    def _replace_queued_payload(
        self,
        payload: _QueuedOSCMessage | None,
        predicate,
    ) -> bool:
        pending: list[_QueuedOSCMessage | None] = []
        while True:
            try:
                pending.append(self._queue.get_nowait())
            except queue.Empty:
                break

        remove_index = next(
            (index for index, item in enumerate(pending) if predicate(item)),
            None,
        )
        if remove_index is None:
            for item in pending:
                try:
                    self._queue.put_nowait(item)
                except queue.Full:
                    break
            try:
                self._queue.put_nowait(payload)
                return True
            except queue.Full:
                return False

        dropped = pending.pop(remove_index)
        if dropped is not None:
            logger.warning(
                "OSC queue full; replacing queued message "
                "(address=%s rate_limited=%s)",
                dropped.address,
                dropped.rate_limited,
            )
        for item in pending:
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                return False
        try:
            self._queue.put_nowait(payload)
            return True
        except queue.Full:
            return False

    def send_chatbox(
        self,
        text: str,
        immediate: bool = True,
        *,
        force: bool = False,
        request_context: Mapping[str, object] | None = None,
        completion_callback: Callable[[Mapping[str, object]], None] | None = None,
    ) -> str:
        safe = self._normalize_text(text)
        if not safe:
            return ""

        self._ensure_worker_running()
        del force
        with self._state_lock:
            generation = int(getattr(self, "_chatbox_generation", 0))
        context = self._request_context_fields(request_context)
        queued = self._enqueue_payload(
            _QueuedOSCMessage(
                address="/chatbox/input",
                arguments=(safe, immediate, False),
                rate_limited=True,
                queued_at=time.monotonic(),
                min_interval_s=self._chatbox_min_interval_s(safe),
                generation=generation,
                completion_callback=completion_callback,
                **context,
            )
        )
        return safe if queued else ""

    def clear_chatbox(self) -> bool:
        with self._state_lock:
            generation = int(getattr(self, "_chatbox_generation", 0))
        return self._enqueue_payload(
            _QueuedOSCMessage(
                address="/chatbox/input",
                arguments=("", True, False),
                rate_limited=True,
                queued_at=time.monotonic(),
                generation=generation,
            )
        )

    def clear_pending_chatbox(self) -> int:
        """Invalidate queued chatbox text while preserving avatar telemetry."""

        enqueue_lock = getattr(self, "_enqueue_lock", None)
        if enqueue_lock is None:
            enqueue_lock = threading.Lock()
            self._enqueue_lock = enqueue_lock
        with enqueue_lock:
            with self._state_lock:
                self._chatbox_generation = int(
                    getattr(self, "_chatbox_generation", 0)
                ) + 1
            pending: list[_QueuedOSCMessage | None] = []
            removed = 0
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is not None and item.rate_limited:
                    removed += 1
                    self._notify_payload_completion(item, outcome="cancelled")
                    continue
                pending.append(item)
            for item in pending:
                try:
                    self._queue.put_nowait(item)
                except queue.Full:
                    break
            return removed

    def send_avatar_parameter(self, name: str, value: object, *, force: bool = False) -> bool:
        param_name = str(name or "").strip()
        if not param_name:
            return False
        if not _VALID_AVATAR_PARAM_RE.match(param_name):
            logger.warning("Avatar parameter name contains invalid characters, ignoring: %r", param_name)
            return False

        self._ensure_worker_running()
        with self._state_lock:
            previous = self._avatar_state.get(param_name)
            if not force and previous == value:
                return False

        queued = self._enqueue_payload(
            _QueuedOSCMessage(
                address=f"/avatar/parameters/{param_name}",
                arguments=(value,),
                rate_limited=False,
                queued_at=time.monotonic(),
            )
        )
        if queued:
            with self._state_lock:
                self._avatar_state.pop(param_name, None)
                self._avatar_state[param_name] = value
                while len(self._avatar_state) > MAX_AVATAR_STATE_ENTRIES:
                    oldest = next(iter(self._avatar_state))
                    self._avatar_state.pop(oldest, None)
        return queued

    def send_avatar_bool(self, name: str, value: bool, *, force: bool = False) -> bool:
        return self.send_avatar_parameter(name, bool(value), force=force)

    def send_avatar_int(self, name: str, value: int, *, force: bool = False) -> bool:
        return self.send_avatar_parameter(name, int(value), force=force)

    def clear_avatar_state(self, names: list[tuple[str, object]] | None = None) -> None:
        defaults = names or []
        for name, value in defaults:
            self.send_avatar_parameter(name, value, force=True)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        stop_event = getattr(self, "_stop_event", None)
        if stop_event is not None:
            stop_event.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            self.clear_pending_chatbox()
            self._enqueue_payload(None)
            worker.join(timeout=1.0)
            if worker.is_alive():
                logger.warning("OSC sender worker did not stop within the shutdown timeout")
        self._worker = None
        while True:
            try:
                pending = self._queue.get_nowait()
            except queue.Empty:
                break
            if pending is not None:
                self._notify_payload_completion(pending, outcome="stopped")
        with self._state_lock:
            self._avatar_state.clear()
        self._close_client()

    def _close_client(self) -> None:
        client = getattr(self, "_client", None)
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
                return
            except Exception:
                logger.debug("Failed to close OSC client", exc_info=True)
        sock = getattr(client, "_sock", None)
        close_socket = getattr(sock, "close", None)
        if callable(close_socket):
            try:
                close_socket()
            except Exception:
                logger.debug("Failed to close OSC UDP socket", exc_info=True)
