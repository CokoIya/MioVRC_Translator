"""HuggingFace model downloader with pause / resume / cancel support.

Download state machine:
  IDLE → DOWNLOADING ⇄ PAUSED → COMPLETED
                    ↘ CANCELLED / ERROR

Speed strategy:
  - Mirror probe: race hf-mirror.com (China CDN) vs huggingface.co; the first
    healthy responder wins for the whole session.
  - Parallel range: large files are split into N segments downloaded in
    parallel via HTTP Range headers. The total throughput on a residential
    link with 4–8 concurrent streams is typically several × that of a single
    stream because the per-connection cap dominates over the link's bandwidth.
  - Connection pool: a single requests.Session reuses TCP/TLS handshakes
    across files and segments via an HTTPAdapter with a larger pool.
  - Streaming chunks: small files and the small remainder use stable 2 MB
    chunks so progress updates stay smooth without misleading adaptive state.

Resume safety:
  Each parallel segment writes to a part file `dest.partN`. On resume the
  downloader picks each part up where it left off. When every segment is
  finished the parts are stitched into `dest` and removed.
"""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import logging
import os
import pathlib
import re
import socket
import tempfile
import threading
import time
import weakref
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from typing import Callable
from urllib.parse import unquote, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

from src.utils.app_paths import (
    atomic_replace_secure_file,
    open_secure_append,
    open_secure_read,
    require_real_directory,
    secure_file_path,
    secure_file_size,
    secure_unlink,
    writable_app_dir,
)
from src.utils.secure_http import open_validated_requests_response

logger = logging.getLogger(__name__)

# Streaming chunk size. Keep this fixed; requests.iter_content binds the
# chunk size when the iterator is created.
_CHUNK_SIZE = 2 * 1024 * 1024

# Parallel range download tuning
_PARALLEL_THRESHOLD_BYTES = 32 * 1024 * 1024  # files >= 32 MB use ranges
_PARALLEL_PART_TARGET = 64 * 1024 * 1024  # ~64 MB per segment
_PARALLEL_MIN_PARTS = 2
_PARALLEL_MAX_PARTS = 3  # cap CPU/disk pressure on low-end PCs

_SPEED_WINDOW_S = 4.0  # rolling window for speed / ETA
_UI_EMIT_MIN_S = 0.15  # throttle: emit to UI at most every 150 ms

# Mirror candidates — tried in order; first to respond wins.
_MIRRORS = (
    "https://hf-mirror.com",  # mainland China CDN (ModelScope-backed)
    "https://huggingface.co",  # official
)
_PROBE_TIMEOUT = 4  # max seconds for any single mirror probe
_PROBE_BYTES = 768 * 1024  # bytes pulled to measure throughput
_PROBE_LOCALE_BONUS = 1.5  # locale-preferred mirror wins ties up to this ratio
_CONNECT_TIMEOUT = 15
_READ_TIMEOUT = 60
_MAX_MODEL_FILE_BYTES = 8 * 1024 * 1024 * 1024
_MAX_MODEL_TOTAL_BYTES = 16 * 1024 * 1024 * 1024
_CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+)$", re.IGNORECASE)

# Browser-style UA: a few mirror CDNs (incl. some hf-mirror nodes) reject
# the default python-requests UA with 403.
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36 MioTranslator/1.3"
    ),
    "Accept-Encoding": "identity",  # model.bin is already compressed
}


def _make_session() -> requests.Session:
    """Session with a pool large enough for parallel-range workers."""
    sess = requests.Session()
    sess.headers.update(_DEFAULT_HEADERS)
    adapter = HTTPAdapter(
        pool_connections=8,
        pool_maxsize=_PARALLEL_MAX_PARTS * 2,
        max_retries=0,
    )
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    return sess


class DownloadState(str, Enum):
    IDLE = "idle"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class DownloadProgress:
    state: DownloadState = DownloadState.IDLE
    file_name: str = ""
    file_index: int = 0
    file_count: int = 0
    file_bytes: int = 0
    file_total: int = 0
    total_bytes: int = 0
    total_total: int = 0
    speed_bps: float = 0.0
    eta_s: float = 0.0
    error: str = ""
    mirror: str = ""  # which base URL is being used

    @property
    def overall_fraction(self) -> float:
        return self.total_bytes / self.total_total if self.total_total > 0 else 0.0

    @property
    def speed_mb(self) -> str:
        mb = self.speed_bps / 1_048_576
        return f"{mb:.1f} MB/s" if mb >= 0.1 else f"{self.speed_bps / 1024:.0f} KB/s"

    @property
    def eta_str(self) -> str:
        s = int(self.eta_s)
        if s <= 0:
            return ""
        m, s = divmod(s, 60)
        return f"{m}分{s:02d}秒" if m else f"{s}秒"


# File list for each HF model repo. Ordered so the tiny files come first
# (fast to resume), with the large model weight file last.
_HF_MODEL_FILES: dict[str, list[str]] = {
    "iic/SenseVoiceSmall": [
        "am.mvn",
        "chn_jpn_yue_eng_ko_spectok.bpe.model",
        "config.yaml",
        "configuration.json",
        "model.pt",
    ],
    "iic/speech_whisper-small_asr_english": [
        "configuration.json",
        "small.en.pb",
    ],
    "coqui/XTTS-v2": [
        "model.pth",
        "config.json",
        "vocab.json",
        "dvae.pth",
        "mel_stats.pth",
    ],
    "coqui/xtts_speaker_encoder": [
        "model.pth",
        "config.json",
    ],
    "ku-nlp/deberta-v2-large-japanese-char-wwm": [
        "config.json",
        "special_tokens_map.json",
        "tokenizer_config.json",
        "vocab.txt",
        "model.safetensors",
    ],
    "microsoft/deberta-v3-large": [
        "config.json",
        "tokenizer_config.json",
        "spm.model",
        "pytorch_model.bin",
    ],
    "hfl/chinese-roberta-wwm-ext-large": [
        "config.json",
        "added_tokens.json",
        "special_tokens_map.json",
        "tokenizer_config.json",
        "tokenizer.json",
        "vocab.txt",
        "pytorch_model.bin",
    ],
}

_HF_MODEL_REVISIONS: dict[str, str] = {
    "iic/SenseVoiceSmall": "70514a3da51f1160f51d18449dab6128bbd4928b",
    "coqui/XTTS-v2": "6c2b0d75eae4b7047358e3b6bd9325f857d43f77",
    "ku-nlp/deberta-v2-large-japanese-char-wwm": (
        "547b0e8b044fba3f9b84d0ab9f990440bd130c8b"
    ),
    "microsoft/deberta-v3-large": "64a8c8eab3e352a784c658aef62be1662607476f",
    "hfl/chinese-roberta-wwm-ext-large": (
        "a25cc9e05974bd9687e528edd516f2cfdb3f5db9"
    ),
}

_HF_MODEL_FILE_SHA256: dict[str, dict[str, str]] = {
    "iic/SenseVoiceSmall": {
        "am.mvn": "29b3c740a2c0cfc6b308126d31d7f265fa2be74f3bb095cd2f143ea970896ae5",
        "chn_jpn_yue_eng_ko_spectok.bpe.model": (
            "aa87f86064c3730d799ddf7af3c04659151102cba548bce325cf06ba4da4e6a8"
        ),
        "config.yaml": "f71e239ba36705564b5bf2d2ffd07eece07b8e3f2bbf6d2c99d8df856339ac19",
        "configuration.json": (
            "02810a7f8e9e8aee10370a265f7e799728ce25b4c00cdbf4602b303ee395a38e"
        ),
        "model.pt": "833ca2dcfdf8ec91bd4f31cfac36d6124e0c459074d5e909aec9cabe6204a3ea",
    },
    "coqui/XTTS-v2": {
        "model.pth": "c7ea20001c6a0a841c77e252d8409f6a74fb423e79b3206a0771ba5989776187",
        "config.json": "ef262b1454dd2a77e1461b0b2cd53e19b8a7624cc131b837d36df67356bc75e8",
        "vocab.json": "928260878a59da8a72a2a5b7687fea29d5106137669d90945430fe17e415304a",
        "dvae.pth": "b29bc227d410d4991e0a8c09b858f77415013eeb9fba9650258e96095557d97a",
        "mel_stats.pth": "1f69422a8a8f344c4fca2f0c6b8d41d2151d6615b7321e48e6bb15ae949b119c",
    },
    "ku-nlp/deberta-v2-large-japanese-char-wwm": {
        "config.json": "8f387ab4c6b36e47c7071327c7a42099002781279b00a7e6a7fe88f3da237a3f",
        "special_tokens_map.json": (
            "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3"
        ),
        "tokenizer_config.json": (
            "1cc5203f09ecac12bb7a98a05cb9c2e39a9e37a113a7d85d12542ef29190583b"
        ),
        "vocab.txt": "902cbd7e218aaf23a72955533293ceac12fcc4e010ad98c0c14757b94ce7abb6",
        "model.safetensors": (
            "2630f547d018524a7b03506a42c700cbac49e29bdc441845b0615bfb3b5d74d2"
        ),
    },
    "microsoft/deberta-v3-large": {
        "config.json": "ddec8b81d079d218ce9e54fc0af5d1d5937d6d53b5d42e70c1f251a1cebc830d",
        "tokenizer_config.json": (
            "3f3978e0c036f2c2588cac34a6047cbb0af0b0dc1814254e291028529805496d"
        ),
        "spm.model": "c679fbf93643d19aab7ee10c0b99e460bdbc02fedf34b92b05af343b4af586fd",
        "pytorch_model.bin": (
            "dd5b5d93e2db101aaf281df0ea1216c07ad73620ff59c5b42dccac4bf2eef5b5"
        ),
    },
    "hfl/chinese-roberta-wwm-ext-large": {
        "config.json": "53d086daf0ccdddbeb78f8798f34c685a3c48089fa21ec61300527f083fa2563",
        "added_tokens.json": (
            "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
        ),
        "special_tokens_map.json": (
            "303df45a03609e4ead04bc3dc1536d0ab19b5358db685b6f3da123d05ec200e3"
        ),
        "tokenizer_config.json": (
            "61785aeaba176fba6d6489f27dccfd2ddee6aee2af0e590451cab7d8b57e0874"
        ),
        "tokenizer.json": (
            "53ff61207898738bbdc000f38abebef01041c8d23b6270c11855fc692d0a3ad6"
        ),
        "vocab.txt": "45bbac6b341c319adc98a532532882e91a9cefc0329aa57bac9ae761c27b291c",
        "pytorch_model.bin": (
            "4ac62d49144d770c5ca9a5d1d3039c4995665a080febe63198189857c6bd11cd"
        ),
    },
}

_VERIFIED_FILE_CACHE_MAX_ENTRIES = 512
_VERIFIED_FILE_CACHE: OrderedDict[tuple[str, int, int, str], bool] = OrderedDict()
_VERIFIED_FILE_CACHE_LOCK = threading.Lock()


def _model_dir(model_id: str) -> pathlib.Path:
    clean = str(model_id or "").strip()
    parts = clean.split("/")
    if not 1 <= len(parts) <= 2 or any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", part)
        or part in {".", ".."}
        for part in parts
    ):
        raise ValueError(f"Unsafe Hugging Face model identifier: {model_id!r}")
    slug = "--".join(parts)
    return require_real_directory(writable_app_dir() / "runtime_models" / slug)


def model_dir(model_id: str) -> pathlib.Path:
    """Return the managed download directory for a Hugging Face model."""
    return _model_dir(model_id)


def _repo_url(base: str, model_id: str, filename: str) -> str:
    revision = _HF_MODEL_REVISIONS.get(model_id, "main")
    return f"{base}/{model_id}/resolve/{revision}/{filename}"


def _trusted_model_manifest(model_id: str) -> tuple[list[str], dict[str, str]]:
    files = _HF_MODEL_FILES.get(model_id)
    hashes = _HF_MODEL_FILE_SHA256.get(model_id)
    revision = _HF_MODEL_REVISIONS.get(model_id)
    if (
        not files
        or not hashes
        or not revision
        or set(files) != set(hashes)
        or not re.fullmatch(r"[0-9a-f]{40}", revision)
        or any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in hashes.values())
    ):
        raise RuntimeError(
            f"No complete pinned integrity manifest exists for model {model_id!r}"
        )
    return list(files), dict(hashes)


def _secure_file_matches_sha256(path: pathlib.Path, expected_sha256: str) -> bool:
    try:
        path = secure_file_path(path, must_exist=True)
        stat_result = path.stat()
        cache_key = (
            str(path),
            int(stat_result.st_size),
            int(stat_result.st_mtime_ns),
            expected_sha256,
        )
        with _VERIFIED_FILE_CACHE_LOCK:
            cached = _VERIFIED_FILE_CACHE.get(cache_key)
            if cached is not None:
                _VERIFIED_FILE_CACHE.move_to_end(cache_key)
        if cached is not None:
            return cached
        hasher = hashlib.sha256()
        with open_secure_read(path, binary=True) as handle:
            while True:
                chunk = handle.read(8 * 1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        matches = hasher.hexdigest() == expected_sha256
        with _VERIFIED_FILE_CACHE_LOCK:
            _VERIFIED_FILE_CACHE[cache_key] = matches
            _VERIFIED_FILE_CACHE.move_to_end(cache_key)
            while len(_VERIFIED_FILE_CACHE) > _VERIFIED_FILE_CACHE_MAX_ENTRIES:
                _VERIFIED_FILE_CACHE.popitem(last=False)
        return matches
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return False


def _is_safe_model_download_url(url: str) -> bool:
    raw = str(url or "").strip()
    try:
        parsed = urlsplit(raw)
        host = str(parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port
    except (TypeError, ValueError):
        return False
    if (
        parsed.scheme.casefold() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
    ):
        return False
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            return False
        addresses = []
        for record in records:
            try:
                address = ipaddress.ip_address(record[4][0].split("%", 1)[0])
            except (IndexError, TypeError, ValueError):
                return False
            if address not in addresses:
                addresses.append(address)
    else:
        addresses = [literal]
    return bool(addresses) and all(address.is_global for address in addresses)


def _normalise_base_url(value: str) -> str:
    """Return a canonical HTTPS mirror base, or an empty string if unsafe."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
    ):
        return ""
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return ""
    decoded_path = unquote(parsed.path or "")
    if (
        "\\" in decoded_path
        or any(ord(character) < 0x20 for character in decoded_path)
        or any(part in {".", ".."} for part in decoded_path.split("/"))
    ):
        return ""
    path = (parsed.path or "").rstrip("/")
    netloc = f"[{host}]" if ":" in host else host
    return urlunsplit(("https", netloc, path, "", ""))


def _validate_https_response(response: object, requested_url: str, *, label: str) -> None:
    """Reject redirects that leave authenticated HTTPS transport."""

    raw_url = str(getattr(response, "url", "") or requested_url)
    try:
        parsed = urlsplit(raw_url)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f"{label} returned an invalid response URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise RuntimeError(f"{label} redirected to an unsafe response URL")


def _mirror_candidates() -> list[str]:
    """Mirror list, with optional user/operator overrides first.

    Mainland users often need a local Hugging Face proxy. Let advanced users
    and packaged builds prepend mirrors without code changes while keeping the
    built-in hf-mirror/huggingface fallback chain intact.
    """
    candidates: list[str] = []
    for env_name in ("MIO_HF_MIRROR_BASES", "HF_ENDPOINT", "HF_HUB_ENDPOINT"):
        raw = os.environ.get(env_name, "")
        for item in raw.replace(";", ",").split(","):
            base = _normalise_base_url(item)
            if base and base not in candidates:
                candidates.append(base)
    for base in _MIRRORS:
        clean = _normalise_base_url(base)
        if clean not in candidates:
            candidates.append(clean)
    return candidates


def _mirror_fallback_order(selected: str) -> list[str]:
    candidates = _mirror_candidates()
    selected = _normalise_base_url(selected)
    ordered = [selected] if selected else []
    preferred = _preferred_mirror_for_locale()
    if preferred:
        preferred = _normalise_base_url(preferred)
        if preferred not in ordered and preferred in candidates:
            ordered.append(preferred)
    for base in candidates:
        if base not in ordered:
            ordered.append(base)
    return ordered


def model_is_complete(model_id: str) -> bool:
    """True if every required file matches the pinned integrity manifest."""
    directory = _model_dir(model_id)
    try:
        files, hashes = _trusted_model_manifest(model_id)
        for filename in files:
            path = secure_file_path(directory / filename, must_exist=True)
            size = secure_file_size(path)
            if size <= 0 or size > _MAX_MODEL_FILE_BYTES:
                return False
            if not _secure_file_matches_sha256(path, hashes[filename]):
                return False
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return False
    return True


def _response_content_length(response: object, *, label: str) -> int | None:
    headers = getattr(response, "headers", {})
    raw = headers.get("Content-Length") if headers is not None else None
    if raw in (None, ""):
        return None
    normalized = str(raw).strip()
    if re.fullmatch(r"[0-9]+", normalized) is None:
        raise RuntimeError(f"{label} returned an invalid Content-Length")
    try:
        value = int(normalized)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} returned an invalid Content-Length") from exc
    if value < 0:
        raise RuntimeError(f"{label} returned an invalid Content-Length")
    return value


def _validate_content_range(
    response: object,
    *,
    requested_start: int,
    requested_end: int,
    expected_total: int | None,
    label: str,
) -> int:
    headers = getattr(response, "headers", {})
    raw = headers.get("Content-Range") if headers is not None else None
    match = _CONTENT_RANGE_RE.fullmatch(str(raw or "").strip())
    if match is None:
        raise RuntimeError(f"{label} returned an invalid Content-Range")
    start, end, total = (int(value) for value in match.groups())
    if (
        start != requested_start
        or end != requested_end
        or end < start
        or total <= end
        or total > _MAX_MODEL_FILE_BYTES
        or (expected_total is not None and total != expected_total)
    ):
        raise RuntimeError(f"{label} returned an unexpected Content-Range")
    content_length = _response_content_length(response, label=label)
    expected_length = requested_end - requested_start + 1
    if content_length is not None and content_length != expected_length:
        raise RuntimeError(f"{label} returned an unexpected Content-Length")
    return total


ProgressCallback = Callable[[DownloadProgress], None]


def _probe_mirror_throughput(base: str, model_id: str, filename: str) -> float | None:
    """Pull a small range from the mirror and return measured B/s.

    HEAD-only probes mislead overseas users: a CN mirror can answer HEAD in
    ~200 ms but cap real downloads at 1 MB/s, while huggingface.co might be
    300 ms HEAD but sustain 30 MB/s. Measuring actual bytes-per-second on a
    short range is a far better predictor of full-download time.
    """
    url = _repo_url(base, model_id, filename)
    headers = dict(_DEFAULT_HEADERS)
    headers["Range"] = f"bytes=0-{_PROBE_BYTES - 1}"
    try:
        t0 = time.monotonic()
        with open_validated_requests_response(
            url,
            url_validator=_is_safe_model_download_url,
            timeout=(_PROBE_TIMEOUT, _PROBE_TIMEOUT),
            label="Hugging Face mirror probe",
            headers=headers,
            stream=True,
            max_redirects=5,
            request_get=requests.get,
        ) as response:
            if response.status_code >= 400:
                return None
            bytes_read = 0
            deadline = t0 + _PROBE_TIMEOUT
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                bytes_read += len(chunk)
                if bytes_read >= _PROBE_BYTES or time.monotonic() >= deadline:
                    break
        elapsed = max(time.monotonic() - t0, 1e-3)
        if bytes_read <= 0:
            return None
        return bytes_read / elapsed
    except Exception:
        return None


def _preferred_mirror_for_locale() -> str | None:
    """Return the mirror users in this locale should prefer, if any."""
    try:
        from src.utils.locale_detect import get_system_language

        lang = get_system_language()
    except Exception:
        return None
    if lang in {"zh", "yue"}:
        return "https://hf-mirror.com"
    # Default to the canonical CDN for everyone else (USA/Europe/JP/KR/...).
    return "https://huggingface.co"


def _select_mirror(model_id: str, first_file: str) -> str:
    """Race mirrors by measured throughput; bias by system locale on ties."""
    candidates = _mirror_candidates()
    results: dict[str, float] = {}  # base_url -> bytes/sec
    lock = threading.Lock()

    def _try(base: str) -> None:
        bps = _probe_mirror_throughput(base, model_id, first_file)
        if bps is not None and bps > 0:
            with lock:
                results[base] = bps
            logger.debug("Mirror probe %s: %.2f MB/s", base, bps / 1_048_576)
        else:
            logger.debug("Mirror probe %s: unreachable / 0 B/s", base)

    threads = [
        threading.Thread(target=_try, args=(b,), daemon=True) for b in candidates
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=_PROBE_TIMEOUT + 1)

    if not results:
        preferred = _preferred_mirror_for_locale()
        fallback = (
            _normalise_base_url(preferred)
            if preferred and _normalise_base_url(preferred) in candidates
            else candidates[0]
        )
        logger.warning("All mirror probes failed, defaulting to %s", fallback)
        return fallback

    fastest = max(results, key=results.__getitem__)
    preferred = _preferred_mirror_for_locale()
    chosen = fastest

    # Locale bias: if the locale-preferred mirror is within PROBE_LOCALE_BONUS×
    # of the fastest, pick it instead. Avoids surprises like a Chinese user
    # being routed to huggingface.co when hf-mirror is only marginally slower.
    if preferred and preferred in results and preferred != fastest:
        if results[preferred] * _PROBE_LOCALE_BONUS >= results[fastest]:
            chosen = preferred
            logger.info(
                "Mirror: %s wins on locale bias (%.1f MB/s vs %s %.1f MB/s)",
                preferred,
                results[preferred] / 1_048_576,
                fastest,
                results[fastest] / 1_048_576,
            )

    logger.info(
        "Selected mirror: %s (%.1f MB/s probed)", chosen, results[chosen] / 1_048_576
    )
    return chosen


class HFModelDownloader:
    """Thread-safe downloader for a single HuggingFace model."""

    def __init__(self, model_id: str) -> None:
        self._model_id = model_id
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused initially
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._progress = DownloadProgress()
        # UI widgets register bound methods here while downloader instances are
        # process-wide singletons.  Keeping those methods strongly would retain
        # every closed download window for the rest of the process.
        self._listeners: list[ProgressCallback | weakref.WeakMethod] = []
        self._speed_samples: deque[tuple[float, int]] = deque()
        self._last_emit_t: float = 0.0
        self._session: requests.Session | None = None

    # ── public API ──────────────────────────────────────────────────────────

    def add_listener(self, cb: ProgressCallback) -> None:
        with self._lock:
            live_listeners: list[ProgressCallback | weakref.WeakMethod] = []
            already_registered = False
            for entry in self._listeners:
                target = entry() if isinstance(entry, weakref.WeakMethod) else entry
                if target is None:
                    continue
                live_listeners.append(entry)
                if target == cb:
                    already_registered = True
            self._listeners = live_listeners
            if already_registered:
                return
            if getattr(cb, "__self__", None) is not None:
                try:
                    self._listeners.append(weakref.WeakMethod(cb))
                    return
                except TypeError:
                    pass
            self._listeners.append(cb)

    def remove_listener(self, cb: ProgressCallback) -> None:
        with self._lock:
            retained: list[ProgressCallback | weakref.WeakMethod] = []
            for entry in self._listeners:
                target = entry() if isinstance(entry, weakref.WeakMethod) else entry
                if target is None or target == cb:
                    continue
                retained.append(entry)
            self._listeners = retained

    @property
    def state(self) -> DownloadState:
        with self._lock:
            return self._progress.state

    @property
    def progress(self) -> DownloadProgress:
        with self._lock:
            return copy.copy(self._progress)

    def start(self) -> None:
        with self._lock:
            if (
                self._progress.state in (
                    DownloadState.DOWNLOADING,
                    DownloadState.COMPLETED,
                )
                or (self._thread is not None and self._thread.is_alive())
            ):
                return
            self._progress = DownloadProgress(state=DownloadState.DOWNLOADING)
            self._cancel_event.clear()
            self._pause_event.set()
            self._last_emit_t = 0.0
            self._speed_samples.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="hf-download"
        )
        self._thread.start()

    def pause(self) -> None:
        with self._lock:
            if self._progress.state != DownloadState.DOWNLOADING:
                return
            self._progress.state = DownloadState.PAUSED
        self._pause_event.clear()
        self._emit(force=True)

    def resume(self) -> None:
        with self._lock:
            if self._progress.state != DownloadState.PAUSED:
                return
            self._progress.state = DownloadState.DOWNLOADING
        self._pause_event.set()
        self._emit(force=True)

    def cancel(self) -> None:
        self._cancel_event.set()
        self._pause_event.set()  # unblock if paused
        with self._lock:
            self._progress.state = DownloadState.CANCELLED
        self._emit(force=True)

    # ── internals ───────────────────────────────────────────────────────────

    def _emit(self, *, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not force and (now - self._last_emit_t) < _UI_EMIT_MIN_S:
                return
            self._last_emit_t = now
            p = copy.copy(self._progress)
            listeners: list[ProgressCallback] = []
            retained: list[ProgressCallback | weakref.WeakMethod] = []
            for entry in self._listeners:
                target = entry() if isinstance(entry, weakref.WeakMethod) else entry
                if target is None:
                    continue
                retained.append(entry)
                listeners.append(target)
            self._listeners = retained
        for cb in listeners:
            try:
                cb(p)
            except Exception:
                logger.debug("Progress listener raised", exc_info=True)

    def _set_state(self, state: DownloadState, error: str = "") -> None:
        with self._lock:
            self._progress.state = state
            if error:
                self._progress.error = error

    def _update_progress(
        self,
        *,
        file_name: str,
        file_index: int,
        file_count: int,
        file_bytes: int,
        file_total: int,
        total_bytes: int,
        total_total: int,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            self._speed_samples.append((now, total_bytes))
            cutoff = now - _SPEED_WINDOW_S
            while self._speed_samples and self._speed_samples[0][0] < cutoff:
                self._speed_samples.popleft()

            speed_bps = 0.0
            if len(self._speed_samples) >= 2:
                t0, b0 = self._speed_samples[0]
                t1, b1 = self._speed_samples[-1]
                dt = t1 - t0
                if dt > 0:
                    speed_bps = (b1 - b0) / dt

            remaining = total_total - total_bytes
            eta_s = remaining / speed_bps if speed_bps > 0 else 0.0

            self._progress.file_name = file_name
            self._progress.file_index = file_index
            self._progress.file_count = file_count
            self._progress.file_bytes = file_bytes
            self._progress.file_total = file_total
            self._progress.total_bytes = total_bytes
            self._progress.total_total = total_total
            self._progress.speed_bps = speed_bps
            self._progress.eta_s = eta_s

        self._emit()

    def _run(self) -> None:
        try:
            self._session = _make_session()
            self._download_all()
        except Exception as exc:
            logger.exception("HF model download failed: %s", self._model_id)
            self._set_state(DownloadState.ERROR, str(exc))
            self._emit(force=True)
        finally:
            if self._session:
                self._session.close()
                self._session = None

    def _download_all(self) -> None:
        files, trusted_hashes = _trusted_model_manifest(self._model_id)
        directory = _model_dir(self._model_id)

        # Select fastest mirror before any real download
        base_url = _select_mirror(self._model_id, files[0])
        base_urls = _mirror_fallback_order(base_url)
        with self._lock:
            self._progress.mirror = base_url

        # Pre-calculate total size via HEAD
        file_sizes: list[int] = []
        for filename in files:
            size = self._head_size(base_url, filename)
            if size <= 0:
                for fallback_base in base_urls[1:]:
                    size = self._head_size(fallback_base, filename)
                    if size > 0:
                        break
            file_sizes.append(size)
        total_total = sum(file_sizes)
        if total_total > _MAX_MODEL_TOTAL_BYTES:
            raise RuntimeError("Model download exceeds the maximum allowed total size")

        total_bytes_done = 0
        for i, (filename, expected_size) in enumerate(zip(files, file_sizes)):
            if self._cancel_event.is_set():
                return

            dest = secure_file_path(directory / filename)
            existing = secure_file_size(dest, missing_ok=True)
            if existing > _MAX_MODEL_FILE_BYTES:
                raise RuntimeError(f"Existing model file is oversized: {filename}")

            if (
                expected_size > 0
                and existing == expected_size
                and _secure_file_matches_sha256(dest, trusted_hashes[filename])
            ):
                total_bytes_done += existing
                if total_bytes_done > _MAX_MODEL_TOTAL_BYTES:
                    raise RuntimeError(
                        "Model download exceeds the maximum allowed total size"
                    )
                self._update_progress(
                    file_name=filename,
                    file_index=i,
                    file_count=len(files),
                    file_bytes=existing,
                    file_total=expected_size,
                    total_bytes=total_bytes_done,
                    total_total=total_total,
                )
                continue
            if existing > 0:
                secure_unlink(dest)

            total_bytes_done = self._download_file_with_fallback(
                base_urls=base_urls,
                filename=filename,
                dest=dest,
                file_index=i,
                file_count=len(files),
                expected_size=expected_size,
                total_bytes_so_far=total_bytes_done,
                total_total=total_total,
            )
            if self._cancel_event.is_set():
                return
            if total_bytes_done > _MAX_MODEL_TOTAL_BYTES:
                raise RuntimeError(
                    "Model download exceeds the maximum allowed total size"
                )
            final_size = secure_file_size(dest)
            if final_size <= 0 or final_size > _MAX_MODEL_FILE_BYTES:
                raise RuntimeError(f"Downloaded model file has an invalid size: {filename}")
            if expected_size > 0 and final_size != expected_size:
                raise RuntimeError(
                    f"Downloaded model file size mismatch for {filename}: "
                    f"{final_size} != {expected_size}"
                )
            if not _secure_file_matches_sha256(dest, trusted_hashes[filename]):
                secure_unlink(dest, missing_ok=True)
                raise RuntimeError(
                    f"Downloaded model file failed SHA256 verification: {filename}"
                )

        if not self._cancel_event.is_set():
            completed_total = sum(
                secure_file_size(secure_file_path(directory / filename, must_exist=True))
                for filename in files
            )
            if completed_total > _MAX_MODEL_TOTAL_BYTES:
                raise RuntimeError(
                    "Model download exceeds the maximum allowed total size"
                )
            progress_total = max(total_total, completed_total)
            self._set_state(DownloadState.COMPLETED)
            with self._lock:
                self._progress.total_bytes = completed_total
                self._progress.total_total = progress_total
                self._progress.file_bytes = (
                    secure_file_size(directory / files[-1]) if files else 0
                )
                self._progress.file_total = (
                    file_sizes[-1] or self._progress.file_bytes if file_sizes else 0
                )
            self._emit(force=True)
            logger.info(
                "Model download complete: %s (via %s)", self._model_id, base_url
            )

    def _download_file_with_fallback(
        self,
        *,
        base_urls: list[str],
        filename: str,
        dest: pathlib.Path,
        file_index: int,
        file_count: int,
        expected_size: int,
        total_bytes_so_far: int,
        total_total: int,
    ) -> int:
        last_error: BaseException | None = None
        for mirror_index, base_url in enumerate(base_urls):
            if self._cancel_event.is_set() and self._is_user_cancelled():
                return total_bytes_so_far
            if not self._pause_event.is_set():
                self._pause_event.wait()
            if mirror_index:
                self._clear_partial_files(dest)
            try:
                with self._lock:
                    self._progress.mirror = base_url
                return self._download_file(
                    base_url=base_url,
                    filename=filename,
                    dest=dest,
                    file_index=file_index,
                    file_count=file_count,
                    expected_size=expected_size,
                    total_bytes_so_far=total_bytes_so_far,
                    total_total=total_total,
                )
            except Exception as exc:
                if self._is_user_cancelled():
                    return total_bytes_so_far
                last_error = exc
                self._cancel_event.clear()
                self._pause_event.set()
                logger.warning(
                    "Download from %s failed for %s; trying next mirror",
                    base_url,
                    filename,
                    exc_info=True,
                )
        if last_error is not None:
            raise last_error
        return total_bytes_so_far

    @staticmethod
    def _single_partial_path(dest: pathlib.Path) -> pathlib.Path:
        return secure_file_path(dest.with_name(f"{dest.name}.part"))

    @staticmethod
    def _parallel_part_paths(dest: pathlib.Path) -> list[pathlib.Path]:
        return [
            secure_file_path(dest.with_suffix(dest.suffix + f".part{index}"))
            for index in range(_PARALLEL_MAX_PARTS)
        ]

    def _clear_parallel_parts(self, dest: pathlib.Path) -> None:
        for part_path in self._parallel_part_paths(dest):
            secure_unlink(part_path, missing_ok=True)

    def _clear_partial_files(self, dest: pathlib.Path) -> None:
        secure_unlink(self._single_partial_path(dest), missing_ok=True)
        self._clear_parallel_parts(dest)

    def _head_size(self, base_url: str, filename: str) -> int:
        url = _repo_url(base_url, self._model_id, filename)
        try:
            session = self._session
            if session is None:
                raise RuntimeError("Model downloader session is not initialized")
            with open_validated_requests_response(
                url,
                url_validator=_is_safe_model_download_url,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                label=filename,
                stream=True,
                max_redirects=5,
                request_get=session.head,
            ) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Unexpected HTTP status while probing {filename}: "
                        f"{response.status_code}"
                    )
                size = _response_content_length(response, label=filename) or 0
                if size > _MAX_MODEL_FILE_BYTES:
                    raise RuntimeError(f"Remote model file is oversized: {filename}")
                return size
        except RuntimeError:
            raise
        except Exception:
            return 0

    def _download_file(
        self,
        *,
        base_url: str,
        filename: str,
        dest: pathlib.Path,
        file_index: int,
        file_count: int,
        expected_size: int,
        total_bytes_so_far: int,
        total_total: int,
    ) -> int:
        # Use parallel range download for large files when total size known.
        if expected_size >= _PARALLEL_THRESHOLD_BYTES:
            try:
                return self._download_file_parallel(
                    base_url=base_url,
                    filename=filename,
                    dest=dest,
                    file_index=file_index,
                    file_count=file_count,
                    expected_size=expected_size,
                    total_bytes_so_far=total_bytes_so_far,
                    total_total=total_total,
                )
            except _RangeNotSupported:
                if not self._is_user_cancelled():
                    self._cancel_event.clear()
                    self._pause_event.set()
                    self._clear_parallel_parts(dest)
                logger.info(
                    "Server refused Range; falling back to single stream for %s",
                    filename,
                )

        if expected_size < _PARALLEL_THRESHOLD_BYTES:
            self._clear_parallel_parts(dest)
        return self._download_file_single(
            base_url=base_url,
            filename=filename,
            dest=dest,
            file_index=file_index,
            file_count=file_count,
            expected_size=expected_size,
            total_bytes_so_far=total_bytes_so_far,
            total_total=total_total,
        )

    def _download_file_single(
        self,
        *,
        base_url: str,
        filename: str,
        dest: pathlib.Path,
        file_index: int,
        file_count: int,
        expected_size: int,
        total_bytes_so_far: int,
        total_total: int,
    ) -> int:
        url = _repo_url(base_url, self._model_id, filename)
        remaining_budget = _MAX_MODEL_TOTAL_BYTES - total_bytes_so_far
        file_limit = min(_MAX_MODEL_FILE_BYTES, remaining_budget)
        if file_limit <= 0:
            raise RuntimeError("Model download exceeds the maximum allowed total size")
        partial = self._single_partial_path(dest)
        resume_from = secure_file_size(partial, missing_ok=True)
        if resume_from > file_limit:
            raise RuntimeError(f"Partial model file is oversized: {filename}")
        if expected_size > 0:
            if expected_size > file_limit:
                raise RuntimeError(f"Remote model file is oversized: {filename}")
            if resume_from > expected_size:
                raise RuntimeError(f"Partial model file exceeds expected size: {filename}")
            if resume_from == expected_size:
                expected_sha256 = _HF_MODEL_FILE_SHA256.get(self._model_id, {}).get(filename)
                if not expected_sha256 or not _secure_file_matches_sha256(
                    partial,
                    expected_sha256,
                ):
                    secure_unlink(partial, missing_ok=True)
                    raise RuntimeError(
                        f"Partial model file failed SHA256 verification: {filename}"
                    )
                atomic_replace_secure_file(
                    partial,
                    dest,
                    expected_size=expected_size,
                )
                return total_bytes_so_far + expected_size

        headers: dict[str, str] = {}
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"
            logger.info("Resuming %s from byte %d", filename, resume_from)

        session = self._session
        if session is None:
            raise RuntimeError("Model downloader session is not initialized")
        response_context = open_validated_requests_response(
            url,
            url_validator=_is_safe_model_download_url,
            label=filename,
            headers=headers,
            stream=True,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            max_redirects=5,
            request_get=session.get,
        )
        response = response_context.__enter__()
        try:
            response.raise_for_status()
            if resume_from > 0 and response.status_code == 200:
                # The server ignored Range. Restart the private partial file and
                # consume this full response from byte zero.
                secure_unlink(partial)
                resume_from = 0
            elif resume_from > 0:
                if response.status_code != 206:
                    raise RuntimeError(
                        f"Unexpected HTTP status while resuming {filename}: "
                        f"{response.status_code}"
                    )
                content_length = _response_content_length(response, label=filename)
                if content_length is None or content_length <= 0:
                    raise RuntimeError(
                        f"Resume response omitted Content-Length for {filename}"
                    )
                response_total = _validate_content_range(
                    response,
                    requested_start=resume_from,
                    requested_end=resume_from + content_length - 1,
                    expected_total=expected_size or None,
                    label=filename,
                )
                if response_total > file_limit:
                    raise RuntimeError(f"Remote model file is oversized: {filename}")
                if expected_size <= 0:
                    expected_size = response_total
            elif response.status_code != 200:
                raise RuntimeError(
                    f"Unexpected HTTP status while downloading {filename}: "
                    f"{response.status_code}"
                )

            content_length = _response_content_length(response, label=filename)
            if response.status_code == 200 and content_length is not None:
                if content_length <= 0:
                    raise RuntimeError(f"Remote model file was empty: {filename}")
                if content_length > file_limit:
                    raise RuntimeError(f"Remote model file is oversized: {filename}")
                if expected_size > 0 and content_length != expected_size:
                    raise RuntimeError(
                        f"Remote model file size changed for {filename}: "
                        f"{content_length} != {expected_size}"
                    )
                if expected_size <= 0:
                    expected_size = content_length

            file_bytes = resume_from
            total_bytes = total_bytes_so_far + resume_from
            with open_secure_append(partial, binary=True) as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    self._pause_event.wait()
                    if self._cancel_event.is_set():
                        handle.flush()
                        os.fsync(handle.fileno())
                        return total_bytes
                    if not chunk:
                        continue
                    next_size = file_bytes + len(chunk)
                    size_limit = expected_size or file_limit
                    if next_size > size_limit:
                        raise RuntimeError(f"Remote model file exceeded its allowed size: {filename}")
                    handle.write(chunk)
                    file_bytes = next_size
                    total_bytes += len(chunk)
                    self._update_progress(
                        file_name=filename,
                        file_index=file_index,
                        file_count=file_count,
                        file_bytes=file_bytes,
                        file_total=expected_size,
                        total_bytes=total_bytes,
                        total_total=max(total_total, total_bytes),
                    )
                handle.flush()
                os.fsync(handle.fileno())

            if file_bytes <= 0:
                raise RuntimeError(f"Remote model file was empty: {filename}")
            if expected_size > 0 and file_bytes != expected_size:
                raise RuntimeError(
                    f"Remote model file was truncated for {filename}: "
                    f"{file_bytes} != {expected_size}"
                )
            expected_sha256 = _HF_MODEL_FILE_SHA256.get(self._model_id, {}).get(filename)
            if not expected_sha256 or not _secure_file_matches_sha256(
                partial,
                expected_sha256,
            ):
                secure_unlink(partial, missing_ok=True)
                raise RuntimeError(
                    f"Downloaded model file failed SHA256 verification: {filename}"
                )
            atomic_replace_secure_file(
                partial,
                dest,
                expected_size=file_bytes,
            )
            self._clear_parallel_parts(dest)
            return total_bytes_so_far + file_bytes
        finally:
            response_context.__exit__(None, None, None)

    # Parallel-range path

    def _plan_segments(self, expected_size: int) -> list[tuple[int, int]]:
        """Split [0, expected_size) into N inclusive byte ranges."""
        n = max(
            _PARALLEL_MIN_PARTS,
            min(
                _PARALLEL_MAX_PARTS,
                (expected_size + _PARALLEL_PART_TARGET - 1) // _PARALLEL_PART_TARGET,
            ),
        )
        seg_size = expected_size // n
        ranges: list[tuple[int, int]] = []
        for i in range(n):
            start = i * seg_size
            end = (start + seg_size - 1) if i < n - 1 else (expected_size - 1)
            ranges.append((start, end))
        return ranges

    def _download_file_parallel(
        self,
        *,
        base_url: str,
        filename: str,
        dest: pathlib.Path,
        file_index: int,
        file_count: int,
        expected_size: int,
        total_bytes_so_far: int,
        total_total: int,
    ) -> int:
        file_limit = min(
            _MAX_MODEL_FILE_BYTES,
            _MAX_MODEL_TOTAL_BYTES - total_bytes_so_far,
        )
        if expected_size <= 0 or expected_size > file_limit:
            raise RuntimeError(f"Invalid expected model size for {filename}")
        url = _repo_url(base_url, self._model_id, filename)
        ranges = self._plan_segments(expected_size)
        n_parts = len(ranges)
        logger.info(
            "Parallel download: %s in %d parts (%.0f MB each, ~%.0f MB total)",
            filename,
            n_parts,
            expected_size / n_parts / 1_048_576,
            expected_size / 1_048_576,
        )

        if not self._probe_range(url, ranges[0], expected_size):
            raise _RangeNotSupported()

        part_paths = self._parallel_part_paths(dest)[:n_parts]
        part_sizes: list[int] = []
        for part, (start, end) in zip(part_paths, ranges):
            size = secure_file_size(part, missing_ok=True)
            expected_part_size = end - start + 1
            if size > expected_part_size:
                raise RuntimeError(
                    f"Parallel part exceeds expected size: {part.name} "
                    f"({size} > {expected_part_size})"
                )
            part_sizes.append(size)

        baseline = sum(part_sizes)
        progress_lock = threading.Lock()
        bytes_done = total_bytes_so_far + baseline
        file_bytes = baseline
        emit_state = {"last_emit": 0.0}

        def _on_chunk(n: int) -> None:
            nonlocal bytes_done, file_bytes
            with progress_lock:
                bytes_done += n
                file_bytes += n
                now = time.monotonic()
                if (now - emit_state["last_emit"]) < _UI_EMIT_MIN_S:
                    return
                emit_state["last_emit"] = now
                snapshot = (file_bytes, bytes_done)
            self._update_progress(
                file_name=filename,
                file_index=file_index,
                file_count=file_count,
                file_bytes=snapshot[0],
                file_total=expected_size,
                total_bytes=snapshot[1],
                total_total=total_total,
            )

        worker_error: list[BaseException] = []
        worker_lock = threading.Lock()

        def _worker(idx: int, span: tuple[int, int]) -> None:
            if self._cancel_event.is_set():
                return
            try:
                self._download_segment(
                    url=url,
                    part_path=part_paths[idx],
                    span=span,
                    total_size=expected_size,
                    on_chunk=_on_chunk,
                )
            except BaseException as exc:  # noqa: BLE001 - propagate to driver
                with worker_lock:
                    worker_error.append(exc)
                self._cancel_event.set()
                self._pause_event.set()

        with ThreadPoolExecutor(
            max_workers=n_parts, thread_name_prefix="hf-range"
        ) as pool:
            futures = [
                pool.submit(_worker, i, span) for i, span in enumerate(ranges)
            ]
            for _ in as_completed(futures):
                pass

        if worker_error:
            raise worker_error[0]
        if self._cancel_event.is_set():
            return total_bytes_so_far + sum(
                secure_file_size(part, missing_ok=True) for part in part_paths
            )

        for part, (start, end) in zip(part_paths, ranges):
            expected_part_size = end - start + 1
            actual_size = secure_file_size(part)
            if actual_size != expected_part_size:
                raise RuntimeError(
                    f"Parallel download part is incomplete: {part.name} "
                    f"({actual_size} != {expected_part_size})"
                )

        staging_path: pathlib.Path | None = None
        staging_fd = -1
        published = False
        try:
            staging_fd, staging_name = tempfile.mkstemp(
                prefix=f".{dest.name}.",
                suffix=".merge.tmp",
                dir=dest.parent,
            )
            staging_path = pathlib.Path(staging_name)
            try:
                os.chmod(staging_path, 0o600)
            except OSError:
                pass

            merged_size = 0
            with os.fdopen(staging_fd, "wb", closefd=True) as output:
                staging_fd = -1
                for part in part_paths:
                    with open_secure_read(part, binary=True) as source:
                        while True:
                            chunk = source.read(8 * 1024 * 1024)
                            if not chunk:
                                break
                            merged_size += len(chunk)
                            if merged_size > expected_size:
                                raise RuntimeError(
                                    f"Merged model file exceeded expected size: {filename}"
                                )
                            output.write(chunk)
                output.flush()
                os.fsync(output.fileno())

            if merged_size != expected_size:
                raise RuntimeError(
                    f"Merged model file size mismatch for {filename}: "
                    f"{merged_size} != {expected_size}"
                )
            expected_sha256 = _HF_MODEL_FILE_SHA256.get(self._model_id, {}).get(filename)
            if not expected_sha256 or not _secure_file_matches_sha256(
                staging_path,
                expected_sha256,
            ):
                raise RuntimeError(
                    f"Merged model file failed SHA256 verification: {filename}"
                )
            atomic_replace_secure_file(
                staging_path,
                dest,
                expected_size=expected_size,
            )
            published = True
        finally:
            if staging_fd >= 0:
                os.close(staging_fd)
            if not published and staging_path is not None:
                try:
                    secure_unlink(staging_path, missing_ok=True)
                except (OSError, RuntimeError):
                    logger.debug(
                        "Could not remove merge staging file %s",
                        staging_path,
                        exc_info=True,
                    )

        for part in part_paths:
            try:
                secure_unlink(part)
            except (FileNotFoundError, OSError, RuntimeError):
                logger.debug("Could not remove part file %s", part, exc_info=True)
        secure_unlink(self._single_partial_path(dest), missing_ok=True)

        self._update_progress(
            file_name=filename,
            file_index=file_index,
            file_count=file_count,
            file_bytes=expected_size,
            file_total=expected_size,
            total_bytes=total_bytes_so_far + expected_size,
            total_total=total_total,
        )
        return total_bytes_so_far + expected_size

    def _is_user_cancelled(self) -> bool:
        with self._lock:
            return self._progress.state == DownloadState.CANCELLED

    def _probe_range(
        self,
        url: str,
        span: tuple[int, int],
        expected_total: int,
    ) -> bool:
        """Check whether the server honours an exact bounded Range request."""
        session = self._session
        if session is None:
            raise RuntimeError("Model downloader session is not initialized")
        start, end = span
        probe_end = min(start + 1023, end)
        try:
            with open_validated_requests_response(
                url,
                url_validator=_is_safe_model_download_url,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                label="range probe",
                headers={"Range": f"bytes={start}-{probe_end}"},
                stream=True,
                max_redirects=5,
                request_get=session.get,
            ) as response:
                if response.status_code != 206:
                    return False
                _validate_content_range(
                    response,
                    requested_start=start,
                    requested_end=probe_end,
                    expected_total=expected_total,
                    label="range probe",
                )
                return True
        except Exception:
            logger.debug("Range probe failed for %s", url, exc_info=True)
            return False

    def _download_segment(
        self,
        *,
        url: str,
        part_path: pathlib.Path,
        span: tuple[int, int],
        total_size: int,
        on_chunk: Callable[[int], None],
    ) -> None:
        """Download one exact Range segment to a validated resumable part file."""
        start, end = span
        part_path = secure_file_path(part_path)
        segment_size = end - start + 1
        existing = secure_file_size(part_path, missing_ok=True)
        if existing > segment_size:
            raise RuntimeError(
                f"Parallel part exceeds expected size: {part_path.name}"
            )
        if existing == segment_size:
            return

        segment_start = start + existing
        session = self._session
        if session is None:
            raise RuntimeError("Model downloader session is not initialized")
        response_context = open_validated_requests_response(
            url,
            url_validator=_is_safe_model_download_url,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            label=part_path.name,
            headers={"Range": f"bytes={segment_start}-{end}"},
            stream=True,
            max_redirects=5,
            request_get=session.get,
        )
        response = response_context.__enter__()
        try:
            response.raise_for_status()
            if response.status_code != 206:
                raise _RangeNotSupported()
            _validate_content_range(
                response,
                requested_start=segment_start,
                requested_end=end,
                expected_total=total_size,
                label=part_path.name,
            )

            written = existing
            with open_secure_append(part_path, binary=True) as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    self._pause_event.wait()
                    if self._cancel_event.is_set():
                        handle.flush()
                        os.fsync(handle.fileno())
                        return
                    if not chunk:
                        continue
                    next_size = written + len(chunk)
                    if next_size > segment_size:
                        raise RuntimeError(
                            f"Range response exceeded the requested segment: "
                            f"{part_path.name}"
                        )
                    handle.write(chunk)
                    written = next_size
                    on_chunk(len(chunk))
                handle.flush()
                os.fsync(handle.fileno())

            if written != segment_size:
                raise RuntimeError(
                    f"Range response was truncated for {part_path.name}: "
                    f"{written} != {segment_size}"
                )
        finally:
            response_context.__exit__(None, None, None)


class _RangeNotSupported(Exception):
    """Mirror returned 200 instead of 206 — fall back to single stream."""


# ── Module-level singleton per model_id ─────────────────────────────────────

_downloaders: weakref.WeakValueDictionary[str, HFModelDownloader] = (
    weakref.WeakValueDictionary()
)
_downloaders_lock = threading.Lock()


def get_downloader(model_id: str) -> HFModelDownloader:
    """Return (or create) the shared downloader for this model."""
    with _downloaders_lock:
        downloader = _downloaders.get(model_id)
        if downloader is None:
            downloader = HFModelDownloader(model_id)
            _downloaders[model_id] = downloader
        return downloader
