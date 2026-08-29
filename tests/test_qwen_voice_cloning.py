from __future__ import annotations

import base64
import io
import json
import wave

import numpy as np
import pytest

from src.core import voice_clone_service
from src.tts import qwen_voice_enrollment as enrollment
from src.tts.api_tts_config import (
    QWEN_VC_DEFAULT_MODEL,
    get_cloned_voice_options,
    get_tts_api_default_config,
    normalize_cloned_voices,
    normalize_tts_api_region,
    resolve_tts_api_config,
)
from src.tts.api_tts_engines import QwenVoiceCloneTTS


def _wav_bytes(duration_s: float = 12.0, sample_rate: int = 24000) -> bytes:
    samples = np.sin(
        np.linspace(0.0, np.pi * 2.0 * 220.0 * duration_s, int(sample_rate * duration_s))
    )
    pcm = (samples * 0.5 * 32767.0).astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return out.getvalue()


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.status_code = status_code
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = {"content-type": "application/json"}
        self.closed = False

    def iter_content(self, chunk_size: int = 1, *_args, **_kwargs):
        yield self._body

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.requests: list[tuple[str, dict, dict]] = []

    def post(self, url, headers=None, json=None, **_kwargs):
        self.requests.append((url, dict(headers or {}), dict(json or {})))
        return _FakeResponse(self.payload, self.status_code)

    def mount(self, *_args, **_kwargs):
        pass

    def close(self):
        pass


def _client(monkeypatch, payload, status_code=200):
    client = enrollment.QwenVoiceEnrollmentClient(
        api_key="sk-test",
        base_url="https://dashscope-intl.aliyuncs.com/api/v1",
    )
    session = _FakeSession(payload, status_code)
    monkeypatch.setattr(client, "_get_session", lambda: session)
    return client, session


def test_enrollment_posts_base64_audio_to_the_customization_endpoint(monkeypatch):
    client, session = _client(
        monkeypatch,
        {"output": {"voice": "cosy-mio-1234", "target_model": QWEN_VC_DEFAULT_MODEL}},
    )

    voice = client.create_voice(
        _wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL, display_name="我的声音"
    )

    url, headers, payload = session.requests[0]
    assert url.endswith("/services/audio/tts/customization")
    assert headers["Authorization"] == "Bearer sk-test"
    assert payload["model"] == "qwen-voice-enrollment"
    assert payload["input"]["action"] == "create"
    assert payload["input"]["target_model"] == QWEN_VC_DEFAULT_MODEL
    data_url = payload["input"]["audio"]["data"]
    assert data_url.startswith("data:audio/wav;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1])[:4] == b"RIFF"
    assert voice.voice_id == "cosy-mio-1234"
    # The readable label stays local; the service only gets the ASCII prefix.
    assert voice.display_name == "我的声音"


def test_enrollment_name_is_reduced_to_the_accepted_charset():
    assert enrollment.safe_preferred_name("My Voice!") == "MyVoice"
    assert enrollment.safe_preferred_name("a" * 40) == "a" * 16
    generated = enrollment.safe_preferred_name("我的声音")
    assert generated.isascii() and generated.isidentifier()
    assert enrollment.safe_preferred_name("123voice").startswith("v")


def test_enrollment_rejects_audio_that_the_service_would_refuse():
    with pytest.raises(enrollment.QwenVoiceEnrollmentError):
        enrollment._audio_data_url(b"")
    with pytest.raises(enrollment.QwenVoiceEnrollmentError):
        enrollment._audio_data_url(b"not audio at all")
    with pytest.raises(enrollment.QwenVoiceEnrollmentError):
        enrollment._audio_data_url(b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * (11 * 1024 * 1024))


def test_enrollment_surfaces_authentication_failures(monkeypatch):
    client, _session = _client(
        monkeypatch,
        {"code": "InvalidApiKey", "message": "invalid key"},
        status_code=401,
    )

    with pytest.raises(enrollment.QwenVoiceEnrollmentError) as excinfo:
        client.create_voice(_wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL)

    message = str(excinfo.value)
    assert "API key" in message
    assert "InvalidApiKey" in message


def test_enrollment_lists_and_deletes_voices(monkeypatch):
    client, session = _client(
        monkeypatch,
        {
            "output": {
                "voice_list": [
                    {
                        "voice": "voice-a",
                        "gmt_create": "2026-08-01 09:00:00",
                        "target_model": QWEN_VC_DEFAULT_MODEL,
                        "language": "en",
                    },
                    {"not_a_voice": True},
                ]
            }
        },
    )

    voices = client.list_voices()
    assert [v.voice_id for v in voices] == ["voice-a"]
    assert voices[0].created_at == "2026-08-01 09:00:00"
    assert session.requests[0][2]["input"]["action"] == "list"

    client.delete_voice("voice-a")
    assert session.requests[1][2]["input"] == {"action": "delete", "voice": "voice-a"}


def test_enrollment_requires_a_usable_api_key():
    client = enrollment.QwenVoiceEnrollmentClient(
        api_key="dpapi:v1:still-encrypted",
        base_url="https://dashscope-intl.aliyuncs.com/api/v1",
    )
    with pytest.raises(enrollment.QwenVoiceEnrollmentError):
        client.create_voice(_wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL)


def test_cloning_config_defaults_to_the_shared_singapore_endpoint():
    defaults = get_tts_api_default_config("qwen_vc")
    assert defaults["model"] == QWEN_VC_DEFAULT_MODEL
    assert defaults["base_url"] == "https://dashscope-intl.aliyuncs.com/api/v1"
    assert defaults["upload_consent"] is False
    assert defaults["custom_voices"] == []


def test_cloning_region_falls_back_when_tokyo_is_selected():
    # Tokyo has no enrollment endpoint, so it must not leave the base URL blank.
    assert normalize_tts_api_region("qwen_vc", "japan") == "singapore"
    resolved = resolve_tts_api_config("qwen_vc", {"region": "japan"})
    assert resolved["base_url"] == "https://dashscope-intl.aliyuncs.com/api/v1"


def test_stored_cloned_voices_drop_malformed_entries():
    voices = normalize_cloned_voices(
        [
            {"voice_id": "a", "display_name": "A"},
            {"display_name": "no id"},
            {"voice": "b"},
            {"voice_id": "a", "display_name": "duplicate"},
            "not a mapping",
        ]
    )
    assert [v["voice_id"] for v in voices] == ["a", "b"]

    options = get_cloned_voice_options({"custom_voices": voices})
    assert [option[0] for option in options] == ["a", "b"]
    assert options[1][1] == "b"  # falls back to the id when unnamed


def _clone_config(**overrides):
    engine_cfg = {
        "api_key": "sk-test",
        "base_url": "https://dashscope-intl.aliyuncs.com/api/v1",
        "model": QWEN_VC_DEFAULT_MODEL,
        "voice": "voice-a",
        "upload_consent": True,
        "custom_voices": [{"voice_id": "voice-a", "display_name": "Mine"}],
    }
    engine_cfg.update(overrides)
    return {"tts": {"engine": "qwen_vc", "qwen_vc": engine_cfg}}


def test_engine_reports_unavailable_until_a_voice_exists():
    without = QwenVoiceCloneTTS(config=_clone_config(voice="", custom_voices=[])["tts"]["qwen_vc"])
    assert without.is_available() is False

    with_voice = QwenVoiceCloneTTS(config=_clone_config()["tts"]["qwen_vc"])
    assert with_voice.is_available() is True
    assert [voice.id for voice in with_voice.get_available_voices()] == ["voice-a"]


def test_engine_refuses_to_synthesize_without_a_cloned_voice():
    engine = QwenVoiceCloneTTS(config=_clone_config(voice="", custom_voices=[])["tts"]["qwen_vc"])
    with pytest.raises(RuntimeError, match="no cloned voice"):
        engine.synthesize("hello", "")


def test_service_refuses_to_upload_without_consent(monkeypatch):
    config = _clone_config(upload_consent=False)
    monkeypatch.setattr(
        voice_clone_service,
        "_client",
        lambda _cfg: pytest.fail("consent must be checked before any upload"),
    )
    with pytest.raises(voice_clone_service.VoiceCloneConsentRequiredError):
        voice_clone_service.create_voice(config, _wav_bytes(), "Mine")


def test_service_requires_an_api_key(monkeypatch):
    config = _clone_config(api_key="")
    with pytest.raises(voice_clone_service.VoiceCloneNotConfiguredError):
        voice_clone_service.create_voice(config, _wav_bytes(), "Mine")


def test_service_records_a_new_voice_and_selects_it(monkeypatch):
    config = _clone_config()

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def create_voice(self, _audio, *, target_model, display_name):
            return enrollment.ClonedVoice(
                voice_id="voice-b",
                display_name=display_name,
                target_model=target_model,
            )

    monkeypatch.setattr(voice_clone_service, "_client", lambda _cfg: FakeClient())

    voice = voice_clone_service.create_voice(config, _wav_bytes(), "Second")

    stored = config["tts"]["qwen_vc"]
    assert voice.voice_id == "voice-b"
    assert [v["voice_id"] for v in stored["custom_voices"]] == ["voice-a", "voice-b"]
    assert stored["voice"] == "voice-b"


def test_service_deletes_locally_even_when_the_service_rejects_it(monkeypatch):
    config = _clone_config()

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def delete_voice(self, _voice_id):
            raise enrollment.QwenVoiceEnrollmentError("voice already gone")

    monkeypatch.setattr(voice_clone_service, "_client", lambda _cfg: FakeClient())

    voice_clone_service.delete_voice(config, "voice-a")

    stored = config["tts"]["qwen_vc"]
    assert stored["custom_voices"] == []
    assert stored["voice"] == ""


def test_service_refresh_keeps_local_names_and_filters_other_models(monkeypatch):
    config = _clone_config()

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def list_voices(self):
            return [
                enrollment.ClonedVoice(
                    voice_id="voice-a", target_model=QWEN_VC_DEFAULT_MODEL
                ),
                enrollment.ClonedVoice(
                    voice_id="voice-other", target_model="qwen3-tts-vc-realtime-x"
                ),
            ]

    monkeypatch.setattr(voice_clone_service, "_client", lambda _cfg: FakeClient())

    voices = voice_clone_service.refresh_voices(config)

    assert [v["voice_id"] for v in voices] == ["voice-a"]
    assert voices[0]["display_name"] == "Mine"


def test_enrollment_error_carries_status_and_provider_code(monkeypatch):
    """A failed enrollment must be identifiable from the log alone."""
    from src.utils.provider_diagnostics import safe_exception_summary

    client, _session = _client(
        monkeypatch,
        {"code": "InvalidParameter", "message": "target_model is not supported"},
        status_code=400,
    )

    with pytest.raises(enrollment.QwenVoiceEnrollmentError) as excinfo:
        client.create_voice(_wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL)

    error = excinfo.value
    assert error.status_code == 400
    assert error.provider_code == "InvalidParameter"
    summary = safe_exception_summary(error)
    assert "status=400" in summary
    assert "code=InvalidParameter" in summary


def test_enrollment_error_code_is_bounded():
    from src.tts.qwen_voice_enrollment import _provider_code

    assert _provider_code({"code": "x" * 200}) == "x" * 64
    assert _provider_code({"error_code": " Throttling "}) == "Throttling"
    assert _provider_code({"message": "no code here"}) == ""


def test_enrollment_refuses_compressed_responses_up_front(monkeypatch):
    """The bounded reader rejects gzip, so the request must opt out of it.

    Without this header DashScope gzips the reply and every enrollment fails
    with an opaque local error regardless of whether the key is valid.
    """

    client, session = _client(
        monkeypatch,
        {"output": {"voice": "voice-x", "target_model": QWEN_VC_DEFAULT_MODEL}},
    )

    client.create_voice(_wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL)

    _url, headers, _payload = session.requests[0]
    assert headers["Accept-Encoding"] == "identity"


def test_region_support_predicate_matches_the_published_endpoints():
    from src.tts.api_tts_config import region_supports_voice_cloning

    assert region_supports_voice_cloning("singapore") is True
    assert region_supports_voice_cloning("china_mainland") is True
    assert region_supports_voice_cloning("cn") is True
    # Tokyo is a workspace-scoped region with no enrollment endpoint.
    assert region_supports_voice_cloning("japan") is False
    assert region_supports_voice_cloning("jp") is False
    assert region_supports_voice_cloning("custom") is False


def test_tokyo_qwen_tts_key_is_not_copied_into_the_cloning_config():
    """A Tokyo key only authenticates in Tokyo, where cloning does not exist."""
    from src.utils import config_manager

    config = {
        "tts": {
            "qwen_tts": {"api_key": "sk-tokyo", "region": "japan"},
            "qwen_vc": {"api_key": ""},
        }
    }
    config_manager._ensure_tts_config(config)
    assert config["tts"]["qwen_vc"]["api_key"] == ""

    shared = {
        "tts": {
            "qwen_tts": {"api_key": "sk-beijing", "region": "china_mainland"},
            "qwen_vc": {"api_key": ""},
        }
    }
    config_manager._ensure_tts_config(shared)
    assert shared["tts"]["qwen_vc"]["api_key"] == "sk-beijing"
