from __future__ import annotations

import pytest

from src.utils.qwen_endpoints import (
    QWEN_TOKYO_COMPATIBLE_MODE_PATH,
    QWEN_TOKYO_DASHSCOPE_PATH,
    is_qwen_tokyo_workspace_base_url,
    is_qwen_tokyo_workspace_host,
    is_qwen_translation_api_host,
    require_qwen_tokyo_workspace_base_url,
)


TOKYO_HOST = "ws-player.ap-northeast-1.maas.aliyuncs.com"


def test_qwen_tokyo_workspace_urls_require_the_official_host_and_path() -> None:
    compatible_url = f"https://{TOKYO_HOST}{QWEN_TOKYO_COMPATIBLE_MODE_PATH}"
    dashscope_url = f"https://{TOKYO_HOST}{QWEN_TOKYO_DASHSCOPE_PATH}"

    assert is_qwen_tokyo_workspace_host(TOKYO_HOST)
    assert is_qwen_tokyo_workspace_base_url(
        compatible_url,
        endpoint_path=QWEN_TOKYO_COMPATIBLE_MODE_PATH,
    )
    assert is_qwen_tokyo_workspace_base_url(
        dashscope_url,
        endpoint_path=QWEN_TOKYO_DASHSCOPE_PATH,
    )
    assert (
        require_qwen_tokyo_workspace_base_url(
            compatible_url + "/",
            endpoint_path=QWEN_TOKYO_COMPATIBLE_MODE_PATH,
            label="Qwen Tokyo workspace API",
        )
        == compatible_url
    )


@pytest.mark.parametrize(
    "candidate",
    (
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "https://ws-player.ap-northeast-1.maas.aliyuncs.com.evil.test/compatible-mode/v1",
        "https://a.b.ap-northeast-1.maas.aliyuncs.com/compatible-mode/v1",
        "http://ws-player.ap-northeast-1.maas.aliyuncs.com/compatible-mode/v1",
        "https://ws-player.ap-northeast-1.maas.aliyuncs.com/api/v1",
        "https://ws-player.ap-northeast-1.maas.aliyuncs.com/compatible-mode/v1?token=1",
    ),
)
def test_qwen_tokyo_workspace_urls_reject_shared_hosts_and_lookalikes(
    candidate: str,
) -> None:
    assert not is_qwen_tokyo_workspace_base_url(
        candidate,
        endpoint_path=QWEN_TOKYO_COMPATIBLE_MODE_PATH,
    )
    with pytest.raises(ValueError, match="Tokyo workspace endpoint"):
        require_qwen_tokyo_workspace_base_url(
            candidate,
            endpoint_path=QWEN_TOKYO_COMPATIBLE_MODE_PATH,
            label="Qwen Tokyo workspace API",
        )


def test_qwen_translation_host_classifier_gates_tokyo_to_qianwen_provider() -> None:
    assert is_qwen_translation_api_host("dashscope-intl.aliyuncs.com")
    assert is_qwen_translation_api_host(TOKYO_HOST, provider_id="qianwen")
    assert not is_qwen_translation_api_host(TOKYO_HOST, provider_id="openai_compatible")
    assert not is_qwen_translation_api_host(
        f"{TOKYO_HOST}.evil.test",
        provider_id="qianwen",
    )
