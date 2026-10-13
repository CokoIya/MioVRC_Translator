from __future__ import annotations

import copy
import re
from collections.abc import Mapping

QWEN_TTS_BASE_URL_MAINLAND = "https://dashscope.aliyuncs.com/api/v1"
QWEN_TTS_BASE_URL_INTERNATIONAL = "https://dashscope-intl.aliyuncs.com/api/v1"
QWEN_TTS_DEFAULT_REGION = "singapore"
QWEN_TTS_DEFAULT_MODEL = "qwen3-tts-flash"
QWEN_TTS_DEFAULT_VOICE = "Cherry"
QWEN_TTS_REGION_BASE_URLS = {
    "china_mainland": QWEN_TTS_BASE_URL_MAINLAND,
    "singapore": QWEN_TTS_BASE_URL_INTERNATIONAL,
    # Tokyo uses a workspace-specific Model Studio hostname.
    "japan": "",
}
QWEN_TTS_REGION_ALIASES = {
    "china": "china_mainland",
    "cn": "china_mainland",
    "mainland": "china_mainland",
    "china-mainland": "china_mainland",
    "intl": "singapore",
    "international": "singapore",
    "sg": "singapore",
    "jp": "japan",
    "japan": "japan",
}

# Cloud voice cloning: DashScope ``voice-enrollment`` plus a Qwen-Audio-TTS
# synthesis model. Alibaba retired qwen-voice-enrollment and every
# qwen3-tts-vc model on 2026-10-10. The replacement takes the recording as a
# URL, which the app gets from Model Studio's temporary storage, and that
# storage only exists in the China (Beijing) region. Cloning is therefore
# Beijing-only for now: Singapore can enroll voices only from a public URL the
# player would have to host, and Tokyo has no enrollment at all.
QWEN_VC_DEFAULT_REGION = "china_mainland"
QWEN_VC_DEFAULT_MODEL = "qwen-audio-3.0-tts-flash"
QWEN_VC_MODEL_OPTIONS = (
    QWEN_VC_DEFAULT_MODEL,
    "qwen-audio-3.1-tts-flash",
    "qwen-audio-3.0-tts-plus",
)
# Synthesis models a cloned voice can target: the Qwen-Audio-TTS family
# (qwen-audio-3.0-tts-flash, qwen-audio-3.1-tts-flash, qwen-audio-3.0-tts-plus
# and their snapshots). The realtime (WebSocket) and ASR models of the same
# family, CosyVoice and the retired qwen3-tts-vc snapshots cannot speak with a
# voice-enrollment voice through the SpeechSynthesizer route.
QWEN_VC_MODEL_PATTERN = re.compile(r"^qwen-audio-\d+(?:\.\d+)?-tts-", re.IGNORECASE)
QWEN_VC_REGION_BASE_URLS = {
    "china_mainland": QWEN_TTS_BASE_URL_MAINLAND,
}
QWEN_VC_REGION_ALIASES = {
    "china": "china_mainland",
    "cn": "china_mainland",
    "mainland": "china_mainland",
    "beijing": "china_mainland",
    # Singapore and Tokyo keys cannot clone through temporary storage. Resolve
    # to Beijing so the endpoint is never blank; the settings page explains
    # that a Beijing (China mainland) key is required.
    "singapore": "china_mainland",
    "intl": "china_mainland",
    "international": "china_mainland",
    "sg": "china_mainland",
    "jp": "china_mainland",
    "japan": "china_mainland",
}
# Endpoints older releases stored for cloning. They are replaced by the
# Beijing endpoint instead of being kept as a "custom" address.
QWEN_VC_LEGACY_BASE_URLS = frozenset({QWEN_TTS_BASE_URL_INTERNATIONAL})
# Regions where the app can enroll a voice. A key issued for any other region
# authenticates against its own region only, so it cannot be reused here.
VOICE_CLONE_SUPPORTED_REGIONS = frozenset({"china_mainland"})


def qwen_vc_model_is_supported(model: object) -> bool:
    """Whether a synthesis model can speak with a voice-enrollment voice."""

    return bool(QWEN_VC_MODEL_PATTERN.match(str(model or "").strip()))


# Enrolled voice ids are named "{target_model}-{prefix}-{unique id}". The
# account-wide list also returns CosyVoice and Qwen-Audio-Realtime voices, so
# recognise those families too; they are filtered out by target model.
_TARGET_MODEL_FROM_VOICE_RE = re.compile(
    r"^("
    r"qwen-audio-\d+(?:\.\d+)?-(?:tts|realtime)-(?:flash|plus)"
    r"|cosyvoice-v\d+(?:\.\d+)?(?:-(?:flash|plus))?"
    r")-[A-Za-z0-9]"
)


def target_model_from_voice_id(voice_id: object) -> str:
    """The synthesis model a voice id was enrolled for, or "" if unknown."""

    match = _TARGET_MODEL_FROM_VOICE_RE.match(str(voice_id or "").strip())
    return match.group(1) if match else ""


def cloned_voice_survives_retirement(voice: Mapping[str, object]) -> bool:
    """Whether a stored cloned voice was enrolled with the current service.

    Voices enrolled for the retired qwen3-tts-vc models (and records from
    before the target model was stored, which were all qwen3-tts-vc) were
    deleted by Alibaba on 2026-10-10 and can never speak again. Voices of a
    Qwen-Audio-TTS model survive; realtime and CosyVoice voices are not
    something this engine can speak with either.
    """

    target = str(voice.get("target_model") or "").strip()
    if not target:
        target = target_model_from_voice_id(voice.get("voice_id"))
    return qwen_vc_model_is_supported(target)


def region_supports_voice_cloning(region: object) -> bool:
    """Report whether enrollment is offered in a preset-engine region."""

    token = _normalize_region_token(region)
    resolved = QWEN_TTS_REGION_ALIASES.get(token, token)
    return resolved in VOICE_CLONE_SUPPORTED_REGIONS

XIAOMI_TTS_BASE_URL_PAYG = "https://api.xiaomimimo.com/v1"
XIAOMI_TTS_BASE_URL_TOKEN_PLAN_CN = "https://token-plan-cn.xiaomimimo.com/v1"
XIAOMI_TTS_BASE_URL_TOKEN_PLAN_SG = "https://token-plan-sgp.xiaomimimo.com/v1"
XIAOMI_TTS_BASE_URL_TOKEN_PLAN_EU = "https://token-plan-ams.xiaomimimo.com/v1"
XIAOMI_TTS_DEFAULT_REGION = "global"
XIAOMI_TTS_DEFAULT_MODEL = "mimo-v2.5-tts"
XIAOMI_TTS_DEFAULT_VOICE = "mimo_default"
XIAOMI_TTS_REGION_BASE_URLS = {
    "global": XIAOMI_TTS_BASE_URL_PAYG,
    "china_cluster": XIAOMI_TTS_BASE_URL_TOKEN_PLAN_CN,
    "singapore_cluster": XIAOMI_TTS_BASE_URL_TOKEN_PLAN_SG,
    "europe_cluster": XIAOMI_TTS_BASE_URL_TOKEN_PLAN_EU,
}
XIAOMI_TTS_REGION_ALIASES = {
    "api": "global",
    "payg": "global",
    "pay_as_you_go": "global",
    "global_payg": "global",
    "china": "china_cluster",
    "cn": "china_cluster",
    "token_plan_cn": "china_cluster",
    "token_plan_china": "china_cluster",
    "singapore": "singapore_cluster",
    "sg": "singapore_cluster",
    "sgp": "singapore_cluster",
    "token_plan_sg": "singapore_cluster",
    "token_plan_sgp": "singapore_cluster",
    "europe": "europe_cluster",
    "eu": "europe_cluster",
    "ams": "europe_cluster",
    "amsterdam": "europe_cluster",
    "token_plan_eu": "europe_cluster",
    "token_plan_ams": "europe_cluster",
}

TTS_API_ENGINE_IDS = ("mimo_tts", "qwen_tts", "qwen_vc")
TTS_API_REGION_OPTION_KEYS = {
    "mimo_tts": (
        ("xiaomi_region_global", "global"),
        ("xiaomi_region_china_cluster", "china_cluster"),
        ("xiaomi_region_singapore_cluster", "singapore_cluster"),
        ("xiaomi_region_europe_cluster", "europe_cluster"),
        ("xiaomi_region_custom", "custom"),
    ),
    "qwen_tts": (
        ("qwen_region_singapore", "singapore"),
        ("qwen_region_japan", "japan"),
        ("qwen_region_china_mainland", "china_mainland"),
        ("qwen_region_custom", "custom"),
    ),
    "qwen_vc": (
        ("qwen_region_china_mainland", "china_mainland"),
        ("qwen_region_custom", "custom"),
    ),
}
TTS_API_REGION_BASE_URLS = {
    "mimo_tts": XIAOMI_TTS_REGION_BASE_URLS,
    "qwen_tts": QWEN_TTS_REGION_BASE_URLS,
    "qwen_vc": QWEN_VC_REGION_BASE_URLS,
}
TTS_API_REGION_ALIASES = {
    "mimo_tts": XIAOMI_TTS_REGION_ALIASES,
    "qwen_tts": QWEN_TTS_REGION_ALIASES,
    "qwen_vc": QWEN_VC_REGION_ALIASES,
}
TTS_API_DEFAULT_REGIONS = {
    "mimo_tts": XIAOMI_TTS_DEFAULT_REGION,
    "qwen_tts": QWEN_TTS_DEFAULT_REGION,
    "qwen_vc": QWEN_VC_DEFAULT_REGION,
}
TTS_API_MODEL_OPTIONS = {
    "mimo_tts": (XIAOMI_TTS_DEFAULT_MODEL,),
    # qwen3-tts-instruct-flash and every dated qwen3-tts snapshot were retired
    # on 2026-10-10; the stable alias is the only Qwen-TTS model left.
    "qwen_tts": (QWEN_TTS_DEFAULT_MODEL,),
    "qwen_vc": QWEN_VC_MODEL_OPTIONS,
}
# Retired Qwen-TTS synthesis models; saved configs move to the stable alias.
QWEN_TTS_RETIRED_MODEL_PREFIXES = (
    "qwen3-tts-instruct-flash",
    "qwen3-tts-flash-20",
    "qwen-tts",
)
TTS_API_LEGACY_BASE_URLS = {
    "qwen_vc": QWEN_VC_LEGACY_BASE_URLS,
}


def qwen_tts_model_is_retired(model: object) -> bool:
    text = str(model or "").strip().lower()
    return bool(text) and text.startswith(QWEN_TTS_RETIRED_MODEL_PREFIXES)
TTS_API_DEFAULT_CONFIGS = {
    "mimo_tts": {
        "api_key": "",
        "region": XIAOMI_TTS_DEFAULT_REGION,
        "base_url": XIAOMI_TTS_BASE_URL_PAYG,
        "model": XIAOMI_TTS_DEFAULT_MODEL,
        "voice": XIAOMI_TTS_DEFAULT_VOICE,
        "rate": 1.0,
        "volume": 0.8,
        "timeout_seconds": 30,
        "max_retries": 0,
    },
    "qwen_tts": {
        "api_key": "",
        "region": QWEN_TTS_DEFAULT_REGION,
        "base_url": QWEN_TTS_BASE_URL_INTERNATIONAL,
        "model": QWEN_TTS_DEFAULT_MODEL,
        "voice": QWEN_TTS_DEFAULT_VOICE,
        "rate": 1.0,
        "volume": 0.8,
        "instructions": "",
        "optimize_instructions": True,
        # Opt-in: derive reading guidance for instruct models from the
        # roleplay persona settings when no explicit instructions are set.
        "use_persona_instructions": False,
        "timeout_seconds": 30,
        "connect_timeout_seconds": 30,
        "read_timeout_seconds": 30,
        "wall_timeout_seconds": 45,
        "max_retries": 0,
    },
    "qwen_vc": {
        "api_key": "",
        "region": QWEN_VC_DEFAULT_REGION,
        "base_url": QWEN_TTS_BASE_URL_MAINLAND,
        "model": QWEN_VC_DEFAULT_MODEL,
        # No preset voices exist for cloning; the player registers one first.
        "voice": "",
        "rate": 1.0,
        "volume": 0.8,
        # Opens the TLS session ahead of the first utterance; there is no local
        # model to load, so this is purely a connection warm-up.
        "prewarm": True,
        "timeout_seconds": 30,
        "connect_timeout_seconds": 30,
        "read_timeout_seconds": 30,
        "wall_timeout_seconds": 45,
        "max_retries": 0,
        # Locally remembered enrollments; the service remains the source of
        # truth and the settings page can re-sync from it on demand.
        "custom_voices": [],
        # Uploading a recording of the player's own voice to a third-party
        # service is opt-in and must be confirmed before the first upload.
        "upload_consent": False,
    },
}

QWEN_TTS_VOICE_OPTIONS = (
    ("Cherry", "Cherry / 芊悦", "multi", "Female", "multi"),
    ("Serena", "Serena / 苏瑶", "multi", "Female", "multi"),
    ("Ethan", "Ethan / 晨煦", "multi", "Male", "multi"),
    ("Chelsie", "Chelsie / 千雪", "multi", "Female", "multi"),
    ("Momo", "Momo / 茉兔", "multi", "Female", "multi"),
    ("Vivian", "Vivian / 十三", "multi", "Female", "multi"),
    ("Moon", "Moon / 月白", "multi", "Male", "multi"),
    ("Maia", "Maia / 四月", "multi", "Female", "multi"),
    ("Kai", "Kai / 凯", "multi", "Male", "multi"),
    ("Nofish", "Nofish / 不吃鱼", "multi", "Male", "multi"),
    ("Bella", "Bella / 萌宝", "multi", "Female", "multi"),
    ("Jennifer", "Jennifer / 詹妮弗", "multi", "Female", "multi"),
    ("Ryan", "Ryan / 甜茶", "multi", "Male", "multi"),
    ("Katerina", "Katerina / 卡捷琳娜", "multi", "Female", "multi"),
    ("Aiden", "Aiden / 艾登", "multi", "Male", "multi"),
    ("Eldric Sage", "Eldric Sage / 沧明子", "multi", "Male", "multi"),
    ("Mia", "Mia / 乖小妹", "multi", "Female", "multi"),
    ("Mochi", "Mochi / 沙小弥", "multi", "Male", "multi"),
    ("Bellona", "Bellona / 燕铮莺", "multi", "Female", "multi"),
    ("Vincent", "Vincent / 田叔", "multi", "Male", "multi"),
    ("Bunny", "Bunny / 萌小姬", "multi", "Female", "multi"),
    ("Neil", "Neil / 阿闻", "multi", "Male", "multi"),
    ("Elias", "Elias / 墨讲师", "multi", "Female", "multi"),
    ("Arthur", "Arthur / 徐大爷", "multi", "Male", "multi"),
    ("Nini", "Nini / 邻家妹妹", "multi", "Female", "multi"),
    ("Seren", "Seren / 小婉", "multi", "Female", "multi"),
    ("Pip", "Pip / 顽屁小孩", "multi", "Male", "multi"),
    ("Stella", "Stella / 少女阿月", "multi", "Female", "multi"),
    ("Bodega", "Bodega / 博德加", "multi", "Male", "multi"),
    ("Sonrisa", "Sonrisa / 索尼莎", "multi", "Female", "multi"),
    ("Alek", "Alek / 阿列克", "multi", "Male", "multi"),
    ("Dolce", "Dolce / 多尔切", "multi", "Male", "multi"),
    ("Sohee", "Sohee / 素熙", "multi", "Female", "multi"),
    ("Ono Anna", "Ono Anna / 小野杏", "multi", "Female", "multi"),
    ("Lenn", "Lenn / 莱恩", "multi", "Male", "multi"),
    ("Emilien", "Emilien / 埃米尔安", "multi", "Male", "multi"),
    ("Andre", "Andre / 安德雷", "multi", "Male", "multi"),
    ("Radio Gol", "Radio Gol / 拉迪奥·戈尔", "multi", "Male", "multi"),
    ("Jada", "Jada / 上海-阿珍", "multi", "Female", "multi"),
    ("Dylan", "Dylan / 北京-晓东", "multi", "Male", "multi"),
    ("Li", "Li / 南京-老李", "multi", "Male", "multi"),
    ("Marcus", "Marcus / 陕西-秦川", "multi", "Male", "multi"),
    ("Roy", "Roy / 闽南-阿杰", "multi", "Male", "multi"),
    ("Peter", "Peter / 天津-李彼得", "multi", "Male", "multi"),
    ("Sunny", "Sunny / 四川-晴儿", "multi", "Female", "multi"),
    ("Eric", "Eric / 四川-程川", "multi", "Male", "multi"),
    ("Rocky", "Rocky / 粤语-阿强", "multi", "Male", "multi"),
    ("Kiki", "Kiki / 粤语-阿清", "multi", "Female", "multi"),
)

TTS_API_VOICE_OPTIONS = {
    "mimo_tts": (
        ("mimo_default", "MiMo Default", "zh", "Neutral", "zh-CN"),
        ("冰糖", "冰糖", "zh", "Female", "zh-CN"),
        ("茉莉", "茉莉", "zh", "Female", "zh-CN"),
        ("苏打", "苏打", "zh", "Male", "zh-CN"),
        ("白桦", "白桦", "zh", "Male", "zh-CN"),
        ("Mia", "Mia", "en", "Female", "en-US"),
        ("Chloe", "Chloe", "en", "Female", "en-US"),
        ("Milo", "Milo", "en", "Male", "en-US"),
        ("Dean", "Dean", "en", "Male", "en-US"),
    ),
    "qwen_tts": QWEN_TTS_VOICE_OPTIONS,
    # Cloning has no catalog: every voice is registered by the player and is
    # resolved from config at runtime.
    "qwen_vc": (),
}
_ENGINE_ALIASES = {
    "mimo": "mimo_tts",
    "xiaomi_tts": "mimo_tts",
    "mimo-tts": "mimo_tts",
    "qwen": "qwen_tts",
    "qwen3_tts": "qwen_tts",
    "qwen-tts": "qwen_tts",
    "qwen3-tts": "qwen_tts",
    "qwen_voice_clone": "qwen_vc",
    "qwen-vc": "qwen_vc",
    "qwen3_tts_vc": "qwen_vc",
    "qwen3-tts-vc": "qwen_vc",
    "voice_clone": "qwen_vc",
}


def normalize_cloned_voices(value: object) -> tuple[dict[str, str], ...]:
    """Normalize stored cloned-voice records, dropping malformed entries."""

    if not isinstance(value, (list, tuple)):
        return ()
    voices: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        voice_id = str(entry.get("voice_id") or entry.get("voice") or "").strip()
        if not voice_id or voice_id in seen:
            continue
        seen.add(voice_id)
        voices.append(
            {
                "voice_id": voice_id,
                "display_name": str(entry.get("display_name") or "").strip(),
                "target_model": str(entry.get("target_model") or "").strip(),
                "created_at": str(entry.get("created_at") or "").strip(),
                "language": str(entry.get("language") or "").strip(),
                # DashScope keys and voices are scoped to a service region;
                # older records predate this field and stay usable anywhere.
                "region": str(entry.get("region") or "").strip(),
                # Review state from the service: DEPLOYING, OK or UNDEPLOYED.
                # Empty for records created before reviews existed.
                "status": str(entry.get("status") or "").strip().upper(),
            }
        )
    return tuple(voices)


def cloned_voice_is_usable(
    voice: Mapping[str, object],
    *,
    model: object,
    region: object,
) -> bool:
    """Return whether a stored cloned voice can be used with this model/region."""

    wanted_model = str(model or "").strip()
    wanted_region = str(region or "").strip()
    voice_model = str(voice.get("target_model") or "").strip()
    voice_region = str(voice.get("region") or "").strip()
    if wanted_model and voice_model and voice_model != wanted_model:
        return False
    if wanted_region and voice_region and voice_region != wanted_region:
        return False
    # A recording the service rejected during review can never speak.
    if str(voice.get("status") or "").strip().upper() == "UNDEPLOYED":
        return False
    return True


def get_cloned_voice_options(
    config: Mapping[str, object] | None,
) -> tuple[tuple[str, str, str, str, str], ...]:
    """Render stored cloned voices in the shared voice-option tuple shape."""

    raw = config.get("custom_voices") if isinstance(config, Mapping) else None
    return tuple(
        (
            voice["voice_id"],
            voice["display_name"] or voice["voice_id"],
            voice["language"] or "multi",
            "",
            voice["language"] or "multi",
        )
        for voice in normalize_cloned_voices(raw)
    )


def usable_cloned_voice_options(
    config: Mapping[str, object] | None,
) -> tuple[tuple[str, str, str, str, str], ...]:
    """Cloned voices registered for the configured model and service region."""

    if not isinstance(config, Mapping):
        return ()
    model = config.get("model", "")
    region = config.get("region", "")
    return tuple(
        (
            voice["voice_id"],
            voice["display_name"] or voice["voice_id"],
            voice["language"] or "multi",
            "",
            voice["language"] or "multi",
        )
        for voice in normalize_cloned_voices(config.get("custom_voices"))
        if cloned_voice_is_usable(voice, model=model, region=region)
    )


def normalize_tts_api_engine(engine: object) -> str:
    text = str(engine or "").strip().lower().replace("-", "_")
    return _ENGINE_ALIASES.get(text, text if text in TTS_API_ENGINE_IDS else "")


def tts_api_engine_has_regions(engine: object) -> bool:
    return bool(normalize_tts_api_engine(engine))


def _normalize_region_token(region: object) -> str:
    return str(region or "").strip().lower().replace(" ", "_").replace("-", "_")


def normalize_tts_api_region(
    engine: object,
    region: object,
    default_region: object | None = None,
) -> str:
    engine_code = normalize_tts_api_engine(engine)
    if not engine_code:
        return ""
    aliases = TTS_API_REGION_ALIASES.get(engine_code, {})
    base_urls = TTS_API_REGION_BASE_URLS.get(engine_code, {})
    text = aliases.get(_normalize_region_token(region), _normalize_region_token(region))
    if text in base_urls or text == "custom":
        return text
    fallback = aliases.get(
        _normalize_region_token(default_region),
        _normalize_region_token(default_region),
    )
    if fallback in base_urls or fallback == "custom":
        return fallback
    return TTS_API_DEFAULT_REGIONS[engine_code]


def get_tts_api_region_options(engine: object) -> tuple[tuple[str, str], ...]:
    engine_code = normalize_tts_api_engine(engine)
    return tuple(TTS_API_REGION_OPTION_KEYS.get(engine_code, ()))


def get_tts_api_base_url(engine: object, region: object) -> str:
    engine_code = normalize_tts_api_engine(engine)
    if not engine_code:
        return ""
    region_code = normalize_tts_api_region(engine_code, region)
    return TTS_API_REGION_BASE_URLS[engine_code].get(region_code, "")


def get_tts_api_known_base_urls(engine: object) -> frozenset[str]:
    engine_code = normalize_tts_api_engine(engine)
    if not engine_code:
        return frozenset()
    return frozenset(
        value for value in TTS_API_REGION_BASE_URLS[engine_code].values() if value
    ) | TTS_API_LEGACY_BASE_URLS.get(engine_code, frozenset())


def tts_api_region_from_base_url(engine: object, base_url: object) -> str:
    engine_code = normalize_tts_api_engine(engine)
    if not engine_code:
        return ""
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        return ""
    for region, url in TTS_API_REGION_BASE_URLS[engine_code].items():
        if text == str(url).rstrip("/"):
            return region
    if text in TTS_API_LEGACY_BASE_URLS.get(engine_code, frozenset()):
        # An endpoint this engine no longer offers is not a custom address.
        return TTS_API_DEFAULT_REGIONS[engine_code]
    return "custom"


def get_tts_api_default_config(engine: object) -> dict[str, object]:
    engine_code = normalize_tts_api_engine(engine)
    defaults = TTS_API_DEFAULT_CONFIGS.get(engine_code, {})
    return copy.deepcopy(defaults)


def get_tts_api_default_value(engine: object, key: str) -> object:
    return get_tts_api_default_config(engine).get(key)


def get_tts_api_model_options(engine: object, current: object = "") -> tuple[str, ...]:
    engine_code = normalize_tts_api_engine(engine)
    options = list(TTS_API_MODEL_OPTIONS.get(engine_code, ()))
    current_text = str(current or "").strip()
    if current_text and current_text not in options:
        options.append(current_text)
    return tuple(options)


def get_tts_api_voice_options(engine: object) -> tuple[tuple[str, str, str, str, str], ...]:
    engine_code = normalize_tts_api_engine(engine)
    return tuple(TTS_API_VOICE_OPTIONS.get(engine_code, ()))


def resolve_tts_api_config(engine: object, config: Mapping[str, object] | None) -> dict[str, object]:
    engine_code = normalize_tts_api_engine(engine)
    raw_config = config if isinstance(config, Mapping) else {}
    raw_region = str(raw_config.get("region", "") or "").strip()
    raw_base_url = str(raw_config.get("base_url", "") or "").strip().rstrip("/")
    inferred_region = (
        tts_api_region_from_base_url(engine_code, raw_base_url)
        if not raw_region
        else ""
    )
    defaults = get_tts_api_default_config(engine_code)
    defaults.update(raw_config)
    base_url = str(defaults.get("base_url", "") or "").strip().rstrip("/")
    region = normalize_tts_api_region(
        engine_code,
        raw_region or inferred_region or defaults.get("region"),
        default_region=inferred_region or defaults.get("region"),
    )
    defaults["region"] = region
    known_base_urls = get_tts_api_known_base_urls(engine_code)
    auto_base_url = get_tts_api_base_url(engine_code, region)
    if auto_base_url and (not base_url or base_url in known_base_urls):
        base_url = auto_base_url
    elif region in TTS_API_REGION_BASE_URLS.get(engine_code, {}) and not auto_base_url:
        # Workspace-scoped regions must never inherit another region's shared
        # default endpoint. Preserve only the URL explicitly supplied by the
        # player; an empty value is surfaced as a configuration error at use.
        base_url = raw_base_url
    defaults["base_url"] = base_url
    return defaults
