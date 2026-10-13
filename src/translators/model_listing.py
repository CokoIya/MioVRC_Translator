"""Read a translation provider's model list from its own API.

AI translation backends no longer rely on a hard-coded model table: the
settings page asks the provider (or the player's relay station) which models
the key can use and offers those. This module turns the raw ``/models``
payload into a short, translation-usable list:

* parse the many payload shapes in the wild (OpenAI ``data[]``, Anthropic
  pagination, Gemini ``models/`` prefixes, bare arrays from small relays);
* drop entries that cannot do text chat (embeddings, image/video/audio
  generation, speech, moderation, rerankers ...);
* drop ids the provider has retired, ids whose lifecycle fields say they are
  being shut down, models whose reasoning cannot be turned down, and ids the
  app deliberately does not offer (DeepSeek pro models);
* hide a dated snapshot when its undated alias is listed too;
* order the result with the built-in recommendations first.

TTS and ASR models are not handled here; they stay fixed built-in lists.
Fetching with the player's credentials lives in ``model_list_service`` so the
translators can use the parsing helpers here without an import cycle.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.utils.model_family import model_family_id
from src.utils.model_retirement import (
    ANTHROPIC_MODEL_ID_PREFIXES,
    is_retired_translation_model,
)
from src.utils.ui_config import (
    RELAY_BACKEND,
    RELAY_PROTOCOL_ANTHROPIC,
    RELAY_PROTOCOL_OPENAI,
    TRANSLATION_MODEL_PRESETS,
    get_backend_spec,
    get_backend_value,
    normalize_backend,
    normalize_relay_protocol,
    relay_protocol_default,
)

logger = logging.getLogger(__name__)

# Most entries the selector offers (applied after filtering).
MAX_LISTED_MODELS = 500
# Most raw entries read from one answer. Aggregators list thousands of
# models, most of them filtered out, so this is far above MAX_LISTED_MODELS.
MAX_PARSED_MODEL_ENTRIES = 20_000
MAX_MODEL_ID_CHARS = 200
MAX_MODEL_LIST_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_MODEL_LIST_TIMEOUT_S = 12.0

# Markers of a model that cannot serve a text chat request. Applied to every
# provider and to relays, matched against the lower-cased id.
#
# Long, distinctive markers are matched as substrings.
_NON_CHAT_SUBSTRINGS: tuple[str, ...] = (
    "embed",
    "rerank",
    "moderation",
    "whisper",
    "transcribe",
    "transcription",
    "text-to-speech",
    "realtime",
    "dall-e",
    "gpt-image",
    "image-generation",
    "stable-diffusion",
    "computer-use",
    "native-audio",
    "nv-embed",
    "paraformer",
    "cosyvoice",
    "sambert",
    "wanx",
    "deep-research",
)
# Short markers are matched as whole id segments (split on - _ . / : @) so
# that, for example, "inkling" is not mistaken for the "kling" video model.
_NON_CHAT_SEGMENTS: frozenset[str] = frozenset(
    {
        "tts",
        "stt",
        "asr",
        "speech",
        "audio",
        "voice",
        "image",
        "images",
        "imagen",
        "imagine",
        "veo",
        "sora",
        "video",
        "midjourney",
        "suno",
        "flux",
        "sdxl",
        "kling",
        "luma",
        "runway",
        "ocr",
        "live",
        "guard",
        "safety",
        "reward",
        "retriever",
        "clip",
        "dalle",
        "t2i",
        "t2v",
        "i2v",
        "s2v",  # MiniMax S2V-01 (subject reference to video)
        "i2i",
        "babbage",
        "davinci",
        "robotics",
        # Embedding families that relays and local servers list by name
        # only (bge-m3, m3e-base, multilingual-e5-large, gte-large,
        # all-minilm, colbert-v2, voyage-3-large).
        "bge",
        "m3e",
        "e5",
        "gte",
        "minilm",
        "colbert",
        "voyage",
        # Music and speech generators.
        "eleven",
        "elevenlabs",
        "music",
        "musicgen",
    }
)
_SEGMENT_SEPARATOR = r"[-_./:@\s]"
_SEGMENT_END = rf"(?=$|{_SEGMENT_SEPARATOR})"
_ID_SEGMENT_SPLIT_RE = re.compile(rf"{_SEGMENT_SEPARATOR}+")
# Markers that must start an id segment (some must also end one). Image,
# video, speech and music generators that relays list next to chat models;
# checked against the live OpenRouter list so no text chat model matches.
_NON_CHAT_SEGMENT_PATTERNS: tuple[str, ...] = (
    r"veo\d",  # veo3, veo3.1-fast
    rf"sd(?:\d(?:\.\d)?|-turbo){_SEGMENT_END}",  # sd3.5-large, stabilityai/sd-turbo
    r"seedream",
    r"seedance",
    r"seededit",
    r"jimeng",
    r"hailuo",
    r"kolors",
    r"cogview",
    r"cogvideo",  # cogvideox-3
    r"vidu",  # vidu2.0, viduq1-classic, viduq2-pro_img2video, vidu-video-q3
    r"ideogram",
    r"recraft",
    r"nano-banana",
    r"lyria",
    r"sensevoice",  # SenseVoiceSmall
    r"fish-speech",
    rf"pika\d*{_SEGMENT_END}",
    rf"chirp\d*{_SEGMENT_END}",
    r"upscal",
    r"swap[-_]?face",
    r"face[-_]?swap",
    r"mj_",  # mj_imagine, mj_blend
    r"wan[-_.]?\d",  # wan2.2-t2v-plus, alibaba/wan-3.0
    r"paraphrase-multilingual",  # Ollama's sentence-embedding model
)
_NON_CHAT_PATTERN_RE = re.compile(
    rf"(?:^|~|{_SEGMENT_SEPARATOR})(?:{'|'.join(_NON_CHAT_SEGMENT_PATTERNS)})"
)

# new-api's supported_endpoint_types values for models that are not chat
# models at all, whatever other endpoint types the channel also reports.
_NON_CHAT_ENDPOINT_TYPES = frozenset(
    {"image-generation", "openai-video", "embeddings", "jina-rerank"}
)
# Endpoint types the app can call: OpenAI chat completions, or Anthropic
# messages. A model listed only for "openai-response" (the Responses API) is
# not callable through chat completions.
_CHAT_ENDPOINT_TYPES_BY_PROTOCOL = {
    RELAY_PROTOCOL_OPENAI: frozenset({"openai", "chat"}),
    RELAY_PROTOCOL_ANTHROPIC: frozenset({"anthropic"}),
}
# Reasoning efforts low enough for live subtitles (OpenRouter's
# ``reasoning.supported_efforts``).
_FAST_REASONING_EFFORTS = frozenset({"none", "minimal", "low"})

# Lifecycle values that mean a model is gone or on its way out.
_RETIRING_STATUS_VALUES = frozenset(
    {
        "deprecated",
        "retired",
        "retiring",
        "offline",
        "pre-offline",
        "pre_offline",
        "shutdown",
        "sunset",
        "disabled",
        "unavailable",
        "archived",
    }
)
_TRUE_TEXT = frozenset({"true", "yes", "1"})
# A model whose announced shutdown is this close (or already past) is hidden:
# picking it now would break the player's setup within weeks.
_RETIREMENT_NOTICE_WINDOW = timedelta(days=30)
_RETIREMENT_DATE_FIELDS = (
    "deprecation",
    "deprecation_date",
    "deprecated_at",
    "shutdown_date",
    "retirement_date",
    "expiration_date",
    "expires_at",
    "sunset_date",
    "retires_at",
    "offline_time",
)
# Values a provider sends in a date field to say "no date announced".
_NO_DATE_TEXT = frozenset({"never", "none", "null", "-1", "0"})
# What a "no date" value parses to: a retirement that never comes.
_NO_RETIREMENT = datetime.max.replace(tzinfo=timezone.utc)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# The last day of year 9999 as Unix seconds: the usual "never expires"
# sentinel (253402300799 = 9999-12-31T23:59:59Z), whatever its unit.
_END_OF_TIME_SECONDS = (253_402_214_400, 253_402_300_800)

# Dated snapshot suffixes that name a full date: -2026-03-05, -20260305,
# @20260305. Every backend collapses these onto their undated alias.
_FULL_DATE = r"(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
_FULL_DATE_SNAPSHOT_RE = re.compile(
    r"^(?P<base>.+?)(?:-(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    rf"|[-@]{_FULL_DATE})$"
)
# Short date suffixes (-0613, -2603, -250828). Only these vendors use them as
# snapshot aliases of an undated id; elsewhere a short number names a newer,
# distinct model (qwen3-235b-a22b-2507, deepseek-r1-0528, kimi-k2-0905).
_SHORT_DATE_SNAPSHOT_RE = re.compile(r"^(?P<base>.+?)-(?:\d{4}|\d{6})$")
_SHORT_SNAPSHOT_BACKENDS = frozenset({"openai", "anthropic", "xai", "mistral"})

# The Models API lists older Claude models by their dated id only
# (claude-haiku-4-5-20251001); the app stores the undated alias.
_ANTHROPIC_DATED_ID_RE = re.compile(rf"^(?P<alias>claude-[a-z0-9-]+?)-{_FULL_DATE}$")
_ANTHROPIC_PREFIX = "claude-"
# Key-like runs in a provider error message (sk-..., bearer tokens).
_OPAQUE_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{24,}")


@dataclass(frozen=True)
class ListedModel:
    """One entry of a provider model list with the fields used for filtering."""

    id: str
    fields: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelListResult:
    """Outcome of one model-list refresh."""

    backend: str
    models: tuple[str, ...]
    source: str  # "remote" or "builtin"
    source_key: str = ""
    error: BaseException | None = None
    hidden_count: int = 0

    @property
    def remote(self) -> bool:
        return self.source == "remote"


def backend_supports_model_listing(backend: str) -> bool:
    """Whether the backend reads its model list from the provider API."""

    spec = get_backend_spec(backend)
    return str(spec.get("model_list", "")).strip().lower() == "remote"


def parse_model_list_payload(payload: object) -> list[ListedModel]:
    """Extract model entries from the JSON body of a model-list endpoint.

    A body that is not JSON (a relay console's HTML page when the Base URL
    lacks ``/v1``) raises ``json.JSONDecodeError``; a JSON error answer
    without a model list raises ``ValueError``.
    """

    if isinstance(payload, (bytes, bytearray)):
        if len(payload) > MAX_MODEL_LIST_RESPONSE_BYTES:
            raise ValueError("Model list response is too large")
        payload = json.loads(bytes(payload).decode("utf-8-sig") or "null")
    elif isinstance(payload, str):
        if len(payload) > MAX_MODEL_LIST_RESPONSE_BYTES:
            raise ValueError("Model list response is too large")
        payload = json.loads(payload or "null")

    entries: object = payload
    if isinstance(payload, Mapping):
        for key in ("data", "models", "items", "result"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                entries = candidate
                break
            if isinstance(candidate, Mapping):
                nested = candidate.get("data") or candidate.get("models")
                if isinstance(nested, list):
                    entries = nested
                    break
        else:
            _raise_for_error_payload(payload)
            entries = []
    if not isinstance(entries, list):
        raise ValueError("Model list response has an unexpected shape")

    models: list[ListedModel] = []
    seen: set[str] = set()
    for entry in entries:
        if isinstance(entry, str):
            raw_id, fields = entry, {}
        elif isinstance(entry, Mapping):
            raw_id = entry.get("id") or entry.get("model") or entry.get("name") or ""
            fields = dict(entry)
        else:
            continue
        model_id = normalize_listed_model_id(raw_id)
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append(ListedModel(id=model_id, fields=fields))
        if len(models) >= MAX_PARSED_MODEL_ENTRIES:
            break
    return models


def _raise_for_error_payload(payload: Mapping[str, object]) -> None:
    """Report a JSON error answer instead of an empty list.

    Relays sometimes answer HTTP 200 with ``{"success": false, "message":
    ...}`` (new-api) or ``{"error": {...}}``; treating that as "no models"
    would hide the real reason (a bad key or token group).
    """

    error = payload.get("error")
    if payload.get("success") is not False and not isinstance(error, (Mapping, str)):
        return
    detail: object = payload.get("message") or ""
    if isinstance(error, Mapping):
        detail = error.get("message") or error.get("type") or error.get("code") or detail
    elif isinstance(error, str):
        detail = error or detail
    # The provider's words let the error formatter tell a bad key from an
    # exhausted quota; long opaque tokens (an echoed key) are masked.
    text = _OPAQUE_TOKEN_RE.sub("***", " ".join(str(detail or "").split()))[:200]
    message = "Model list request was rejected by the provider"
    raise ValueError(f"{message}: {text}" if text else message)


def normalize_listed_model_id(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("models/"):
        # Gemini (native and OpenAI-compatible) names models "models/<id>".
        text = text[len("models/") :]
    if not text or len(text) > MAX_MODEL_ID_CHARS:
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        return ""
    return text


def _is_non_chat_model(model_id: str) -> bool:
    lowered = model_id.casefold()
    if any(token in lowered for token in _NON_CHAT_SUBSTRINGS):
        return True
    segments = {segment for segment in _ID_SEGMENT_SPLIT_RE.split(lowered) if segment}
    if segments & _NON_CHAT_SEGMENTS:
        return True
    return _NON_CHAT_PATTERN_RE.search(lowered) is not None


def _timestamp_date(number: float) -> datetime:
    """A Unix timestamp in s, ms, us or ns (picked by size) as UTC."""

    if math.isnan(number) or number <= 0:
        return _NO_RETIREMENT
    for scale in (1.0, 1e3, 1e6, 1e9):
        if _END_OF_TIME_SECONDS[0] <= number / scale < _END_OF_TIME_SECONDS[1]:
            return _NO_RETIREMENT
    if number < 1e11:
        seconds = number
    elif number < 1e14:
        seconds = number / 1e3
    elif number < 1e17:
        seconds = number / 1e6
    else:
        seconds = number / 1e9
    try:
        return _EPOCH + timedelta(seconds=seconds)
    except OverflowError:
        # Past year 9999: no real retirement date.
        return _NO_RETIREMENT


def _parse_lifecycle_date(value: object) -> datetime | None:
    """Parse a lifecycle date into UTC.

    Accepts an ISO date/datetime or a Unix timestamp. Values that mean "no
    date" (0, negative numbers, ``never``/``none``/``null``/``-1``/``0``, the
    year-9999 sentinel or anything later) parse as ``datetime.max``. Returns
    ``None`` only for a value that cannot be read at all.
    """

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return _timestamp_date(float(value))
    text = str(value).strip()
    if not text or text.casefold() in _NO_DATE_TEXT:
        return _NO_RETIREMENT
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return _timestamp_date(float(text))
    if re.match(r"^\+?\d{5,}-", text):
        # An ISO date past year 9999.
        return _NO_RETIREMENT
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10].replace("/", "-"), "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _field_says_retiring(
    fields: Mapping[str, object],
    *,
    now: datetime | None = None,
    _nested: bool = False,
) -> bool:
    """Whether lifecycle fields say the model is retired or about to be."""

    for key in ("deprecated", "archived"):
        flag = fields.get(key)
        if flag is True or (isinstance(flag, str) and flag.strip().casefold() in _TRUE_TEXT):
            return True
    for key in ("status", "lifecycle", "lifecycle_status", "state"):
        value = fields.get(key)
        if isinstance(value, str) and value.strip().casefold() in _RETIRING_STATUS_VALUES:
            return True
    current = now or datetime.now(timezone.utc)
    lifecycle = fields.get("lifecycle")
    if not _nested and isinstance(lifecycle, Mapping):
        # {"lifecycle": {"status": "deprecated", "retires_at": ...}}
        if _field_says_retiring(lifecycle, now=current, _nested=True):
            return True
    candidates: list[object] = [fields.get(key) for key in _RETIREMENT_DATE_FIELDS]
    offline_info = fields.get("inference_offline_info")
    if isinstance(offline_info, Mapping):
        # DashScope: a non-null offline_time means the model is retiring.
        candidates.append(offline_info.get("offline_time"))
    for value in candidates:
        if value in (None, "", False):
            continue
        if isinstance(value, Mapping):
            # Some providers nest the date ({"date": ..., "replacement": ...}).
            value = value.get("date") or value.get("time") or value.get("at")
            if value in (None, ""):
                continue
        when = _parse_lifecycle_date(value)
        if when is None or when <= current + _RETIREMENT_NOTICE_WINDOW:
            # An unparseable announcement still announces a retirement.
            return True
    return False


def _effective_protocol(backend: str, protocol: object) -> str:
    """Wire protocol the backend's translator speaks."""

    if backend == RELAY_BACKEND:
        return normalize_relay_protocol(protocol)
    if backend == "anthropic":
        return RELAY_PROTOCOL_ANTHROPIC
    return RELAY_PROTOCOL_OPENAI


def _field_says_not_chat(
    fields: Mapping[str, object],
    *,
    protocol: str = RELAY_PROTOCOL_OPENAI,
) -> bool:
    """Use structured capability fields when a provider sends them."""

    capabilities = fields.get("capabilities")
    if isinstance(capabilities, Mapping):
        # Mistral: {"completion_chat": bool, ...}
        if "completion_chat" in capabilities and not capabilities.get("completion_chat"):
            return True
    output_modalities = None
    architecture = fields.get("architecture")
    if isinstance(architecture, Mapping):
        output_modalities = architecture.get("output_modalities")
    if output_modalities is None:
        output_modalities = fields.get("output_modalities")
    if isinstance(output_modalities, list) and output_modalities:
        lowered = {str(item).casefold() for item in output_modalities}
        if "text" not in lowered:
            return True
    endpoint_types = fields.get("supported_endpoint_types")
    if isinstance(endpoint_types, (list, tuple)) and endpoint_types:
        # new-api: the endpoints a model can be called on. Image models also
        # report "openai", so a non-chat type wins over everything else.
        lowered = {str(item).strip().casefold() for item in endpoint_types}
        if lowered & _NON_CHAT_ENDPOINT_TYPES:
            return True
        required = _CHAT_ENDPOINT_TYPES_BY_PROTOCOL.get(
            protocol, _CHAT_ENDPOINT_TYPES_BY_PROTOCOL[RELAY_PROTOCOL_OPENAI]
        )
        if not lowered & required:
            return True
    methods = fields.get("supportedGenerationMethods")
    if isinstance(methods, list) and methods:
        if "generateContent" not in {str(item) for item in methods}:
            return True
    return False


def _reasoning_cannot_be_lowered(fields: Mapping[str, object]) -> bool:
    """OpenRouter: reasoning is mandatory and offers no low effort.

    Such models (o3-mini-high, gpt-5-pro, minimax-m2, *-thinking) think at
    length on every request and cannot be made fast enough for subtitles.
    """

    reasoning = fields.get("reasoning")
    if not isinstance(reasoning, Mapping) or reasoning.get("mandatory") is not True:
        return False
    efforts = reasoning.get("supported_efforts")
    if isinstance(efforts, str):
        efforts = [efforts]
    if not isinstance(efforts, (list, tuple)):
        efforts = []
    offered = {str(effort).strip().casefold() for effort in efforts}
    return not offered & _FAST_REASONING_EFFORTS


@dataclass(frozen=True)
class _ListRules:
    """Per-backend filter rules taken from the backend spec."""

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    exclude_patterns: tuple[re.Pattern[str], ...] = ()
    require: tuple[str, ...] = ()
    merge_builtin: bool = False
    priority: tuple[str, ...] = ()


def _spec_strings(spec: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = spec.get(key, ()) or ()
    if isinstance(value, str):
        value = (value,)
    return tuple(str(item).casefold() for item in value if str(item).strip())


def _provider_rules(backend: str, protocol: str = "") -> _ListRules:
    """Collect the filter rules a backend spec declares.

    ``protocol`` is accepted for callers that pass it; the relay applies the
    same rules on both protocols (an Anthropic-protocol relay may serve
    non-Claude models over /v1/messages, so nothing is restricted to
    ``claude-`` ids).
    """

    del protocol
    spec = get_backend_spec(backend)
    patterns: list[re.Pattern[str]] = []
    for raw_pattern in spec.get("model_list_exclude_patterns", ()) or ():
        try:
            patterns.append(re.compile(str(raw_pattern), re.IGNORECASE))
        except re.error:
            logger.warning("Ignoring an invalid model-list pattern for %s", backend)
    priority = tuple(
        str(item).strip() for item in spec.get("model_list_priority", ()) or () if str(item).strip()
    )
    return _ListRules(
        include=_spec_strings(spec, "model_list_include"),
        exclude=_spec_strings(spec, "model_list_exclude"),
        exclude_patterns=tuple(patterns),
        require=_spec_strings(spec, "model_list_require"),
        merge_builtin=bool(spec.get("model_list_merge_builtin", False)),
        priority=priority,
    )


def _rejected_by_rules(model_id: str, rules: _ListRules, *, backend: str) -> bool:
    lowered = model_id.casefold()
    # NVIDIA ids are "provider/model"; relays may namespace ids the same way.
    # Prefix rules look at the bare model name for relays only.
    prefix_target = model_family_id(lowered) if backend == RELAY_BACKEND else lowered
    if rules.include and not prefix_target.startswith(rules.include):
        return True
    if rules.require and not any(token in lowered for token in rules.require):
        return True
    if any(token in lowered for token in rules.exclude):
        return True
    return any(pattern.search(model_id) for pattern in rules.exclude_patterns)


def _anthropic_alias(model_id: str) -> str:
    """Map a dated Claude id to the undated alias the app stores.

    Only for an alias of a Claude family the translator drives; anything
    else keeps its dated id (and the retirement table then decides).
    """

    match = _ANTHROPIC_DATED_ID_RE.match(model_id.casefold())
    if match is None:
        return model_id
    alias = match.group("alias")
    return alias if alias.startswith(ANTHROPIC_MODEL_ID_PREFIXES) else model_id


def _recommended_models(backend: str, protocol: str) -> tuple[str, ...]:
    """The backend's default model followed by its built-in presets."""

    if backend == RELAY_BACKEND:
        default_model = relay_protocol_default(protocol, "model").strip()
    else:
        default_model = get_backend_value(backend, "model").strip()
    return (default_model, *TRANSLATION_MODEL_PRESETS.get(backend, ()))


def _drop_dated_duplicates(
    models: list[str],
    *,
    backend: str,
    protected: Iterable[str] = (),
) -> tuple[list[str], int]:
    """Hide ``name-2026-03-05`` style snapshots when ``name`` is listed too.

    Recommended ids (``protected``) are never hidden.
    """

    listed = {model.casefold() for model in models}
    keep = {str(model).casefold() for model in protected if str(model).strip()}
    patterns = [_FULL_DATE_SNAPSHOT_RE]
    if backend in _SHORT_SNAPSHOT_BACKENDS:
        patterns.append(_SHORT_DATE_SNAPSHOT_RE)
    kept: list[str] = []
    hidden = 0
    for model in models:
        folded = model.casefold()
        if folded not in keep and any(
            (match := pattern.match(model)) is not None
            and match.group("base").casefold() in listed
            for pattern in patterns
        ):
            hidden += 1
            continue
        kept.append(model)
    return kept, hidden


def filter_listed_models(
    backend: str,
    models: Iterable[ListedModel | str],
    *,
    protocol: object = "",
    now: datetime | None = None,
) -> tuple[tuple[str, ...], int]:
    """Keep translation-usable chat models and order them for the selector.

    Returns ``(model ids, number of listed entries not offered)``. ``now``
    is the reference time for lifecycle dates (tests pass a fixed one).
    """

    normalized_backend = normalize_backend(backend)
    relay_protocol = _effective_protocol(normalized_backend, protocol)
    rules = _provider_rules(normalized_backend)
    current = now or datetime.now(timezone.utc)
    kept: list[str] = []
    seen: set[str] = set()
    hidden = 0
    for entry in models:
        listed = entry if isinstance(entry, ListedModel) else ListedModel(id=str(entry))
        model_id = normalize_listed_model_id(listed.id)
        if not model_id:
            hidden += 1
            continue
        if normalized_backend == "anthropic":
            model_id = _anthropic_alias(model_id)
        if (
            _rejected_by_rules(model_id, rules, backend=normalized_backend)
            or _is_non_chat_model(model_id)
            or _field_says_not_chat(listed.fields, protocol=relay_protocol)
            or _field_says_retiring(listed.fields, now=current)
            or _reasoning_cannot_be_lowered(listed.fields)
            or is_retired_translation_model(normalized_backend, model_id)
        ):
            hidden += 1
            continue
        if model_id in seen:
            # A dated Claude id whose alias is listed too.
            hidden += 1
            continue
        seen.add(model_id)
        kept.append(model_id)
    recommended = _recommended_models(normalized_backend, relay_protocol)
    kept, hidden_snapshots = _drop_dated_duplicates(
        kept, backend=normalized_backend, protected=recommended
    )
    hidden += hidden_snapshots
    if rules.merge_builtin:
        # Some providers' /models omit ids they serve (GLM leaves out its
        # flash models); the built-in recommendations fill the gap.
        for model_id in builtin_model_list(normalized_backend):
            if model_id not in kept and not is_retired_translation_model(
                normalized_backend, model_id
            ):
                kept.append(model_id)
    ordered = _order_models(
        normalized_backend,
        kept,
        priority=rules.priority,
        protocol=relay_protocol,
    )
    if len(ordered) > MAX_LISTED_MODELS:
        hidden += len(ordered) - MAX_LISTED_MODELS
        ordered = ordered[:MAX_LISTED_MODELS]
    return ordered, hidden


def _version_sort_key(model_id: str) -> tuple:
    """Group by family, newest version first within a family.

    The version numbers are padded so a missing component ranks older:
    gpt-5.4 sorts above gpt-5 and claude-sonnet-4-5 above claude-sonnet-4.
    """

    lowered = model_id.casefold()
    match = re.search(r"\d", lowered)
    family = lowered[: match.start()] if match else lowered
    numbers = [int(part) for part in re.findall(r"\d+", lowered)[:4]]
    numbers.extend([0] * (4 - len(numbers)))
    return (family, tuple(-number for number in numbers), lowered)


def _order_models(
    backend: str,
    models: list[str],
    *,
    priority: tuple[str, ...] = (),
    protocol: str = RELAY_PROTOCOL_OPENAI,
) -> tuple[str, ...]:
    """Recommended models first (default, presets, priority list), then the
    rest grouped by family with the newest version first.

    Relay ids are matched by family (``openai/gpt-6-luna`` and
    ``anthropic/claude-haiku-5.5`` rank like ``gpt-6-luna`` and
    ``claude-haiku-5-5``); the listed ids themselves are kept verbatim. A
    relay on the Anthropic protocol lists Claude models first.
    """

    relay = backend == RELAY_BACKEND
    by_folded: dict[str, str] = {}
    by_family: dict[str, list[str]] = {}
    for model in models:
        by_folded.setdefault(model.casefold(), model)
        if relay:
            by_family.setdefault(model_family_id(model), []).append(model)
    preferred: list[str] = []
    for candidate in (*_recommended_models(backend, protocol), *priority):
        text = str(candidate or "").strip()
        if not text:
            continue
        matches = [by_folded.get(text.casefold())]
        if relay:
            matches.extend(by_family.get(model_family_id(text), ()))
        for actual in matches:
            if actual and actual not in preferred:
                preferred.append(actual)
    preferred_set = set(preferred)
    rest = sorted((model for model in models if model not in preferred_set), key=_version_sort_key)
    ordered = preferred + rest
    if relay and protocol == RELAY_PROTOCOL_ANTHROPIC:
        claude = [model for model in ordered if model_family_id(model).startswith(_ANTHROPIC_PREFIX)]
        claude_set = set(claude)
        ordered = claude + [model for model in ordered if model not in claude_set]
    return tuple(ordered)


def builtin_model_list(backend: str, *, protocol: object = "") -> tuple[str, ...]:
    """The built-in recommendations: the default model and the presets.

    ``protocol`` matters for the relay only, whose default model follows its
    wire protocol.
    """

    normalized_backend = normalize_backend(backend)
    default_model, *presets = _recommended_models(
        normalized_backend, _effective_protocol(normalized_backend, protocol)
    )
    models = list(presets)
    if default_model and default_model not in models:
        models.insert(0, default_model)
    return tuple(models)
