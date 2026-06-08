from __future__ import annotations

import copy
from collections.abc import Mapping

QWEN_TTS_BASE_URL_MAINLAND = "https://dashscope.aliyuncs.com/api/v1"
QWEN_TTS_BASE_URL_INTERNATIONAL = "https://dashscope-intl.aliyuncs.com/api/v1"
QWEN_TTS_DEFAULT_REGION = "singapore"
QWEN_TTS_DEFAULT_MODEL = "qwen3-tts-flash"
QWEN_TTS_DEFAULT_VOICE = "Cherry"
QWEN_TTS_REGION_BASE_URLS = {
    "china_mainland": QWEN_TTS_BASE_URL_MAINLAND,
    "singapore": QWEN_TTS_BASE_URL_INTERNATIONAL,
}
QWEN_TTS_REGION_ALIASES = {
    "china": "china_mainland",
    "cn": "china_mainland",
    "mainland": "china_mainland",
    "china-mainland": "china_mainland",
    "intl": "singapore",
    "international": "singapore",
    "sg": "singapore",
}

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

TTS_API_ENGINE_IDS = ("mimo_tts", "qwen_tts")
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
        ("qwen_region_china_mainland", "china_mainland"),
        ("qwen_region_custom", "custom"),
    ),
}
TTS_API_REGION_BASE_URLS = {
    "mimo_tts": XIAOMI_TTS_REGION_BASE_URLS,
    "qwen_tts": QWEN_TTS_REGION_BASE_URLS,
}
TTS_API_REGION_ALIASES = {
    "mimo_tts": XIAOMI_TTS_REGION_ALIASES,
    "qwen_tts": QWEN_TTS_REGION_ALIASES,
}
TTS_API_DEFAULT_REGIONS = {
    "mimo_tts": XIAOMI_TTS_DEFAULT_REGION,
    "qwen_tts": QWEN_TTS_DEFAULT_REGION,
}
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
        "timeout_seconds": 30,
        "max_retries": 0,
    },
}

QWEN_TTS_VOICE_OPTIONS = (
    ("Cherry", "Cherry / 芊悦", "multi", "Female", "multi"),
    ("Serena", "Serena / 苏瑶", "multi", "Female", "multi"),
    ("Ethan", "Ethan / 晨煦", "multi", "Male", "multi"),
    ("Chelsie", "Chelsie / 千雪", "multi", "Female", "multi"),
    ("Momo", "Momo / 茉兔", "multi", "Female", "multi"),
    ("Vivian", "Vivian / 十三", "multi", "Female", "multi"),
    ("Moon", "Moon / 月白", "multi", "Female", "multi"),
    ("Maia", "Maia / 四月", "multi", "Female", "multi"),
    ("Kai", "Kai / 凯", "multi", "Male", "multi"),
    ("Nofish", "Nofish / 不吃鱼", "multi", "Female", "multi"),
    ("Bella", "Bella / 萌宝", "multi", "Female", "multi"),
    ("Jennifer", "Jennifer / 詹妮弗", "multi", "Female", "multi"),
    ("Ryan", "Ryan / 甜茶", "multi", "Male", "multi"),
    ("Katerina", "Katerina / 卡捷琳娜", "multi", "Female", "multi"),
    ("Aiden", "Aiden / 艾登", "multi", "Male", "multi"),
    ("Eldric Sage", "Eldric Sage / 沧明子", "multi", "Male", "multi"),
    ("Mia", "Mia / 乖小妹", "multi", "Female", "multi"),
    ("Mochi", "Mochi / 沙小弥", "multi", "Female", "multi"),
    ("Bellona", "Bellona / 燕铮莺", "multi", "Female", "multi"),
    ("Vincent", "Vincent / 田叔", "multi", "Male", "multi"),
    ("Bunny", "Bunny / 萌小姬", "multi", "Female", "multi"),
    ("Neil", "Neil / 阿闻", "multi", "Male", "multi"),
    ("Elias", "Elias / 墨讲师", "multi", "Male", "multi"),
    ("Arthur", "Arthur / 徐大爷", "multi", "Male", "multi"),
    ("Nini", "Nini / 邻家妹妹", "multi", "Female", "multi"),
    ("Ebona", "Ebona / 伊波娜", "multi", "Female", "multi"),
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
}
_ENGINE_ALIASES = {
    "mimo": "mimo_tts",
    "xiaomi_tts": "mimo_tts",
    "mimo-tts": "mimo_tts",
    "qwen": "qwen_tts",
    "qwen3_tts": "qwen_tts",
    "qwen-tts": "qwen_tts",
    "qwen3-tts": "qwen_tts",
}


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
    return frozenset(TTS_API_REGION_BASE_URLS[engine_code].values())


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
    return "custom"


def get_tts_api_default_config(engine: object) -> dict[str, object]:
    engine_code = normalize_tts_api_engine(engine)
    defaults = TTS_API_DEFAULT_CONFIGS.get(engine_code, {})
    return copy.deepcopy(defaults)


def get_tts_api_default_value(engine: object, key: str) -> object:
    return get_tts_api_default_config(engine).get(key)


def get_tts_api_voice_options(engine: object) -> tuple[tuple[str, str, str, str, str], ...]:
    engine_code = normalize_tts_api_engine(engine)
    return tuple(TTS_API_VOICE_OPTIONS.get(engine_code, ()))


def resolve_tts_api_config(engine: object, config: Mapping[str, object] | None) -> dict[str, object]:
    engine_code = normalize_tts_api_engine(engine)
    defaults = get_tts_api_default_config(engine_code)
    if isinstance(config, Mapping):
        defaults.update(config)
    base_url = str(defaults.get("base_url", "") or "").strip().rstrip("/")
    region = normalize_tts_api_region(
        engine_code,
        defaults.get("region"),
        default_region=tts_api_region_from_base_url(engine_code, base_url),
    )
    defaults["region"] = region
    known_base_urls = get_tts_api_known_base_urls(engine_code)
    auto_base_url = get_tts_api_base_url(engine_code, region)
    if auto_base_url and (not base_url or base_url in known_base_urls):
        base_url = auto_base_url
    defaults["base_url"] = base_url
    return defaults
