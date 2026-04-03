from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass

from src.utils.ui_config import DEFAULT_UI_LANGUAGE, normalize_backend


@dataclass(frozen=True)
class FriendlyTranslationError:
    short_message: str
    inline_message: str
    detailed_message: str
    category: str
    detail: str


_SUPPORTED_UI_LANGUAGES = ("zh-CN", "en", "ja", "ru", "ko")

_TEXTS: dict[str, dict[str, str]] = {
    "zh-CN": {
        "unknown_provider": "翻译服务",
        "error_prefix": "[错误]",
        "detail_label": "详细信息",
        "auth_short": "翻译失败：{provider} API 密钥无效",
        "auth_inline": "{prefix} {provider} API 密钥无效，请检查设置中的 API Key。",
        "auth_detail": "{provider} API 密钥无效。\n\n{hint}{detail_suffix}",
        "parameter_short": "翻译失败：当前模型不支持该请求参数",
        "parameter_inline": "{prefix} 当前模型不支持参数 {subject}，请更新程序或更换模型。",
        "parameter_detail": "当前模型不支持参数 {subject}。\n\n请更新到最新版，或更换模型/供应商后重试。{detail_suffix}",
        "model_short": "翻译失败：模型配置无效",
        "model_inline": "{prefix} 模型名称或 Endpoint ID 无效，请检查设置。",
        "model_detail": "模型名称或 Endpoint ID 无效。\n\n{hint}{detail_suffix}",
        "quota_short": "翻译失败：额度不足或请求过快",
        "quota_inline": "{prefix} 额度不足，或请求过于频繁，请稍后再试。",
        "quota_detail": "接口暂时不可用，可能是额度不足、余额不足，或请求过于频繁。\n\n请检查供应商后台的额度、计费和限流设置。{detail_suffix}",
        "network_short": "翻译失败：网络连接异常",
        "network_inline": "{prefix} 网络连接失败或请求超时，请稍后重试。",
        "network_detail": "无法连接到翻译服务，或请求已超时。\n\n请检查网络、代理和 Base URL 设置后重试。{detail_suffix}",
        "ready_short": "翻译失败：翻译器尚未就绪",
        "ready_inline": "{prefix} 翻译器尚未就绪，请检查 API 设置后重试。",
        "ready_detail": "翻译器尚未就绪。\n\n请先确认 API Key、模型和后端设置是否正确，然后重新开始。{detail_suffix}",
        "empty_short": "翻译失败：接口没有返回结果",
        "empty_inline": "{prefix} 翻译接口没有返回结果，请稍后重试。",
        "empty_detail": "翻译接口没有返回有效结果。\n\n请稍后重试，或更换模型后再试。{detail_suffix}",
        "dependency_short": "翻译失败：缺少运行依赖",
        "dependency_inline": "{prefix} 缺少翻译依赖，请重新安装程序。",
        "dependency_detail": "当前环境缺少翻译依赖。\n\n请重新安装程序，或按提示安装缺失的 Python 包。{detail_suffix}",
        "config_short": "翻译失败：API 设置不完整",
        "config_inline": "{prefix} API Key 或模型未配置，请先检查设置。",
        "config_detail": "翻译设置不完整。\n\n请检查 API Key、模型和后端配置后重试。{detail_suffix}",
        "generic_short": "翻译失败：{provider} 接口返回错误",
        "generic_inline": "{prefix} {provider} 接口返回错误，请检查设置后重试。",
        "generic_detail": "{provider} 接口返回错误。\n\n请检查 API Key、模型、Base URL 和网络设置。{detail_suffix}",
        "model_hint_default": "请检查设置中的 Model 是否正确，或更换该供应商支持的模型。",
        "model_hint_doubao": "请在 Model 字段中填写有效的火山引擎 Ark Endpoint ID。",
        "auth_hint_openai": "请填写有效的 OpenAI API Key。",
        "auth_hint_deepseek": "请填写有效的 DeepSeek API Key。",
        "auth_hint_zhipu": "请填写有效的智谱 GLM API Key。",
        "auth_hint_qianwen": "请填写有效的阿里云 DashScope API Key。",
        "auth_hint_gemini": "请填写有效的 Google AI Studio / Gemini API Key。",
        "auth_hint_doubao": "请填写有效的火山引擎 Ark API Key，并在 Model 中填写 Endpoint ID。",
        "auth_hint_anthropic": "请填写有效的 Anthropic API Key。",
        "auth_hint_default": "请检查设置中的 API Key 是否填写正确。",
        "parameter_subject": "“{parameter}”",
        "parameter_subject_generic": "当前请求参数",
    },
    "en": {
        "unknown_provider": "translation service",
        "error_prefix": "[Error]",
        "detail_label": "Details",
        "auth_short": "Translation failed: invalid {provider} API key",
        "auth_inline": "{prefix} The {provider} API key is invalid. Please check the API Key in Settings.",
        "auth_detail": "The {provider} API key is invalid.\n\n{hint}{detail_suffix}",
        "parameter_short": "Translation failed: this model does not support the request parameter",
        "parameter_inline": "{prefix} This model does not support {subject}. Update the app or switch models.",
        "parameter_detail": "This model does not support {subject}.\n\nUpdate to the latest version, or switch to another model/provider and try again.{detail_suffix}",
        "model_short": "Translation failed: invalid model configuration",
        "model_inline": "{prefix} The model name or Endpoint ID is invalid. Please check Settings.",
        "model_detail": "The model name or Endpoint ID is invalid.\n\n{hint}{detail_suffix}",
        "quota_short": "Translation failed: quota exceeded or rate limited",
        "quota_inline": "{prefix} The request was limited, or your quota has been used up. Please try again later.",
        "quota_detail": "The translation service is temporarily unavailable, likely due to quota, balance, or rate limits.\n\nPlease check your provider dashboard and billing settings.{detail_suffix}",
        "network_short": "Translation failed: network connection issue",
        "network_inline": "{prefix} The request timed out or could not reach the translation service.",
        "network_detail": "The app could not reach the translation service, or the request timed out.\n\nPlease check your network, proxy, and Base URL settings and try again.{detail_suffix}",
        "ready_short": "Translation failed: translator is not ready",
        "ready_inline": "{prefix} The translator is not ready. Please check your API settings and try again.",
        "ready_detail": "The translator is not ready.\n\nPlease confirm your API key, model, and backend settings, then start again.{detail_suffix}",
        "empty_short": "Translation failed: empty response from the API",
        "empty_inline": "{prefix} The translation API returned an empty response. Please try again.",
        "empty_detail": "The translation API did not return any usable result.\n\nPlease try again later, or switch to another model.{detail_suffix}",
        "dependency_short": "Translation failed: missing runtime dependency",
        "dependency_inline": "{prefix} A required translation dependency is missing. Please reinstall the app.",
        "dependency_detail": "A required translation dependency is missing in the current environment.\n\nPlease reinstall the app, or install the missing Python package if you are running from source.{detail_suffix}",
        "config_short": "Translation failed: incomplete API settings",
        "config_inline": "{prefix} The API key or model is not configured. Please check Settings first.",
        "config_detail": "The translation settings are incomplete.\n\nPlease check your API key, model, and backend configuration and try again.{detail_suffix}",
        "generic_short": "Translation failed: {provider} returned an error",
        "generic_inline": "{prefix} {provider} returned an error. Please check your settings and try again.",
        "generic_detail": "{provider} returned an error.\n\nPlease check your API key, model, Base URL, and network settings.{detail_suffix}",
        "model_hint_default": "Please check whether the Model field is correct, or switch to a model supported by this provider.",
        "model_hint_doubao": "Please enter a valid Volcano Ark Endpoint ID in the Model field.",
        "auth_hint_openai": "Please use a valid OpenAI API key.",
        "auth_hint_deepseek": "Please use a valid DeepSeek API key.",
        "auth_hint_zhipu": "Please use a valid Zhipu GLM API key.",
        "auth_hint_qianwen": "Please use a valid Alibaba Cloud DashScope API key.",
        "auth_hint_gemini": "Please use a valid Google AI Studio / Gemini API key.",
        "auth_hint_doubao": "Please use a valid Volcano Ark API key, and put your Endpoint ID in the Model field.",
        "auth_hint_anthropic": "Please use a valid Anthropic API key.",
        "auth_hint_default": "Please check whether the API key in Settings is correct.",
        "parameter_subject": "\"{parameter}\"",
        "parameter_subject_generic": "the current request parameter",
    },
    "ja": {
        "unknown_provider": "翻訳サービス",
        "error_prefix": "[エラー]",
        "detail_label": "詳細",
        "auth_short": "翻訳失敗: {provider} の APIキーが無効です",
        "auth_inline": "{prefix} {provider} の APIキーが無効です。設定の API Key を確認してください。",
        "auth_detail": "{provider} の APIキーが無効です。\n\n{hint}{detail_suffix}",
        "parameter_short": "翻訳失敗: このモデルでは要求パラメータを使えません",
        "parameter_inline": "{prefix} このモデルは {subject} をサポートしていません。アプリを更新するか、別のモデルを使ってください。",
        "parameter_detail": "このモデルは {subject} をサポートしていません。\n\n最新版に更新するか、別のモデル/プロバイダに切り替えて再試行してください。{detail_suffix}",
        "model_short": "翻訳失敗: モデル設定が無効です",
        "model_inline": "{prefix} モデル名または Endpoint ID が無効です。設定を確認してください。",
        "model_detail": "モデル名または Endpoint ID が無効です。\n\n{hint}{detail_suffix}",
        "quota_short": "翻訳失敗: 利用上限超過またはリクエスト過多です",
        "quota_inline": "{prefix} 利用上限を超えたか、リクエストが多すぎます。しばらくしてから再試行してください。",
        "quota_detail": "翻訳サービスが一時的に利用できません。利用上限、残高不足、またはレート制限の可能性があります。\n\nプロバイダ側の利用状況と課金設定を確認してください。{detail_suffix}",
        "network_short": "翻訳失敗: ネットワーク接続エラーです",
        "network_inline": "{prefix} 翻訳サービスに接続できないか、リクエストがタイムアウトしました。",
        "network_detail": "翻訳サービスに接続できないか、リクエストがタイムアウトしました。\n\nネットワーク、プロキシ、Base URL の設定を確認して再試行してください。{detail_suffix}",
        "ready_short": "翻訳失敗: 翻訳機能の準備ができていません",
        "ready_inline": "{prefix} 翻訳機能の準備ができていません。API 設定を確認して再試行してください。",
        "ready_detail": "翻訳機能の準備ができていません。\n\nAPI Key、モデル、バックエンド設定を確認してから、もう一度開始してください。{detail_suffix}",
        "empty_short": "翻訳失敗: API から結果が返ってきませんでした",
        "empty_inline": "{prefix} 翻訳 API から結果が返ってきませんでした。しばらくしてから再試行してください。",
        "empty_detail": "翻訳 API から有効な結果が返りませんでした。\n\n時間をおいて再試行するか、別のモデルに切り替えてください。{detail_suffix}",
        "dependency_short": "翻訳失敗: 必要な依存関係が不足しています",
        "dependency_inline": "{prefix} 翻訳に必要な依存関係が不足しています。アプリを再インストールしてください。",
        "dependency_detail": "現在の環境で翻訳に必要な依存関係が不足しています。\n\nアプリを再インストールするか、ソース版の場合は不足している Python パッケージを導入してください。{detail_suffix}",
        "config_short": "翻訳失敗: API 設定が不足しています",
        "config_inline": "{prefix} API Key またはモデルが未設定です。先に設定を確認してください。",
        "config_detail": "翻訳設定が不足しています。\n\nAPI Key、モデル、バックエンド設定を確認して再試行してください。{detail_suffix}",
        "generic_short": "翻訳失敗: {provider} でエラーが返されました",
        "generic_inline": "{prefix} {provider} でエラーが返されました。設定を確認して再試行してください。",
        "generic_detail": "{provider} でエラーが返されました。\n\nAPI Key、モデル、Base URL、ネットワーク設定を確認してください。{detail_suffix}",
        "model_hint_default": "設定の Model が正しいか確認するか、このプロバイダで使えるモデルに切り替えてください。",
        "model_hint_doubao": "Model 欄に有効な Volcano Ark Endpoint ID を入力してください。",
        "auth_hint_openai": "有効な OpenAI API Key を入力してください。",
        "auth_hint_deepseek": "有効な DeepSeek API Key を入力してください。",
        "auth_hint_zhipu": "有効な Zhipu GLM API Key を入力してください。",
        "auth_hint_qianwen": "有効な Alibaba Cloud DashScope API Key を入力してください。",
        "auth_hint_gemini": "有効な Google AI Studio / Gemini API Key を入力してください。",
        "auth_hint_doubao": "有効な Volcano Ark API Key を入力し、Model 欄には Endpoint ID を設定してください。",
        "auth_hint_anthropic": "有効な Anthropic API Key を入力してください。",
        "auth_hint_default": "設定の API Key が正しいか確認してください。",
        "parameter_subject": "「{parameter}」",
        "parameter_subject_generic": "現在のリクエストパラメータ",
    },
    "ru": {
        "unknown_provider": "сервис перевода",
        "error_prefix": "[Ошибка]",
        "detail_label": "Подробности",
        "auth_short": "Ошибка перевода: неверный API-ключ {provider}",
        "auth_inline": "{prefix} API-ключ {provider} недействителен. Проверьте API Key в настройках.",
        "auth_detail": "API-ключ {provider} недействителен.\n\n{hint}{detail_suffix}",
        "parameter_short": "Ошибка перевода: модель не поддерживает этот параметр",
        "parameter_inline": "{prefix} Эта модель не поддерживает {subject}. Обновите приложение или смените модель.",
        "parameter_detail": "Эта модель не поддерживает {subject}.\n\nОбновите приложение до последней версии или переключитесь на другую модель/провайдера и повторите попытку.{detail_suffix}",
        "model_short": "Ошибка перевода: неверная конфигурация модели",
        "model_inline": "{prefix} Неверное имя модели или Endpoint ID. Проверьте настройки.",
        "model_detail": "Неверное имя модели или Endpoint ID.\n\n{hint}{detail_suffix}",
        "quota_short": "Ошибка перевода: превышен лимит или слишком много запросов",
        "quota_inline": "{prefix} Достигнут лимит или запросов слишком много. Попробуйте позже.",
        "quota_detail": "Сервис перевода временно недоступен. Возможны проблемы с квотой, балансом или ограничением частоты запросов.\n\nПроверьте лимиты и биллинг у провайдера.{detail_suffix}",
        "network_short": "Ошибка перевода: проблема с сетью",
        "network_inline": "{prefix} Не удалось подключиться к сервису перевода или истекло время ожидания.",
        "network_detail": "Не удалось подключиться к сервису перевода или запрос завершился по тайм-ауту.\n\nПроверьте сеть, прокси и настройки Base URL, затем повторите попытку.{detail_suffix}",
        "ready_short": "Ошибка перевода: переводчик не готов",
        "ready_inline": "{prefix} Переводчик еще не готов. Проверьте API-настройки и повторите попытку.",
        "ready_detail": "Переводчик еще не готов.\n\nПроверьте API Key, модель и настройки бэкенда, затем запустите снова.{detail_suffix}",
        "empty_short": "Ошибка перевода: API вернул пустой ответ",
        "empty_inline": "{prefix} API перевода вернул пустой ответ. Попробуйте еще раз позже.",
        "empty_detail": "API перевода не вернул пригодный результат.\n\nПопробуйте позже или переключитесь на другую модель.{detail_suffix}",
        "dependency_short": "Ошибка перевода: отсутствует зависимость",
        "dependency_inline": "{prefix} Отсутствует необходимая зависимость для перевода. Переустановите приложение.",
        "dependency_detail": "В текущем окружении отсутствует необходимая зависимость для перевода.\n\nПереустановите приложение или установите недостающий Python-пакет, если запускаете проект из исходников.{detail_suffix}",
        "config_short": "Ошибка перевода: настройки API неполные",
        "config_inline": "{prefix} API Key или модель не настроены. Сначала проверьте настройки.",
        "config_detail": "Настройки перевода неполные.\n\nПроверьте API Key, модель и конфигурацию бэкенда, затем повторите попытку.{detail_suffix}",
        "generic_short": "Ошибка перевода: {provider} вернул ошибку",
        "generic_inline": "{prefix} {provider} вернул ошибку. Проверьте настройки и повторите попытку.",
        "generic_detail": "{provider} вернул ошибку.\n\nПроверьте API Key, модель, Base URL и сетевые настройки.{detail_suffix}",
        "model_hint_default": "Проверьте поле Model или переключитесь на модель, поддерживаемую этим провайдером.",
        "model_hint_doubao": "Введите корректный Volcano Ark Endpoint ID в поле Model.",
        "auth_hint_openai": "Используйте действительный API-ключ OpenAI.",
        "auth_hint_deepseek": "Используйте действительный API-ключ DeepSeek.",
        "auth_hint_zhipu": "Используйте действительный API-ключ Zhipu GLM.",
        "auth_hint_qianwen": "Используйте действительный API-ключ Alibaba Cloud DashScope.",
        "auth_hint_gemini": "Используйте действительный API-ключ Google AI Studio / Gemini.",
        "auth_hint_doubao": "Используйте действительный API-ключ Volcano Ark и укажите Endpoint ID в поле Model.",
        "auth_hint_anthropic": "Используйте действительный API-ключ Anthropic.",
        "auth_hint_default": "Проверьте, правильно ли указан API Key в настройках.",
        "parameter_subject": "параметр «{parameter}»",
        "parameter_subject_generic": "текущий параметр запроса",
    },
    "ko": {
        "unknown_provider": "번역 서비스",
        "error_prefix": "[오류]",
        "detail_label": "세부 정보",
        "auth_short": "번역 실패: {provider} API 키가 올바르지 않습니다",
        "auth_inline": "{prefix} {provider} API 키가 올바르지 않습니다. 설정의 API Key를 확인해 주세요.",
        "auth_detail": "{provider} API 키가 올바르지 않습니다.\n\n{hint}{detail_suffix}",
        "parameter_short": "번역 실패: 현재 모델이 이 요청 파라미터를 지원하지 않습니다",
        "parameter_inline": "{prefix} 현재 모델은 {subject} 을(를) 지원하지 않습니다. 앱을 업데이트하거나 다른 모델을 사용해 주세요.",
        "parameter_detail": "현재 모델은 {subject} 을(를) 지원하지 않습니다.\n\n최신 버전으로 업데이트하거나 다른 모델/공급자로 변경한 뒤 다시 시도해 주세요.{detail_suffix}",
        "model_short": "번역 실패: 모델 설정이 올바르지 않습니다",
        "model_inline": "{prefix} 모델 이름 또는 Endpoint ID가 올바르지 않습니다. 설정을 확인해 주세요.",
        "model_detail": "모델 이름 또는 Endpoint ID가 올바르지 않습니다.\n\n{hint}{detail_suffix}",
        "quota_short": "번역 실패: 할당량 부족 또는 요청 과다",
        "quota_inline": "{prefix} 할당량이 부족하거나 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
        "quota_detail": "번역 서비스가 일시적으로 사용할 수 없습니다. 할당량, 잔액, 또는 요청 제한 문제일 수 있습니다.\n\n공급자 대시보드의 사용량과 결제 설정을 확인해 주세요.{detail_suffix}",
        "network_short": "번역 실패: 네트워크 연결 문제",
        "network_inline": "{prefix} 번역 서비스에 연결할 수 없거나 요청 시간이 초과되었습니다.",
        "network_detail": "번역 서비스에 연결할 수 없거나 요청 시간이 초과되었습니다.\n\n네트워크, 프록시, Base URL 설정을 확인한 뒤 다시 시도해 주세요.{detail_suffix}",
        "ready_short": "번역 실패: 번역기가 아직 준비되지 않았습니다",
        "ready_inline": "{prefix} 번역기가 아직 준비되지 않았습니다. API 설정을 확인한 뒤 다시 시도해 주세요.",
        "ready_detail": "번역기가 아직 준비되지 않았습니다.\n\nAPI Key, 모델, 백엔드 설정을 확인한 뒤 다시 시작해 주세요.{detail_suffix}",
        "empty_short": "번역 실패: API가 빈 응답을 반환했습니다",
        "empty_inline": "{prefix} 번역 API가 결과를 반환하지 않았습니다. 잠시 후 다시 시도해 주세요.",
        "empty_detail": "번역 API가 사용할 수 있는 결과를 반환하지 않았습니다.\n\n잠시 후 다시 시도하거나 다른 모델로 변경해 주세요.{detail_suffix}",
        "dependency_short": "번역 실패: 필요한 의존성이 없습니다",
        "dependency_inline": "{prefix} 번역에 필요한 의존성이 없습니다. 앱을 다시 설치해 주세요.",
        "dependency_detail": "현재 환경에 번역에 필요한 의존성이 없습니다.\n\n앱을 다시 설치하거나, 소스 실행 환경이라면 누락된 Python 패키지를 설치해 주세요.{detail_suffix}",
        "config_short": "번역 실패: API 설정이 완전하지 않습니다",
        "config_inline": "{prefix} API Key 또는 모델이 설정되지 않았습니다. 먼저 설정을 확인해 주세요.",
        "config_detail": "번역 설정이 완전하지 않습니다.\n\nAPI Key, 모델, 백엔드 설정을 확인한 뒤 다시 시도해 주세요.{detail_suffix}",
        "generic_short": "번역 실패: {provider} 에서 오류를 반환했습니다",
        "generic_inline": "{prefix} {provider} 에서 오류를 반환했습니다. 설정을 확인한 뒤 다시 시도해 주세요.",
        "generic_detail": "{provider} 에서 오류를 반환했습니다.\n\nAPI Key, 모델, Base URL, 네트워크 설정을 확인해 주세요.{detail_suffix}",
        "model_hint_default": "설정의 Model 값이 올바른지 확인하거나, 해당 공급자가 지원하는 모델로 변경해 주세요.",
        "model_hint_doubao": "Model 필드에 올바른 Volcano Ark Endpoint ID를 입력해 주세요.",
        "auth_hint_openai": "유효한 OpenAI API 키를 입력해 주세요.",
        "auth_hint_deepseek": "유효한 DeepSeek API 키를 입력해 주세요.",
        "auth_hint_zhipu": "유효한 Zhipu GLM API 키를 입력해 주세요.",
        "auth_hint_qianwen": "유효한 Alibaba Cloud DashScope API 키를 입력해 주세요.",
        "auth_hint_gemini": "유효한 Google AI Studio / Gemini API 키를 입력해 주세요.",
        "auth_hint_doubao": "유효한 Volcano Ark API 키를 입력하고 Model 필드에는 Endpoint ID를 입력해 주세요.",
        "auth_hint_anthropic": "유효한 Anthropic API 키를 입력해 주세요.",
        "auth_hint_default": "설정의 API Key가 올바른지 확인해 주세요.",
        "parameter_subject": "파라미터 \"{parameter}\"",
        "parameter_subject_generic": "현재 요청 파라미터",
    },
}

_PROVIDER_NAMES = {
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
    "zhipu": "GLM",
    "qianwen": "Qwen",
    "gemini": "Gemini",
    "doubao": "Doubao / Ark",
    "anthropic": "Claude",
}

_AUTH_KEYWORDS = (
    "api key not valid",
    "invalid api key",
    "incorrect api key",
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "permission denied",
    "invalid x-api-key",
    "invalid key",
)
_PARAMETER_KEYWORDS = (
    "unsupported parameter",
    "is not supported with this model",
    "is not supported for this model",
    "unknown name",
    "extra inputs are not permitted",
)
_MODEL_KEYWORDS = (
    "model not found",
    "invalid model",
    "no such model",
    "does not exist",
    "unknown model",
    "unsupported model",
    "endpoint id",
    "endpoint not found",
    "invalid endpoint",
)
_QUOTA_KEYWORDS = (
    "rate limit",
    "too many requests",
    "insufficient_quota",
    "quota",
    "balance",
    "credit",
    "billing",
    "resource exhausted",
)
_NETWORK_KEYWORDS = (
    "timeout",
    "timed out",
    "connection error",
    "connect error",
    "could not connect",
    "failed to establish",
    "temporary failure",
    "network is unreachable",
    "name resolution",
    "dns",
    "ssl",
    "connection reset",
    "remote end closed connection",
)


def format_translation_error(
    raw_error: object,
    backend: str | None,
    ui_language: str | None,
) -> FriendlyTranslationError:
    raw_text = _clean_text(raw_error)
    detail = _extract_detail_message(raw_text)
    combined = "\n".join(part for part in (detail, raw_text) if part).lower()
    lang = _normalize_language(ui_language)
    texts = _TEXTS[lang]
    normalized_backend = normalize_backend(backend)
    provider = _PROVIDER_NAMES.get(normalized_backend, texts["unknown_provider"])
    detail_suffix = _detail_suffix(texts, detail)
    parameter = _extract_parameter_name(detail or raw_text)
    subject = _parameter_subject(texts, parameter)

    if _contains_any(combined, "pip install", "module not found", "no module named", "未安装", "not installed"):
        category = "dependency"
        return FriendlyTranslationError(
            short_message=texts["dependency_short"],
            inline_message=texts["dependency_inline"].format(prefix=texts["error_prefix"]),
            detailed_message=texts["dependency_detail"].format(detail_suffix=detail_suffix),
            category=category,
            detail=detail,
        )

    if _contains_any(combined, "not configured", "is not configured", "missing api key", "api key is required", "model is required"):
        category = "config"
        return FriendlyTranslationError(
            short_message=texts["config_short"],
            inline_message=texts["config_inline"].format(prefix=texts["error_prefix"]),
            detailed_message=texts["config_detail"].format(detail_suffix=detail_suffix),
            category=category,
            detail=detail,
        )

    if _contains_any(combined, "translator is not ready", "translator not ready"):
        category = "ready"
        return FriendlyTranslationError(
            short_message=texts["ready_short"],
            inline_message=texts["ready_inline"].format(prefix=texts["error_prefix"]),
            detailed_message=texts["ready_detail"].format(detail_suffix=detail_suffix),
            category=category,
            detail=detail,
        )

    if _contains_any(combined, *_AUTH_KEYWORDS):
        category = "auth"
        hint = texts.get(f"auth_hint_{normalized_backend}", texts["auth_hint_default"])
        return FriendlyTranslationError(
            short_message=texts["auth_short"].format(provider=provider),
            inline_message=texts["auth_inline"].format(
                prefix=texts["error_prefix"],
                provider=provider,
            ),
            detailed_message=texts["auth_detail"].format(
                provider=provider,
                hint=hint,
                detail_suffix=detail_suffix,
            ),
            category=category,
            detail=detail,
        )

    if _contains_any(combined, *_PARAMETER_KEYWORDS):
        category = "parameter"
        return FriendlyTranslationError(
            short_message=texts["parameter_short"],
            inline_message=texts["parameter_inline"].format(
                prefix=texts["error_prefix"],
                subject=subject,
            ),
            detailed_message=texts["parameter_detail"].format(
                subject=subject,
                detail_suffix=detail_suffix,
            ),
            category=category,
            detail=detail,
        )

    if _contains_any(combined, *_MODEL_KEYWORDS):
        category = "model"
        hint_key = "model_hint_doubao" if normalized_backend == "doubao" else "model_hint_default"
        return FriendlyTranslationError(
            short_message=texts["model_short"],
            inline_message=texts["model_inline"].format(prefix=texts["error_prefix"]),
            detailed_message=texts["model_detail"].format(
                hint=texts[hint_key],
                detail_suffix=detail_suffix,
            ),
            category=category,
            detail=detail,
        )

    if _contains_any(combined, *_QUOTA_KEYWORDS):
        category = "quota"
        return FriendlyTranslationError(
            short_message=texts["quota_short"],
            inline_message=texts["quota_inline"].format(prefix=texts["error_prefix"]),
            detailed_message=texts["quota_detail"].format(detail_suffix=detail_suffix),
            category=category,
            detail=detail,
        )

    if _contains_any(combined, *_NETWORK_KEYWORDS):
        category = "network"
        return FriendlyTranslationError(
            short_message=texts["network_short"],
            inline_message=texts["network_inline"].format(prefix=texts["error_prefix"]),
            detailed_message=texts["network_detail"].format(detail_suffix=detail_suffix),
            category=category,
            detail=detail,
        )

    if _contains_any(combined, "empty response", "returned an empty response", "no output text", "empty output"):
        category = "empty"
        return FriendlyTranslationError(
            short_message=texts["empty_short"],
            inline_message=texts["empty_inline"].format(prefix=texts["error_prefix"]),
            detailed_message=texts["empty_detail"].format(detail_suffix=detail_suffix),
            category=category,
            detail=detail,
        )

    category = "generic"
    return FriendlyTranslationError(
        short_message=texts["generic_short"].format(provider=provider),
        inline_message=texts["generic_inline"].format(
            prefix=texts["error_prefix"],
            provider=provider,
        ),
        detailed_message=texts["generic_detail"].format(
            provider=provider,
            detail_suffix=detail_suffix,
        ),
        category=category,
        detail=detail,
    )


def _normalize_language(ui_language: str | None) -> str:
    candidate = str(ui_language or "").strip()
    if candidate in _SUPPORTED_UI_LANGUAGES:
        return candidate
    base = candidate.split("-", 1)[0]
    for item in _SUPPORTED_UI_LANGUAGES:
        if item.split("-", 1)[0] == base:
            return item
    return DEFAULT_UI_LANGUAGE


def _parameter_subject(texts: dict[str, str], parameter: str | None) -> str:
    if parameter:
        return texts["parameter_subject"].format(parameter=parameter)
    return texts["parameter_subject_generic"]


def _detail_suffix(texts: dict[str, str], detail: str) -> str:
    if not detail:
        return ""
    return f"\n\n{texts['detail_label']}: {detail}"


def _contains_any(text: str, *keywords: str) -> bool:
    return any(keyword in text for keyword in keywords)


def _clean_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "Unknown error"
    return re.sub(r"\s+", " ", text).strip()


def _extract_detail_message(raw_text: str) -> str:
    for candidate in _payload_candidates(raw_text):
        parsed = _parse_payload(candidate)
        if parsed is None:
            continue
        message = _find_message(parsed)
        if message:
            return _clean_text(message)
    message_match = re.search(r"['\"]message['\"]\s*:\s*['\"](.+?)['\"](?=[,}])", raw_text)
    if message_match:
        return _clean_text(message_match.group(1))
    return _clean_text(raw_text)


def _payload_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    if " - " in stripped:
        tail = stripped.split(" - ", 1)[1].strip()
        if tail:
            candidates.append(tail)
    if ": " in stripped:
        tail = stripped.split(": ", 1)[1].strip()
        if tail and (tail.startswith("{") or tail.startswith("[")):
            candidates.append(tail)
    match = re.search(r"([\[{].*[\]}])", stripped)
    if match:
        candidates.append(match.group(1).strip())

    deduped: list[str] = []
    for item in candidates:
        if item and item not in deduped:
            deduped.append(item)
    return deduped


def _parse_payload(text: str):
    stripped = str(text or "").strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        return json.loads(stripped)
    except Exception:
        pass
    try:
        return ast.literal_eval(stripped)
    except Exception:
        return None


def _find_message(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("message", "detail", "error_description", "description"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
            found = _find_message(nested)
            if found:
                return found
        for key in ("error", "errors"):
            found = _find_message(value.get(key))
            if found:
                return found
        for nested in value.values():
            found = _find_message(nested)
            if found:
                return found
        return ""
    if isinstance(value, list):
        for item in value:
            found = _find_message(item)
            if found:
                return found
        return ""
    return ""


def _extract_parameter_name(text: str) -> str | None:
    patterns = (
        r"unsupported parameter:\s*['\"]?([a-zA-Z0-9_.-]+)['\"]?",
        r"unknown name\s*['\"]?([a-zA-Z0-9_.-]+)['\"]?",
        r"['\"]([a-zA-Z0-9_.-]+)['\"]\s+is not supported",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None
