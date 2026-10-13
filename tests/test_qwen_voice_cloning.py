"""Cloud voice cloning: DashScope voice-enrollment, its config and the engine.

Alibaba retired qwen-voice-enrollment and the qwen3-tts-vc models on
2026-10-10. Enrollment now goes through ``voice-enrollment``: the recording is
put into Model Studio temporary storage (China (Beijing) only), the oss:// URL
is enrolled for a Qwen-Audio-TTS model, the new voice is reviewed, and it
speaks through the SpeechSynthesizer service. Every request below goes to a
scripted fake session; nothing here touches the network.
"""

from __future__ import annotations

import copy
import io
import json
import re
import time
import types
import wave

import numpy as np
import pytest
import requests
from urllib3.util import parse_url

from src.core import voice_clone_service
from src.tts import api_tts_engines
from src.tts import qwen_voice_enrollment as enrollment
from src.tts.api_tts_config import (
    QWEN_TTS_BASE_URL_INTERNATIONAL,
    QWEN_TTS_BASE_URL_MAINLAND,
    QWEN_VC_DEFAULT_MODEL,
    QWEN_VC_MODEL_OPTIONS,
    cloned_voice_is_usable,
    cloned_voice_survives_retirement,
    get_cloned_voice_options,
    get_tts_api_default_config,
    get_tts_api_known_base_urls,
    get_tts_api_region_options,
    normalize_cloned_voices,
    normalize_tts_api_region,
    qwen_vc_model_is_supported,
    region_supports_voice_cloning,
    resolve_tts_api_config,
    target_model_from_voice_id,
    tts_api_region_from_base_url,
    usable_cloned_voice_options,
)
from src.tts.api_tts_engines import QwenTTS, QwenVoiceCloneTTS

BEIJING = QWEN_TTS_BASE_URL_MAINLAND
CUSTOMIZATION_URL = f"{BEIJING}/services/audio/tts/customization"
SYNTHESIZER_URL = f"{BEIJING}/services/audio/tts/SpeechSynthesizer"
MULTIMODAL_URL = f"{BEIJING}/services/aigc/multimodal-generation/generation"
WORKSPACE_URL = "https://ws-mio0test.cn-beijing.maas.aliyuncs.com/api/v1"
MODEL_31 = "qwen-audio-3.1-tts-flash"
VOICE_ID = "qwen-audio-3.0-tts-flash-mine-0a1b2c"
VOICE_31 = "qwen-audio-3.1-tts-flash-mine-3d4e5f"
UPLOAD_DIR = "dashscope-instant/acct123/2026-10-13/abc"
UPLOAD_HOST = "https://dashscope-file-mgr.oss-cn-beijing.aliyuncs.com"
POLICY = {
    "request_id": "req-policy",
    "data": {
        "policy": "eyJleHBpcmF0aW9uIjoiMjAyNi0xMC0xM1QxMjowMDowMFoifQ==",
        "signature": "c2lnbmF0dXJl",
        "upload_dir": UPLOAD_DIR,
        "upload_host": UPLOAD_HOST,
        "expire_in_seconds": 300,
        "max_file_size_mb": 100,
        "capacity_limit_mb": 999999999,
        "oss_access_key_id": "LTAI-test-key-id",
        "x_oss_object_acl": "private",
        "x_oss_forbid_overwrite": "true",
    },
}
OSS_FIELD_ORDER = [
    "OSSAccessKeyId",
    "Signature",
    "policy",
    "x-oss-object-acl",
    "x-oss-forbid-overwrite",
    "key",
    "success_action_status",
    "file",
]


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
    """A streamed requests response: a JSON body, raw bytes or a broken stream."""

    def __init__(
        self,
        payload=None,
        status_code: int = 200,
        *,
        body: bytes | None = None,
        headers: dict | None = None,
        broken: bool = False,
    ) -> None:
        self.status_code = status_code
        if body is None:
            body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self._body = body
        self.headers = {"content-type": "application/json", **(headers or {})}
        self.broken = broken
        self.closed = False

    def iter_content(self, chunk_size: int = 1, *_args, **_kwargs):
        if self.broken:
            # What requests raises when the connection drops mid-body.
            raise requests.exceptions.ChunkedEncodingError(
                "Connection broken: IncompleteRead(0 bytes read)"
            )
        if self._body:
            yield self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def close(self) -> None:
        self.closed = True


class _EnrollmentSession:
    """Scripted stand-in for the requests session the enrollment client opens.

    ``actions`` maps a customization action to the replies it gets, served in
    order (the last one repeats): a payload, a ``(payload, status)`` pair or a
    ready ``_FakeResponse``.
    """

    def __init__(
        self,
        *,
        policy=None,
        policy_status: int = 200,
        upload_status: int = 200,
        upload_error: Exception | None = None,
        actions: dict | None = None,
    ) -> None:
        self.policy = copy.deepcopy(POLICY) if policy is None else policy
        self.policy_status = policy_status
        self.upload_status = upload_status
        self.upload_error = upload_error
        self.actions = {name: list(items) for name, items in (actions or {}).items()}
        self.calls: list[dict] = []
        self.closed = False

    def get(self, url, headers=None, params=None, **kwargs):
        self.calls.append(
            {
                "method": "GET",
                "url": url,
                "headers": dict(headers or {}),
                "params": dict(params or {}),
                "kwargs": kwargs,
            }
        )
        return _FakeResponse(self.policy, self.policy_status)

    def post(self, url, headers=None, json=None, files=None, **kwargs):
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "headers": dict(headers or {}),
                "json": copy.deepcopy(json),
                "files": files,
                "kwargs": kwargs,
            }
        )
        if files is not None:
            if self.upload_error is not None:
                raise self.upload_error
            return _FakeResponse(
                status_code=self.upload_status,
                body=b"",
                headers={"content-type": "text/plain"},
            )
        script = self.actions.get(json["input"]["action"]) or [{"output": {}}]
        reply = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(reply, _FakeResponse):
            return reply
        if isinstance(reply, tuple):
            return _FakeResponse(reply[0], reply[1])
        return _FakeResponse(reply)

    def mount(self, *_args, **_kwargs):
        pass

    def close(self):
        self.closed = True

    def action_calls(self, action: str | None = None) -> list[dict]:
        return [
            call
            for call in self.calls
            if call["method"] == "POST"
            and call["json"] is not None
            and (action is None or call["json"]["input"]["action"] == action)
        ]

    def uploads(self) -> list[dict]:
        return [call for call in self.calls if call.get("files") is not None]


def _client(session: _EnrollmentSession | None = None, *, api_key: str = "sk-test"):
    client = enrollment.QwenVoiceEnrollmentClient(api_key=api_key, base_url=BEIJING)
    client._session = session if session is not None else _EnrollmentSession()
    return client


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    """Drive the review wait with a fake clock instead of real sleeps."""

    fake = _Clock()
    monkeypatch.setattr(
        enrollment,
        "time",
        types.SimpleNamespace(
            monotonic=fake.monotonic,
            time=time.time,
            strftime=time.strftime,
            sleep=fake.sleep,
        ),
    )
    return fake


def _clone_config(**overrides):
    engine_cfg = {
        "api_key": "sk-test",
        "region": "china_mainland",
        "base_url": BEIJING,
        "model": QWEN_VC_DEFAULT_MODEL,
        "voice": VOICE_ID,
        "upload_consent": True,
        "custom_voices": [
            {
                "voice_id": VOICE_ID,
                "display_name": "Mine",
                "target_model": QWEN_VC_DEFAULT_MODEL,
                "region": "china_mainland",
                "status": "OK",
            }
        ],
    }
    engine_cfg.update(overrides)
    return {
        "translation": {"source_language": "zh"},
        "tts": {"engine": "qwen_vc", "qwen_vc": engine_cfg},
    }


class _ServiceClient:
    """Fake enrollment client for voice_clone_service tests."""

    def __init__(self, *, remote=(), created=None, delete_error=None) -> None:
        self.remote = list(remote)
        self.created = created
        self.delete_error = delete_error
        self.create_calls: list[dict] = []
        self.deleted: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def create_voice(self, audio, *, target_model, display_name="", language=""):
        self.create_calls.append(
            {
                "audio": audio,
                "target_model": target_model,
                "display_name": display_name,
                "language": language,
            }
        )
        if self.created is not None:
            return self.created
        return enrollment.ClonedVoice(
            voice_id="qwen-audio-3.0-tts-flash-new-9z8y7x",
            display_name=display_name,
            target_model=target_model,
            language=language,
            status=enrollment.VOICE_STATUS_DEPLOYING,
        )

    def delete_voice(self, voice_id):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(voice_id)

    def list_all_voices(self):
        return [enrollment.ClonedVoice.from_dict(item) for item in self.remote]


def _use_service_client(monkeypatch, client: _ServiceClient) -> _ServiceClient:
    monkeypatch.setattr(voice_clone_service, "_client", lambda _cfg: client)
    return client


# ---------------------------------------------------------------------------
# Temporary storage upload
# ---------------------------------------------------------------------------


def test_upload_requests_a_policy_then_posts_the_recording_to_temporary_storage():
    session = _EnrollmentSession()
    audio = _wav_bytes()

    url = _client(session).upload_reference_audio(audio)

    policy_call, upload_call = session.calls
    assert policy_call["method"] == "GET"
    assert policy_call["url"] == f"{BEIJING}/uploads"
    assert policy_call["params"] == {"action": "getPolicy", "model": "voice-enrollment"}
    assert policy_call["headers"]["Authorization"] == "Bearer sk-test"
    assert policy_call["headers"]["Accept-Encoding"] == "identity"
    assert policy_call["kwargs"]["allow_redirects"] is False
    assert policy_call["kwargs"]["stream"] is True

    assert upload_call["method"] == "POST"
    assert upload_call["url"] == UPLOAD_HOST
    # The signed policy authorizes the upload; the API key never goes to OSS.
    assert "Authorization" not in upload_call["headers"]
    assert upload_call["kwargs"]["allow_redirects"] is False
    files = upload_call["files"]
    assert list(files) == OSS_FIELD_ORDER
    assert files["OSSAccessKeyId"] == (None, "LTAI-test-key-id")
    assert files["Signature"] == (None, "c2lnbmF0dXJl")
    assert files["policy"] == (None, POLICY["data"]["policy"])
    assert files["x-oss-object-acl"] == (None, "private")
    assert files["x-oss-forbid-overwrite"] == (None, "true")
    assert files["success_action_status"] == (None, "200")
    assert files["file"] == ("reference.wav", audio)
    key = files["key"][1]
    assert re.fullmatch(rf"{re.escape(UPLOAD_DIR)}/mio-reference-\d+\.wav", key)
    assert url == f"oss://{key}"


def test_upload_encodes_the_recording_as_the_last_multipart_field():
    """OSS ignores every form field that follows the file."""

    session = _EnrollmentSession()
    _client(session).upload_reference_audio(_wav_bytes(duration_s=6.0))
    upload_call = session.uploads()[0]

    prepared = requests.Request("POST", upload_call["url"], files=upload_call["files"]).prepare()
    names = re.findall(rb'form-data; name="([^"]+)"', prepared.body)

    assert [name.decode() for name in names] == OSS_FIELD_ORDER


@pytest.mark.parametrize(
    ("audio", "extension"),
    [
        pytest.param(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64, "mp3", id="mp3"),
        pytest.param(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 64, "m4a", id="m4a"),
    ],
)
def test_upload_names_the_object_after_the_detected_format(audio, extension):
    session = _EnrollmentSession()

    url = _client(session).upload_reference_audio(audio)

    files = session.uploads()[0]["files"]
    assert files["file"] == (f"reference.{extension}", audio)
    assert url.endswith(f".{extension}")


def _policy_with(**changes):
    policy = copy.deepcopy(POLICY)
    for name, value in changes.items():
        if value is None:
            policy["data"].pop(name, None)
        else:
            policy["data"][name] = value
    return policy


# Upload hosts that must never receive the recording and the signed OSS form
# fields. urllib3, which requests connects with, ends a host at a backslash
# where urlsplit keeps it, so the backslash spellings looked like aliyuncs.com
# hosts to a urlsplit suffix check but connected to evil.com.
_UNTRUSTED_UPLOAD_HOSTS = {
    "backslash-suffix": "https://evil.com\\.aliyuncs.com",
    "backslash-oss-host": "https://evil.com\\x.oss-cn-beijing.aliyuncs.com",
    "backslash-at": "https://evil.com\\@x.aliyuncs.com",
    "backslash-at-reversed": "https://x.aliyuncs.com\\@evil.com",
    "percent-slash": "https://evil.com%2F.aliyuncs.com",
    "percent-backslash": "https://evil.com%5C.aliyuncs.com",
    "percent-at": "https://evil.com%40x.aliyuncs.com",
    "percent-dot": "https://evil.com%2Ex.aliyuncs.com",
    "userinfo-oss-name": "https://x.aliyuncs.com@evil.com",
    "userinfo-evil-name": "https://evil.com@x.aliyuncs.com",
    "empty-userinfo": "https://@x.aliyuncs.com",
    "credentials": "https://user:secret@dashscope-file.oss-cn-beijing.aliyuncs.com",
    "fragment": "https://evil.com#.aliyuncs.com",
    "query": "https://evil.com?.aliyuncs.com",
    "tab": "https://evil.com\t.aliyuncs.com",
    "space": "https://evil.com .aliyuncs.com",
    "semicolon": "https://evil.com;.aliyuncs.com",
    "ideographic-full-stop": "https://evil。com.aliyuncs.com",
    "kelvin-sign": "https://evilK.aliyuncs.com",
    "empty-label": "https://.aliyuncs.com",
    "double-dot": "https://x..aliyuncs.com",
    "bare-domain": "https://aliyuncs.com",
    "bad-port": "https://x.aliyuncs.com:abc",
    "plain-http": "http://dashscope-file-mgr.oss-cn-beijing.aliyuncs.com",
    "other-domain": "https://evil.example.com",
    "suffix-as-label": "https://oss-cn-beijing.aliyuncs.com.evil.example",
}


@pytest.mark.parametrize(
    "upload_host",
    [pytest.param(url, id=name) for name, url in _UNTRUSTED_UPLOAD_HOSTS.items()],
)
def test_upload_refuses_an_untrusted_host_without_uploading(upload_host):
    session = _EnrollmentSession(policy=_policy_with(upload_host=upload_host))

    with pytest.raises(enrollment.QwenVoiceEnrollmentError, match="China \\(Beijing\\)"):
        _client(session).upload_reference_audio(_wav_bytes())

    assert [call["method"] for call in session.calls] == ["GET"]
    assert session.uploads() == []
    assert enrollment._trusted_upload_url(upload_host) == ""


@pytest.mark.parametrize(
    ("upload_host", "posted_url"),
    [
        pytest.param(UPLOAD_HOST, UPLOAD_HOST, id="as-issued"),
        pytest.param(
            "https://DashScope-File-Mgr.OSS-CN-BEIJING.aliyuncs.com.:443/upload?x=1#top",
            "https://dashscope-file-mgr.oss-cn-beijing.aliyuncs.com:443/upload",
            id="rebuilt-from-checked-parts",
        ),
    ],
)
def test_upload_posts_to_the_url_rebuilt_from_the_checked_host(upload_host, posted_url):
    session = _EnrollmentSession(policy=_policy_with(upload_host=upload_host))

    _client(session).upload_reference_audio(_wav_bytes(duration_s=6.0))

    [upload_call] = session.uploads()
    assert upload_call["url"] == posted_url
    # requests connects to exactly the host that was checked.
    prepared = requests.Request("POST", upload_call["url"], files=upload_call["files"]).prepare()
    assert parse_url(prepared.url).host == "dashscope-file-mgr.oss-cn-beijing.aliyuncs.com"


@pytest.mark.parametrize(
    "policy",
    [
        _policy_with(upload_host=None),
        _policy_with(upload_dir=None),
        _policy_with(signature=None),
        _policy_with(policy=""),
        _policy_with(oss_access_key_id=None),
        {"request_id": "req", "data": "not a mapping"},
        {"request_id": "req"},
    ],
)
def test_upload_refuses_an_incomplete_policy_without_uploading(policy):
    session = _EnrollmentSession(policy=policy)

    with pytest.raises(enrollment.QwenVoiceEnrollmentError, match="China \\(Beijing\\)"):
        _client(session).upload_reference_audio(_wav_bytes())

    assert [call["method"] for call in session.calls] == ["GET"]
    assert session.uploads() == []


def test_upload_reports_a_rejected_storage_upload():
    session = _EnrollmentSession(upload_status=403)

    with pytest.raises(enrollment.QwenVoiceEnrollmentError) as excinfo:
        _client(session).upload_reference_audio(_wav_bytes())

    assert str(excinfo.value) == "Uploading the reference recording failed (HTTP 403)"
    assert excinfo.value.status_code == 403


def test_upload_reports_an_unreachable_storage_host():
    session = _EnrollmentSession(upload_error=requests.ConnectionError("connection reset"))

    with pytest.raises(enrollment.QwenVoiceEnrollmentError, match="Could not upload"):
        _client(session).upload_reference_audio(_wav_bytes())


def test_upload_policy_rejection_carries_the_provider_code():
    session = _EnrollmentSession(
        policy={"code": "InvalidApiKey", "message": "Invalid API-key provided."},
        policy_status=401,
    )

    with pytest.raises(enrollment.QwenVoiceEnrollmentError) as excinfo:
        _client(session).upload_reference_audio(_wav_bytes())

    assert excinfo.value.status_code == 401
    assert excinfo.value.provider_code == "InvalidApiKey"
    assert session.uploads() == []


@pytest.mark.parametrize(
    "audio",
    [
        # Explicit ids: pytest would otherwise spell the bytes out in the test
        # id, and an 11 MB id overflows the PYTEST_CURRENT_TEST variable.
        pytest.param(b"", id="empty"),
        pytest.param(b"not audio at all", id="not-audio"),
        pytest.param(
            b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * (11 * 1024 * 1024),
            id="over-10-mb",
        ),
    ],
)
def test_enrollment_rejects_audio_the_service_would_refuse_before_any_request(audio):
    session = _EnrollmentSession()

    with pytest.raises(enrollment.QwenVoiceEnrollmentError):
        _client(session).upload_reference_audio(audio)

    assert session.calls == []


@pytest.mark.parametrize(
    "api_key",
    ["", "dpapi:v1:still-encrypted", "sk-\ntest"],
    ids=["missing", "still-encrypted", "control-character"],
)
def test_enrollment_requires_a_usable_api_key(api_key):
    session = _EnrollmentSession()

    with pytest.raises(enrollment.QwenVoiceEnrollmentError):
        _client(session, api_key=api_key).create_voice(
            _wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL, wait_for_review=False
        )

    assert session.calls == []


def test_reference_requirements_match_the_service_limits():
    requirements = enrollment.reference_audio_requirements()

    assert requirements["min_seconds"] == 5.0
    assert requirements["min_sample_rate"] == 16000
    assert requirements["max_bytes"] == 10 * 1024 * 1024
    assert requirements["channels"] == 1


# ---------------------------------------------------------------------------
# Enrollment and review
# ---------------------------------------------------------------------------


def test_create_voice_enrolls_the_uploaded_url_and_waits_for_the_review(clock):
    session = _EnrollmentSession(
        actions={
            "create_voice": [{"output": {"voice_id": VOICE_ID}, "usage": {"count": 1}}],
            "query_voice": [
                {"output": {"status": "DEPLOYING"}},
                {"output": {"status": "OK"}},
            ],
        }
    )

    voice = _client(session).create_voice(
        _wav_bytes(),
        target_model=QWEN_VC_DEFAULT_MODEL,
        display_name="我的声音",
        language="ZH",
        sleep=clock.sleep,
    )

    create_call = session.action_calls("create_voice")[0]
    assert create_call["url"] == CUSTOMIZATION_URL
    assert create_call["headers"]["Authorization"] == "Bearer sk-test"
    assert create_call["headers"]["X-DashScope-OssResourceResolve"] == "enable"
    assert create_call["headers"]["Accept-Encoding"] == "identity"
    assert create_call["headers"]["Content-Type"] == "application/json"
    key = session.uploads()[0]["files"]["key"][1]
    payload = create_call["json"]
    prefix = payload["input"].pop("prefix")
    assert payload == {
        "model": "voice-enrollment",
        "input": {
            "action": "create_voice",
            "target_model": QWEN_VC_DEFAULT_MODEL,
            "url": f"oss://{key}",
            "language_hints": ["zh"],
        },
    }
    # A non-Latin name collapses to a generated ASCII prefix; the readable
    # label stays local.
    assert re.fullmatch(r"mio\d{1,7}", prefix)

    queries = session.action_calls("query_voice")
    assert [call["json"] for call in queries] == [
        {"model": "voice-enrollment", "input": {"action": "query_voice", "voice_id": VOICE_ID}}
    ] * 2
    # Only the enrollment itself resolves an oss:// resource.
    assert all("X-DashScope-OssResourceResolve" not in call["headers"] for call in queries)
    assert clock.sleeps == [2.0]

    assert voice.voice_id == VOICE_ID
    assert voice.display_name == "我的声音"
    assert voice.target_model == QWEN_VC_DEFAULT_MODEL
    assert voice.language == "zh"
    assert voice.status == enrollment.VOICE_STATUS_OK


def test_create_voice_without_a_language_or_review_wait():
    session = _EnrollmentSession(actions={"create_voice": [{"output": {"voice_id": VOICE_ID}}]})

    voice = _client(session).create_voice(
        _wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL, wait_for_review=False
    )

    payload = session.action_calls("create_voice")[0]["json"]
    assert "language_hints" not in payload["input"]
    assert session.action_calls("query_voice") == []
    assert voice.status == enrollment.VOICE_STATUS_DEPLOYING


def test_create_voice_requires_a_target_model_before_uploading():
    session = _EnrollmentSession()

    with pytest.raises(enrollment.QwenVoiceEnrollmentError, match="target synthesis model"):
        _client(session).create_voice(_wav_bytes(), target_model=" ")

    assert session.calls == []


def test_create_voice_needs_a_voice_id_in_the_reply():
    session = _EnrollmentSession(actions={"create_voice": [{"output": {}}]})

    with pytest.raises(enrollment.QwenVoiceEnrollmentError, match="did not return a voice id"):
        _client(session).create_voice(
            _wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL, wait_for_review=False
        )


def test_review_wait_ends_with_the_voice_still_deploying(clock):
    session = _EnrollmentSession(
        actions={
            "create_voice": [{"output": {"voice_id": VOICE_ID}}],
            "query_voice": [{"output": {"status": "DEPLOYING"}}],
        }
    )

    voice = _client(session).create_voice(
        _wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL, sleep=clock.sleep
    )

    assert voice.status == enrollment.VOICE_STATUS_DEPLOYING
    # Polls every 2 s for up to 24 s, then leaves the review to the service.
    assert set(clock.sleeps) == {2.0}
    assert sum(clock.sleeps) == pytest.approx(24.0)
    assert len(session.action_calls("query_voice")) == len(clock.sleeps) + 1
    assert session.action_calls("delete_voice") == []


def test_a_voice_rejected_in_review_is_deleted_and_reported(clock):
    session = _EnrollmentSession(
        actions={
            "create_voice": [{"output": {"voice_id": VOICE_ID}}],
            "query_voice": [{"output": {"status": "UNDEPLOYED"}}],
        }
    )

    with pytest.raises(enrollment.QwenVoiceEnrollmentError) as excinfo:
        _client(session).create_voice(
            _wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL, sleep=clock.sleep
        )

    assert excinfo.value.provider_code == "UNDEPLOYED"
    assert [call["json"] for call in session.action_calls("delete_voice")] == [
        {"model": "voice-enrollment", "input": {"action": "delete_voice", "voice_id": VOICE_ID}}
    ]
    assert clock.sleeps == []


_BROKEN_POLL_REPLIES = [
    pytest.param(_FakeResponse(broken=True), id="connection-dropped-mid-body"),
    pytest.param(
        _FakeResponse({"output": {"status": "OK"}}, headers={"Content-Encoding": "gzip"}),
        id="compressed-body",
    ),
    pytest.param(
        _FakeResponse({"output": {"status": "OK"}}, headers={"Content-Length": "999999999"}),
        id="oversized-body",
    ),
    pytest.param(
        _FakeResponse({"code": "InternalError", "message": "busy"}, 500),
        id="server-error",
    ),
    pytest.param(
        _FakeResponse(body=b"<html>502 Bad Gateway</html>", status_code=502),
        id="gateway-html",
    ),
]


@pytest.mark.parametrize("poll_reply", _BROKEN_POLL_REPLIES)
def test_a_failed_review_poll_keeps_the_enrolled_voice(clock, poll_reply):
    """The voice exists once create_voice answered; a poll failure must not hide it."""

    session = _EnrollmentSession(
        actions={
            "create_voice": [{"output": {"voice_id": VOICE_ID}}],
            "query_voice": [poll_reply],
        }
    )

    voice = _client(session).create_voice(
        _wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL, sleep=clock.sleep
    )

    assert voice.voice_id == VOICE_ID
    assert voice.status == enrollment.VOICE_STATUS_DEPLOYING
    assert session.action_calls("delete_voice") == []


def test_service_stores_a_voice_whose_review_poll_failed(monkeypatch):
    session = _EnrollmentSession(
        actions={
            "create_voice": [{"output": {"voice_id": VOICE_ID}}],
            "query_voice": [_FakeResponse(broken=True)],
        }
    )
    client = _client(session)
    monkeypatch.setattr(voice_clone_service, "_client", lambda _cfg: client)
    config = _clone_config(voice="", custom_voices=[])

    voice = voice_clone_service.create_voice(config, _wav_bytes(), "Mine")

    stored = config["tts"]["qwen_vc"]
    assert voice.status == enrollment.VOICE_STATUS_DEPLOYING
    assert [(v["voice_id"], v["status"]) for v in stored["custom_voices"]] == [
        (VOICE_ID, "DEPLOYING")
    ]
    assert stored["voice"] == VOICE_ID
    assert session.closed is True


@pytest.mark.parametrize(
    "delete_reply",
    [
        pytest.param(_FakeResponse(broken=True), id="connection-dropped-mid-body"),
        pytest.param(_FakeResponse({"output": {}}, headers={"Content-Encoding": "br"}), id="compressed"),
        pytest.param(({"code": "Throttling", "message": "slow down"}, 429), id="rate-limited"),
    ],
)
def test_a_failed_cleanup_of_a_rejected_voice_still_reports_the_rejection(clock, delete_reply):
    session = _EnrollmentSession(
        actions={
            "create_voice": [{"output": {"voice_id": VOICE_ID}}],
            "query_voice": [{"output": {"status": "UNDEPLOYED"}}],
            "delete_voice": [delete_reply],
        }
    )

    with pytest.raises(enrollment.QwenVoiceEnrollmentError) as excinfo:
        _client(session).create_voice(
            _wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL, sleep=clock.sleep
        )

    assert excinfo.value.provider_code == "UNDEPLOYED"
    assert len(session.action_calls("delete_voice")) == 1


def test_unreadable_replies_become_enrollment_errors_and_are_closed():
    client = _client()

    dropped = _FakeResponse(broken=True)
    with pytest.raises(enrollment.QwenVoiceEnrollmentError, match="Could not reach") as excinfo:
        client._read_json(dropped, label="Qwen voice cloning API")
    assert isinstance(excinfo.value.__cause__, requests.RequestException)
    assert excinfo.value.status_code is None
    assert dropped.closed is True

    compressed = _FakeResponse({"output": {}}, 503, headers={"Content-Encoding": "gzip"})
    with pytest.raises(enrollment.QwenVoiceEnrollmentError, match="unreadable response") as excinfo:
        client._read_json(compressed, label="Qwen voice cloning API")
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    # The status still tells the settings page what kind of failure it was.
    assert excinfo.value.status_code == 503
    assert compressed.closed is True

    gateway = _FakeResponse(body=b"<html>Bad Gateway</html>", status_code=502)
    with pytest.raises(enrollment.QwenVoiceEnrollmentError, match="unreadable response") as excinfo:
        client._read_json(gateway, label="Qwen voice cloning API")
    assert excinfo.value.status_code == 502

    garbage = _FakeResponse(body=b"not json", status_code=200)
    with pytest.raises(enrollment.QwenVoiceEnrollmentError, match="unreadable response") as excinfo:
        client._read_json(garbage, label="Qwen voice cloning API")
    assert excinfo.value.status_code is None


def test_enrollment_redirects_are_refused():
    session = _EnrollmentSession(actions={"create_voice": [({"output": {}}, 302)]})

    with pytest.raises(enrollment.QwenVoiceEnrollmentError, match="redirects are not allowed"):
        _client(session).create_voice(
            _wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL, wait_for_review=False
        )


def test_enrollment_surfaces_authentication_failures():
    session = _EnrollmentSession(
        actions={"create_voice": [({"code": "InvalidApiKey", "message": "invalid key"}, 401)]}
    )

    with pytest.raises(enrollment.QwenVoiceEnrollmentError) as excinfo:
        _client(session).create_voice(
            _wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL, wait_for_review=False
        )

    message = str(excinfo.value)
    assert "API key" in message
    assert "InvalidApiKey" in message
    assert excinfo.value.status_code == 401
    assert excinfo.value.provider_code == "InvalidApiKey"


def test_enrollment_error_carries_status_and_provider_code():
    """A failed enrollment must be identifiable from the log alone."""
    from src.utils.provider_diagnostics import safe_exception_summary

    session = _EnrollmentSession(
        actions={
            "create_voice": [
                ({"code": "InvalidParameter", "message": "target_model is not supported"}, 400)
            ]
        }
    )

    with pytest.raises(enrollment.QwenVoiceEnrollmentError) as excinfo:
        _client(session).create_voice(
            _wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL, wait_for_review=False
        )

    error = excinfo.value
    assert error.status_code == 400
    assert error.provider_code == "InvalidParameter"
    summary = safe_exception_summary(error)
    assert "status=400" in summary
    assert "code=InvalidParameter" in summary


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, "rejected the API key"),
        # 403 is account access (model permission, activation, free tier),
        # not a rejected key.
        (403, "access denied (HTTP 403)"),
        (404, "endpoint not found (HTTP 404)"),
        (429, "rate limit exceeded (HTTP 429)"),
        (503, "provider failure (HTTP 503)"),
        (400, "request failed (HTTP 400)"),
    ],
)
def test_enrollment_failure_messages_follow_the_status(status_code, expected):
    session = _EnrollmentSession(
        actions={"create_voice": [({"code": "Code", "message": "detail"}, status_code)]}
    )

    with pytest.raises(enrollment.QwenVoiceEnrollmentError) as excinfo:
        _client(session).create_voice(
            _wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL, wait_for_review=False
        )

    assert expected in str(excinfo.value)
    assert str(excinfo.value).endswith(": Code: detail")


def test_enrollment_error_code_is_bounded():
    from src.tts.qwen_voice_enrollment import _provider_code

    assert _provider_code({"code": "x" * 200}) == "x" * 64
    assert _provider_code({"error_code": " Throttling "}) == "Throttling"
    assert _provider_code({"message": "no code here"}) == ""


def test_enrollment_refuses_compressed_responses_up_front():
    """The bounded reader rejects gzip, so every request must opt out of it.

    Without this header DashScope gzips the reply and every enrollment fails
    with an opaque local error regardless of whether the key is valid.
    """

    session = _EnrollmentSession(
        actions={
            "create_voice": [{"output": {"voice_id": VOICE_ID}}],
            "query_voice": [{"output": {"status": "OK"}}],
        }
    )

    _client(session).create_voice(
        _wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL, sleep=lambda _s: None
    )

    api_calls = [call for call in session.calls if call.get("files") is None]
    assert [call["method"] for call in api_calls] == ["GET", "POST", "POST"]
    assert all(call["headers"]["Accept-Encoding"] == "identity" for call in api_calls)


def test_a_compressed_reply_is_reported_as_unreadable():
    session = _EnrollmentSession(
        actions={
            "create_voice": [
                _FakeResponse({"output": {"voice_id": VOICE_ID}}, headers={"Content-Encoding": "gzip"})
            ]
        }
    )

    with pytest.raises(enrollment.QwenVoiceEnrollmentError, match="unreadable response"):
        _client(session).create_voice(
            _wav_bytes(), target_model=QWEN_VC_DEFAULT_MODEL, wait_for_review=False
        )


def test_enrollment_prefix_is_reduced_to_the_accepted_charset():
    assert enrollment.safe_preferred_name("My Voice!") == "MyVoice"
    assert enrollment.safe_preferred_name("a" * 40) == "a" * 10
    assert enrollment.safe_preferred_name("123voice") == "v123voice"
    assert enrollment.safe_preferred_name("1234567890123") == "v123456789"
    assert enrollment.safe_preferred_name("x_y-z") == "xyz"
    generated = enrollment.safe_preferred_name("我的声音")
    assert re.fullmatch(r"mio\d{1,7}", generated)
    for name in ("My Voice!", "a" * 40, "我的声音", "123voice", "x_y-z", "", None):
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,9}", enrollment.safe_preferred_name(name))


# ---------------------------------------------------------------------------
# Listing, querying and deleting voices
# ---------------------------------------------------------------------------


def test_list_voices_reads_the_voice_list_and_derives_each_target_model():
    session = _EnrollmentSession(
        actions={
            "list_voice": [
                {
                    "output": {
                        "voice_list": [
                            {
                                "voice_id": VOICE_ID,
                                "gmt_create": "2026-10-13 09:00:00",
                                "gmt_modified": "2026-10-13 09:05:00",
                                "status": "ok",
                            },
                            {
                                "voice_id": "cosyvoice-v3-flash-other-a1b2",
                                "gmt_create": "2026-10-12 10:00:00",
                                "status": "OK",
                            },
                            {
                                "voice_id": "qwen-audio-3.0-realtime-flash-rt-c3d4",
                                "status": "DEPLOYING",
                            },
                            {"not_a_voice": True},
                            "garbage",
                        ]
                    }
                }
            ]
        }
    )

    voices = _client(session).list_voices()

    assert session.action_calls()[0]["json"] == {
        "model": "voice-enrollment",
        "input": {"action": "list_voice", "page_size": 50, "page_index": 0},
    }
    assert [(v.voice_id, v.target_model, v.status) for v in voices] == [
        (VOICE_ID, QWEN_VC_DEFAULT_MODEL, "OK"),
        ("cosyvoice-v3-flash-other-a1b2", "cosyvoice-v3-flash", "OK"),
        ("qwen-audio-3.0-realtime-flash-rt-c3d4", "qwen-audio-3.0-realtime-flash", "DEPLOYING"),
    ]
    assert voices[0].created_at == "2026-10-13 09:00:00"


def test_list_voices_clamps_the_page_parameters():
    session = _EnrollmentSession(actions={"list_voice": [{"output": {"voice_list": []}}]})

    assert _client(session).list_voices(page_size=500, page_index=-3) == []

    assert session.action_calls()[0]["json"]["input"] == {
        "action": "list_voice",
        "page_size": 100,
        "page_index": 0,
    }


def test_enrollment_lists_every_page_of_voices(monkeypatch):
    client = _client()
    pages = {
        0: [
            {
                "voice_id": (
                    f"qwen-audio-3.0-tts-flash-v{i}-x" if i % 2 else f"cosyvoice-v3-flash-v{i}-x"
                ),
                "status": "OK",
            }
            for i in range(100)
        ],
        1: [{"voice_id": f"qwen-audio-3.0-tts-flash-v{100 + i}-x"} for i in range(7)],
    }
    requested: list[int] = []

    def fake_post(payload):
        index = int(payload["input"]["page_index"])
        requested.append(index)
        assert payload["input"]["page_size"] == 100
        return {"output": {"voice_list": pages.get(index, [])}}

    monkeypatch.setattr(client, "_post", fake_post)

    voices = client.list_all_voices()

    assert requested == [0, 1]
    assert len(voices) == 107
    assert voices[-1].voice_id == "qwen-audio-3.0-tts-flash-v106-x"


def test_listing_stops_when_the_service_repeats_a_page(monkeypatch):
    client = _client()
    page = [{"voice_id": f"qwen-audio-3.0-tts-flash-v{i}-x"} for i in range(100)]
    requested: list[int] = []

    def fake_post(payload):
        requested.append(int(payload["input"]["page_index"]))
        return {"output": {"voice_list": page}}

    monkeypatch.setattr(client, "_post", fake_post)

    assert len(client.list_all_voices()) == 100
    assert requested == [0, 1]


def test_listing_is_bounded_by_the_page_limit(monkeypatch):
    client = _client()
    requested: list[int] = []

    def fake_post(payload):
        index = int(payload["input"]["page_index"])
        requested.append(index)
        return {
            "output": {
                "voice_list": [
                    {"voice_id": f"qwen-audio-3.0-tts-flash-p{index}v{i}-x"} for i in range(100)
                ]
            }
        }

    monkeypatch.setattr(client, "_post", fake_post)

    assert len(client.list_all_voices(max_pages=3)) == 300
    assert requested == [0, 1, 2]


def test_query_and_delete_voice_requests():
    session = _EnrollmentSession(actions={"query_voice": [{"output": {"status": "ok"}}]})
    client = _client(session)

    assert client.query_voice_status(VOICE_ID) == "OK"
    client.delete_voice(VOICE_ID)

    assert [call["json"] for call in session.action_calls()] == [
        {"model": "voice-enrollment", "input": {"action": "query_voice", "voice_id": VOICE_ID}},
        {"model": "voice-enrollment", "input": {"action": "delete_voice", "voice_id": VOICE_ID}},
    ]
    assert all(call["url"] == CUSTOMIZATION_URL for call in session.action_calls())

    assert client.query_voice_status(" ") == ""
    with pytest.raises(enrollment.QwenVoiceEnrollmentError, match="No voice was selected"):
        client.delete_voice("")
    assert len(session.action_calls()) == 2


@pytest.mark.parametrize(
    ("voice_id", "expected"),
    [
        ("qwen-audio-3.0-tts-flash-mine-abc", "qwen-audio-3.0-tts-flash"),
        ("qwen-audio-3.1-tts-flash-x-1", "qwen-audio-3.1-tts-flash"),
        ("qwen-audio-3.0-tts-plus-y-2", "qwen-audio-3.0-tts-plus"),
        ("qwen-audio-3.0-realtime-flash-z-3", "qwen-audio-3.0-realtime-flash"),
        ("cosyvoice-v3-flash-a-4", "cosyvoice-v3-flash"),
        ("cosyvoice-v3.5-plus-b-5", "cosyvoice-v3.5-plus"),
        ("cosyvoice-v2-c-6", "cosyvoice-v2"),
        ("vc-mio-7f3a9c", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_the_target_model_is_read_from_the_voice_id(voice_id, expected):
    assert target_model_from_voice_id(voice_id) == expected


def test_cloned_voice_records_round_trip_with_status_and_region():
    voice = enrollment.ClonedVoice.from_dict(
        {"voice_id": "vc-mio-7f3a9c", "target_model": MODEL_31, "status": "deploying"}
    )
    assert voice.target_model == MODEL_31
    assert voice.status == "DEPLOYING"

    derived = enrollment.ClonedVoice.from_dict({"voice_id": VOICE_ID, "region": "china_mainland"})
    assert derived.target_model == QWEN_VC_DEFAULT_MODEL
    assert derived.as_dict() == {
        "voice_id": VOICE_ID,
        "display_name": "",
        "target_model": QWEN_VC_DEFAULT_MODEL,
        "created_at": "",
        "language": "",
        "region": "china_mainland",
        "status": "",
    }
    assert enrollment.ClonedVoice.from_dict({"display_name": "no id"}) is None
    assert enrollment.ClonedVoice.from_dict(None) is None


# ---------------------------------------------------------------------------
# voice_clone_service
# ---------------------------------------------------------------------------


def test_service_refuses_to_upload_without_consent(monkeypatch):
    config = _clone_config(upload_consent=False)
    monkeypatch.setattr(
        voice_clone_service,
        "_client",
        lambda _cfg: pytest.fail("consent must be checked before any upload"),
    )
    with pytest.raises(voice_clone_service.VoiceCloneConsentRequiredError):
        voice_clone_service.create_voice(config, _wav_bytes(), "Mine")


def test_service_requires_an_api_key():
    config = _clone_config(api_key="")
    with pytest.raises(voice_clone_service.VoiceCloneNotConfiguredError):
        voice_clone_service.create_voice(config, _wav_bytes(), "Mine")


@pytest.mark.parametrize(
    "model",
    ["qwen3-tts-vc-2026-01-22", "qwen-audio-3.0-realtime-flash", "cosyvoice-v3-flash", ""],
)
def test_service_refuses_a_model_that_cannot_use_cloned_voices(monkeypatch, model):
    config = _clone_config(model=model)
    monkeypatch.setattr(
        voice_clone_service,
        "_client",
        lambda _cfg: pytest.fail("an unsupported model must be refused before any upload"),
    )

    with pytest.raises(voice_clone_service.VoiceCloneModelUnsupportedError):
        voice_clone_service.create_voice(config, _wav_bytes(), "Mine")


@pytest.mark.parametrize(
    ("source_language", "expected"),
    [
        ("auto", ""),
        ("", ""),
        ("zh-CN", "zh"),
        ("zh_TW", "zh"),
        ("yue", "zh"),
        ("ja", "ja"),
        ("en-US", "en"),
        ("pt-BR", "pt"),
        ("tl", "fil"),
        ("xx", ""),
    ],
)
def test_recording_language_hint_follows_the_source_language(source_language, expected):
    config = {"translation": {"source_language": source_language}}
    assert voice_clone_service.recording_language_hint(config) == expected


def test_recording_language_hint_tolerates_a_missing_translation_section():
    assert voice_clone_service.recording_language_hint({}) == ""
    assert voice_clone_service.recording_language_hint(None) == ""


def test_service_records_a_new_voice_with_status_and_region_and_selects_it(monkeypatch):
    config = _clone_config()
    config["translation"]["source_language"] = "ja-JP"
    client = _use_service_client(monkeypatch, _ServiceClient())

    voice = voice_clone_service.create_voice(config, _wav_bytes(), "Second")

    assert client.create_calls[0]["target_model"] == QWEN_VC_DEFAULT_MODEL
    assert client.create_calls[0]["display_name"] == "Second"
    assert client.create_calls[0]["language"] == "ja"
    stored = config["tts"]["qwen_vc"]
    assert voice.region == "china_mainland"
    assert [v["voice_id"] for v in stored["custom_voices"]] == [VOICE_ID, voice.voice_id]
    new_entry = stored["custom_voices"][-1]
    assert new_entry["status"] == "DEPLOYING"
    assert new_entry["region"] == "china_mainland"
    assert new_entry["target_model"] == QWEN_VC_DEFAULT_MODEL
    assert new_entry["language"] == "ja"
    assert stored["voice"] == voice.voice_id


def test_service_deletes_locally_when_the_service_says_the_voice_is_gone(monkeypatch):
    config = _clone_config()
    _use_service_client(
        monkeypatch,
        _ServiceClient(
            delete_error=enrollment.QwenVoiceEnrollmentError(
                "Qwen voice cloning request failed (HTTP 400): InvalidParameter: voice not found",
                status_code=400,
                provider_code="InvalidParameter",
            )
        ),
    )

    voice_clone_service.delete_voice(config, VOICE_ID)

    stored = config["tts"]["qwen_vc"]
    assert stored["custom_voices"] == []
    assert stored["voice"] == ""


@pytest.mark.parametrize(
    "error",
    [
        enrollment.QwenVoiceEnrollmentError("Could not reach the Qwen voice cloning service"),
        enrollment.QwenVoiceEnrollmentError(
            "Qwen voice cloning rejected the API key", status_code=401
        ),
        enrollment.QwenVoiceEnrollmentError(
            "Qwen voice cloning rate limit exceeded (HTTP 429)", status_code=429
        ),
        enrollment.QwenVoiceEnrollmentError(
            "Qwen voice cloning provider failure (HTTP 503)", status_code=503
        ),
    ],
)
def test_service_keeps_the_local_voice_when_the_delete_fails_for_other_reasons(
    monkeypatch, error
):
    """A network or account failure must not make the voice vanish locally while it still exists (and bills) remotely."""
    config = _clone_config()
    _use_service_client(monkeypatch, _ServiceClient(delete_error=error))

    with pytest.raises(enrollment.QwenVoiceEnrollmentError):
        voice_clone_service.delete_voice(config, VOICE_ID)

    stored = config["tts"]["qwen_vc"]
    assert [voice["voice_id"] for voice in stored["custom_voices"]] == [VOICE_ID]
    assert stored["voice"] == VOICE_ID


def test_deleting_the_selected_voice_selects_one_usable_with_the_current_model(monkeypatch):
    spare = "qwen-audio-3.0-tts-flash-spare-777"
    config = _clone_config(
        custom_voices=[
            {"voice_id": VOICE_ID, "target_model": QWEN_VC_DEFAULT_MODEL, "status": "OK"},
            {"voice_id": VOICE_31, "target_model": MODEL_31, "status": "OK"},
            {"voice_id": spare, "target_model": QWEN_VC_DEFAULT_MODEL, "status": "OK"},
        ]
    )
    client = _use_service_client(monkeypatch, _ServiceClient())

    voice_clone_service.delete_voice(config, VOICE_ID)

    stored = config["tts"]["qwen_vc"]
    assert client.deleted == [VOICE_ID]
    assert [voice["voice_id"] for voice in stored["custom_voices"]] == [VOICE_31, spare]
    # The 3.1 voice is first but cannot speak with the 3.0 model.
    assert stored["voice"] == spare


def test_refresh_keeps_voices_of_another_qwen_audio_model(monkeypatch):
    """Changing the model box and syncing used to drop the other model's voices."""

    config = _clone_config(
        custom_voices=[
            {
                "voice_id": VOICE_ID,
                "display_name": "我的声音 3.0",
                "target_model": QWEN_VC_DEFAULT_MODEL,
                "language": "zh",
                "region": "china_mainland",
                "status": "OK",
            },
            {
                "voice_id": VOICE_31,
                "display_name": "我的声音 3.1",
                "target_model": MODEL_31,
                "language": "ja",
                "region": "china_mainland",
                "status": "OK",
            },
        ]
    )
    _use_service_client(
        monkeypatch,
        _ServiceClient(
            remote=[
                {"voice_id": VOICE_ID, "gmt_create": "2026-10-12 10:00:00", "status": "OK"},
                {"voice_id": VOICE_31, "gmt_create": "2026-10-12 11:00:00", "status": "OK"},
            ]
        ),
    )
    expected = [
        (VOICE_ID, "我的声音 3.0", "zh", QWEN_VC_DEFAULT_MODEL, "china_mainland"),
        (VOICE_31, "我的声音 3.1", "ja", MODEL_31, "china_mainland"),
    ]

    config["tts"]["qwen_vc"]["model"] = MODEL_31
    voices = voice_clone_service.refresh_voices(config)
    assert [
        (v["voice_id"], v["display_name"], v["language"], v["target_model"], v["region"])
        for v in voices
    ] == expected
    assert config["tts"]["qwen_vc"]["voice"] == VOICE_ID

    config["tts"]["qwen_vc"]["model"] = QWEN_VC_DEFAULT_MODEL
    voices = voice_clone_service.refresh_voices(config)
    assert [
        (v["voice_id"], v["display_name"], v["language"], v["target_model"], v["region"])
        for v in voices
    ] == expected
    assert voices[0]["created_at"] == "2026-10-12 10:00:00"


def test_refresh_skips_voices_this_engine_cannot_speak_with(monkeypatch):
    new_plus = "qwen-audio-3.0-tts-plus-new-5e5e"
    rejected = "qwen-audio-3.0-tts-flash-rejected-4d4d"
    config = _clone_config(
        custom_voices=[
            {
                "voice_id": VOICE_ID,
                "display_name": "Mine",
                "target_model": QWEN_VC_DEFAULT_MODEL,
                "region": "china_mainland",
                "status": "DEPLOYING",
            },
            {
                "voice_id": rejected,
                "display_name": "Rejected",
                "target_model": QWEN_VC_DEFAULT_MODEL,
                "region": "china_mainland",
                "status": "DEPLOYING",
            },
        ]
    )
    _use_service_client(
        monkeypatch,
        _ServiceClient(
            remote=[
                {"voice_id": VOICE_ID, "status": "OK"},
                {"voice_id": "cosyvoice-v3-flash-mine-1a1a", "status": "OK"},
                {"voice_id": "qwen-audio-3.0-realtime-flash-rt-2b2b", "status": "OK"},
                {"voice_id": "vc-unknown-3c3c", "status": "OK"},
                {"voice_id": rejected, "status": "UNDEPLOYED"},
                {"voice_id": new_plus, "status": "DEPLOYING"},
            ]
        ),
    )

    voices = voice_clone_service.refresh_voices(config)

    assert [(v["voice_id"], v["display_name"], v["status"]) for v in voices] == [
        (VOICE_ID, "Mine", "OK"),
        (new_plus, new_plus, "DEPLOYING"),
    ]
    assert voices[1]["target_model"] == "qwen-audio-3.0-tts-plus"


def test_refresh_keeps_the_stored_model_of_an_id_that_does_not_name_it(monkeypatch):
    voice_id = "vc-mio-7f3a9c"
    config = _clone_config(
        voice=voice_id,
        custom_voices=[
            {
                "voice_id": voice_id,
                "display_name": "Mio",
                "target_model": QWEN_VC_DEFAULT_MODEL,
                "language": "zh",
                "created_at": "2026-10-01 08:00:00",
                "region": "china_mainland",
                "status": "OK",
            }
        ],
    )
    _use_service_client(
        monkeypatch, _ServiceClient(remote=[{"voice_id": voice_id, "status": "OK"}])
    )

    voices = voice_clone_service.refresh_voices(config)

    assert len(voices) == 1
    entry = voices[0]
    assert entry["target_model"] == QWEN_VC_DEFAULT_MODEL
    assert entry["display_name"] == "Mio"
    assert entry["language"] == "zh"
    assert entry["created_at"] == "2026-10-01 08:00:00"
    # The next config load keeps it, and the engine can speak with it.
    assert cloned_voice_survives_retirement(entry)
    assert cloned_voice_is_usable(entry, model=QWEN_VC_DEFAULT_MODEL, region="china_mainland")
    assert config["tts"]["qwen_vc"]["voice"] == voice_id


def test_refresh_keeps_voices_recorded_in_another_region(monkeypatch):
    here = "qwen-audio-3.0-tts-flash-here-1a2b"
    moved = "qwen-audio-3.0-tts-flash-moved-9z9z"
    config = _clone_config(
        custom_voices=[
            {
                "voice_id": "qwen-audio-3.0-tts-flash-elsewhere-3c4d",
                "display_name": "Elsewhere",
                "target_model": QWEN_VC_DEFAULT_MODEL,
                "region": "custom",
            },
            {
                "voice_id": "qwen-audio-3.0-tts-flash-stale-5e6f",
                "display_name": "Stale",
                "target_model": QWEN_VC_DEFAULT_MODEL,
                "region": "china_mainland",
            },
            {
                "voice_id": moved,
                "display_name": "Moved",
                "target_model": QWEN_VC_DEFAULT_MODEL,
                "region": "custom",
            },
        ]
    )
    _use_service_client(
        monkeypatch,
        _ServiceClient(
            remote=[
                {"voice_id": here, "status": "OK"},
                # Listed here, so this region decides: it was rejected.
                {"voice_id": moved, "status": "UNDEPLOYED"},
            ]
        ),
    )

    voices = voice_clone_service.refresh_voices(config)

    # The other region's voice survives (that account still has it); the
    # stale voice of the current region is dropped because the service no
    # longer lists it.
    assert [v["voice_id"] for v in voices] == [
        here,
        "qwen-audio-3.0-tts-flash-elsewhere-3c4d",
    ]
    assert voices[0]["region"] == "china_mainland"
    assert voices[1]["region"] == "custom"


def test_refresh_selects_a_voice_usable_with_the_current_model(monkeypatch):
    config = _clone_config(voice="qwen-audio-3.0-tts-flash-gone-0000", custom_voices=[])
    client = _use_service_client(
        monkeypatch,
        _ServiceClient(
            remote=[
                {"voice_id": VOICE_31, "status": "OK"},
                {"voice_id": VOICE_ID, "status": "OK"},
            ]
        ),
    )

    voice_clone_service.refresh_voices(config)
    assert config["tts"]["qwen_vc"]["voice"] == VOICE_ID

    # Nothing usable with the current model: select nothing rather than a
    # voice that can only fail.
    client.remote = [{"voice_id": VOICE_31, "status": "OK"}]
    config["tts"]["qwen_vc"]["voice"] = ""
    voice_clone_service.refresh_voices(config)
    assert config["tts"]["qwen_vc"]["voice"] == ""
    assert [v["voice_id"] for v in config["tts"]["qwen_vc"]["custom_voices"]] == [VOICE_31]


def test_new_voices_record_their_region_and_mismatched_voices_are_unusable(monkeypatch):
    config = _clone_config(custom_voices=[], voice="")
    _use_service_client(
        monkeypatch,
        _ServiceClient(
            created=enrollment.ClonedVoice(
                voice_id="qwen-audio-3.0-tts-flash-new-1111",
                display_name="Mine",
                target_model=QWEN_VC_DEFAULT_MODEL,
                status="OK",
            )
        ),
    )
    created = voice_clone_service.create_voice(config, b"RIFF", "Mine")
    resolved = voice_clone_service.voice_clone_config(config)
    assert created.region == resolved["region"] == "china_mainland"
    stored = config["tts"]["qwen_vc"]["custom_voices"][0]
    assert stored["region"] == resolved["region"]
    assert stored["target_model"] == resolved["model"]

    engine_config = dict(resolved)
    assert [v[0] for v in usable_cloned_voice_options(engine_config)] == [
        "qwen-audio-3.0-tts-flash-new-1111"
    ]

    # Switching the synthesis model makes the voice unusable and the engine
    # explains why instead of forwarding an opaque HTTP 400.
    engine_config["model"] = MODEL_31
    assert usable_cloned_voice_options(engine_config) == ()
    engine = QwenVoiceCloneTTS(engine_config)
    # Still "available": the mismatch must surface as a specific error on
    # use rather than making the engine silently disappear.
    assert engine.is_available() is True
    assert engine.get_available_voices() == []
    with pytest.raises(RuntimeError, match="registered for model"):
        engine.synthesize("hello", "qwen-audio-3.0-tts-flash-new-1111")
    with pytest.raises(RuntimeError, match="registered for model"):
        engine.synthesize("hello", "")


def test_tokyo_or_singapore_qwen_tts_key_is_not_copied_into_the_cloning_config():
    """A key only authenticates in its own region; cloning is Beijing-only."""
    from src.utils import config_manager

    for region, key in (("japan", "sk-tokyo"), ("singapore", "sk-singapore")):
        config = {
            "tts": {
                "qwen_tts": {"api_key": key, "region": region},
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


# ---------------------------------------------------------------------------
# Cloning config (Beijing only)
# ---------------------------------------------------------------------------


def test_cloning_config_defaults_to_beijing():
    defaults = get_tts_api_default_config("qwen_vc")

    assert defaults["region"] == "china_mainland"
    assert defaults["base_url"] == BEIJING
    assert defaults["model"] == QWEN_VC_DEFAULT_MODEL == "qwen-audio-3.0-tts-flash"
    assert defaults["voice"] == ""
    assert defaults["upload_consent"] is False
    assert defaults["custom_voices"] == []
    assert QWEN_VC_MODEL_OPTIONS == (
        "qwen-audio-3.0-tts-flash",
        "qwen-audio-3.1-tts-flash",
        "qwen-audio-3.0-tts-plus",
    )


@pytest.mark.parametrize(
    "region",
    ["china_mainland", "china", "cn", "mainland", "beijing", "singapore", "intl",
     "international", "sg", "jp", "japan", "", "nonsense"],
)
def test_every_cloning_region_alias_resolves_to_beijing(region):
    assert normalize_tts_api_region("qwen_vc", region) == "china_mainland"
    resolved = resolve_tts_api_config("qwen_vc", {"region": region})
    assert resolved["region"] == "china_mainland"
    assert resolved["base_url"] == BEIJING


def test_cloning_region_options_are_beijing_and_custom():
    assert get_tts_api_region_options("qwen_vc") == (
        ("qwen_region_china_mainland", "china_mainland"),
        ("qwen_region_custom", "custom"),
    )


def test_the_legacy_international_cloning_url_is_replaced_by_beijing():
    assert get_tts_api_known_base_urls("qwen_vc") == frozenset(
        {BEIJING, QWEN_TTS_BASE_URL_INTERNATIONAL}
    )
    assert tts_api_region_from_base_url("qwen_vc", QWEN_TTS_BASE_URL_INTERNATIONAL) == (
        "china_mainland"
    )
    for raw in (
        {"base_url": QWEN_TTS_BASE_URL_INTERNATIONAL},
        {"base_url": f"{QWEN_TTS_BASE_URL_INTERNATIONAL}/"},
        {"region": "singapore", "base_url": QWEN_TTS_BASE_URL_INTERNATIONAL},
    ):
        resolved = resolve_tts_api_config("qwen_vc", raw)
        assert resolved["region"] == "china_mainland"
        assert resolved["base_url"] == BEIJING


def test_a_custom_beijing_workspace_url_is_kept():
    for raw in (
        {"region": "custom", "base_url": WORKSPACE_URL},
        {"base_url": WORKSPACE_URL},
    ):
        resolved = resolve_tts_api_config("qwen_vc", raw)
        assert resolved["region"] == "custom"
        assert resolved["base_url"] == WORKSPACE_URL
    kept = resolve_tts_api_config(
        "qwen_vc", {"region": "china_mainland", "base_url": WORKSPACE_URL}
    )
    assert kept["base_url"] == WORKSPACE_URL


@pytest.mark.parametrize(
    ("region", "supported"),
    [
        ("china_mainland", True),
        ("China-Mainland", True),
        ("cn", True),
        ("china", True),
        ("mainland", True),
        ("singapore", False),
        ("intl", False),
        ("sg", False),
        ("japan", False),
        ("jp", False),
        ("custom", False),
        ("", False),
        (None, False),
    ],
)
def test_region_support_predicate_is_beijing_only(region, supported):
    assert region_supports_voice_cloning(region) is supported


@pytest.mark.parametrize(
    ("model", "supported"),
    [
        ("qwen-audio-3.0-tts-flash", True),
        ("qwen-audio-3.1-tts-flash", True),
        ("qwen-audio-3.0-tts-plus", True),
        ("qwen-audio-3.0-tts-flash-2026-10-01", True),
        ("qwen-audio-4-tts-flash", True),
        (" Qwen-Audio-3.0-TTS-Flash ", True),
        ("qwen-audio-3.0-realtime-flash", False),
        ("qwen-audio-3.0-asr-flash", False),
        ("qwen-audio-tts-flash", False),
        ("qwen-audio-", False),
        ("qwen3-tts-vc-2026-01-22", False),
        ("qwen3-tts-flash", False),
        ("cosyvoice-v3-flash", False),
        ("", False),
        (None, False),
    ],
)
def test_only_qwen_audio_tts_models_can_use_cloned_voices(model, supported):
    assert qwen_vc_model_is_supported(model) is supported


def test_every_offered_cloning_model_accepts_cloned_voices():
    assert all(qwen_vc_model_is_supported(model) for model in QWEN_VC_MODEL_OPTIONS)


@pytest.mark.parametrize(
    ("voice", "survives"),
    [
        ({"voice_id": VOICE_ID}, True),
        ({"voice_id": "vc-mio-1", "target_model": MODEL_31}, True),
        ({"voice_id": "qwen-audio-3.0-tts-plus-p-2"}, True),
        ({"voice_id": "qwen-audio-3.0-realtime-flash-r-3"}, False),
        ({"voice_id": "cosyvoice-v3-flash-c-4"}, False),
        ({"voice_id": "qwen-tts-vc-old-voice-5"}, False),
        ({"voice_id": "vc-old-6", "target_model": "qwen3-tts-vc-2026-01-22"}, False),
        ({"voice_id": "vc-old-7"}, False),
    ],
)
def test_only_qwen_audio_tts_voices_survive_the_retirement(voice, survives):
    assert cloned_voice_survives_retirement(voice) is survives


def test_rejected_voices_are_not_usable():
    base = {"voice_id": VOICE_ID, "target_model": QWEN_VC_DEFAULT_MODEL, "region": "china_mainland"}
    for status, usable in (("OK", True), ("DEPLOYING", True), ("", True),
                           ("UNDEPLOYED", False), ("undeployed", False)):
        voice = dict(base, status=status)
        assert cloned_voice_is_usable(
            voice, model=QWEN_VC_DEFAULT_MODEL, region="china_mainland"
        ) is usable

    config = {
        "model": QWEN_VC_DEFAULT_MODEL,
        "region": "china_mainland",
        "custom_voices": [
            dict(base, status="OK"),
            dict(base, voice_id="qwen-audio-3.0-tts-flash-bad-1", status="undeployed"),
        ],
    }
    assert [option[0] for option in usable_cloned_voice_options(config)] == [VOICE_ID]
    assert normalize_cloned_voices(config["custom_voices"])[1]["status"] == "UNDEPLOYED"


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


# ---------------------------------------------------------------------------
# QwenVoiceCloneTTS (SpeechSynthesizer)
# ---------------------------------------------------------------------------


class _SynthesisSession:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict, dict]] = []
        self.gets: list[str] = []

    def mount(self, *_args, **_kwargs):
        pass

    def close(self):
        pass

    def post(self, url, headers=None, json=None, timeout=None, stream=None, allow_redirects=None):
        assert stream is True
        assert allow_redirects is False
        self.posts.append((url, dict(headers or {}), copy.deepcopy(json)))
        return _FakeResponse(
            {
                "request_id": "req-synth",
                "output": {
                    "audio": {
                        "url": (
                            "http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/"
                            "clone.wav?Signature=test"
                        ),
                        "id": "audio-1",
                    }
                },
            }
        )

    def get(self, url, headers=None, timeout=None, stream=None, allow_redirects=None):
        assert stream is True
        assert allow_redirects is False
        self.gets.append(url)
        return _FakeResponse(
            body=b"RIFF\x04\x00\x00\x00WAVE",
            headers={"content-type": "audio/wav"},
        )


@pytest.fixture
def synthesis_session(monkeypatch):
    session = _SynthesisSession()
    monkeypatch.setattr("src.tts.api_tts_engines.requests.Session", lambda: session)
    # The fake result host has no DNS; trust it like the engine trusts
    # DashScope's signed result URLs.
    monkeypatch.setattr(api_tts_engines._APITTSBase, "_is_safe_audio_url", lambda _self, _url: True)
    return session


def _engine_config(**overrides):
    config = dict(_clone_config()["tts"]["qwen_vc"])
    config.update(overrides)
    return config


def test_engine_reports_unavailable_until_a_voice_exists():
    without = QwenVoiceCloneTTS(config=_engine_config(voice="", custom_voices=[]))
    assert without.is_available() is False

    with_voice = QwenVoiceCloneTTS(config=_engine_config())
    assert with_voice.is_available() is True
    assert [voice.id for voice in with_voice.get_available_voices()] == [VOICE_ID]


def test_engine_refuses_to_synthesize_without_a_cloned_voice():
    engine = QwenVoiceCloneTTS(config=_engine_config(voice="", custom_voices=[]))
    with pytest.raises(RuntimeError, match="no cloned voice"):
        engine.synthesize("hello", "")


def test_engine_does_not_offer_a_voice_rejected_in_review():
    engine = QwenVoiceCloneTTS(
        config=_engine_config(
            custom_voices=[
                {"voice_id": VOICE_ID, "target_model": QWEN_VC_DEFAULT_MODEL, "status": "UNDEPLOYED"}
            ]
        )
    )
    assert engine.get_available_voices() == []


def test_clone_engine_posts_to_the_speech_synthesizer_and_downloads_the_result(
    synthesis_session,
):
    engine = QwenVoiceCloneTTS(_engine_config())

    audio = engine.synthesize("Hello there", VOICE_ID)

    assert audio == b"RIFF\x04\x00\x00\x00WAVE"
    url, headers, payload = synthesis_session.posts[0]
    assert url == SYNTHESIZER_URL
    assert headers["Authorization"] == "Bearer sk-test"
    assert payload == {
        "model": QWEN_VC_DEFAULT_MODEL,
        "input": {
            "text": "Hello there",
            "voice": VOICE_ID,
            "format": "wav",
            "sample_rate": 24000,
        },
    }
    # The signed result URL is fetched over HTTPS.
    assert synthesis_session.gets == [
        "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/clone.wav?Signature=test"
    ]


def test_clone_engine_uses_the_configured_voice_by_default(synthesis_session):
    engine = QwenVoiceCloneTTS(_engine_config())

    engine.synthesize("Hello there", "")

    assert synthesis_session.posts[0][2]["input"]["voice"] == VOICE_ID


def test_clone_engine_prewarms_the_speech_synthesizer_route():
    engine = QwenVoiceCloneTTS(_engine_config())

    assert engine._prewarm_url() == SYNTHESIZER_URL
    assert engine._synthesis_url() == SYNTHESIZER_URL


def test_preset_qwen_tts_still_uses_multimodal_generation():
    preset = QwenTTS(
        {"api_key": "sk-test", "region": "china_mainland", "model": "qwen3-tts-flash", "voice": "Cherry"}
    )

    assert preset._synthesis_url() == MULTIMODAL_URL
    assert preset._prewarm_url() == MULTIMODAL_URL
    assert preset._synthesis_payload(
        "Hello", "Cherry", language_type="English", rate=1.5
    ) == {
        "model": "qwen3-tts-flash",
        "input": {"text": "Hello", "voice": "Cherry", "language_type": "English"},
    }
    # qwen3-tts has no speed parameter; the whole rate is applied locally.
    assert preset._local_rate_factor(1.5) == 1.5


def _capture_synthesis(monkeypatch, engine):
    captured: dict[str, list] = {"requests": [], "local": []}

    def fake_request(url, payload):
        captured["requests"].append((url, copy.deepcopy(payload)))
        return _wav_bytes(duration_s=0.2)

    def fake_local(audio, rate, volume):
        captured["local"].append((rate, volume))
        return audio

    monkeypatch.setattr(engine, "_request_json_audio", fake_request)
    monkeypatch.setattr(engine, "_apply_local_rate_volume", fake_local)
    return captured


@pytest.mark.parametrize(
    ("rate", "native_rate", "local_factor"),
    [
        (1.0, None, 1.0),
        (1.5, 1.5, 1.0),
        (2.0, 2.0, 1.0),
        (2.5, 2.0, 1.25),
        (0.5, 0.5, 1.0),
        (0.3, 0.5, 0.6),
    ],
)
def test_clone_engine_sends_the_native_rate_and_applies_the_rest_locally(
    monkeypatch, rate, native_rate, local_factor
):
    engine = QwenVoiceCloneTTS(_engine_config())
    captured = _capture_synthesis(monkeypatch, engine)

    engine.synthesize("Hello there", VOICE_ID, rate=rate, volume=0.6)

    [(url, payload)] = captured["requests"]
    assert url == SYNTHESIZER_URL
    assert payload["input"].get("rate") == native_rate
    # Volume has no service parameter; it always stays local.
    assert "volume" not in payload["input"]
    [(applied_rate, applied_volume)] = captured["local"]
    assert applied_rate == pytest.approx(local_factor)
    assert applied_volume == 0.6


@pytest.mark.parametrize(
    ("text", "language_type", "hints"),
    [
        ("你好，今天天气怎么样？", "", ["zh"]),
        ("こんにちは", "", ["ja"]),
        ("안녕하세요", "", ["ko"]),
        ("Hello there", "", None),
        ("Hello there", "English", ["en"]),
        # Kanji-only Japanese follows the translation target.
        ("大丈夫", "Japanese", ["ja"]),
        ("大丈夫", "", ["zh"]),
        ("Hola, ¿cómo estás?", "Spanish", ["es"]),
        ("Guten Tag", "German", ["de"]),
        ("Bom dia", "pt-BR", ["pt"]),
        ("Sawasdee", "Thai", None),
    ],
)
def test_clone_engine_hints_the_spoken_language(monkeypatch, text, language_type, hints):
    engine = QwenVoiceCloneTTS(_engine_config(language_type=language_type))
    captured = _capture_synthesis(monkeypatch, engine)

    engine.synthesize(text, VOICE_ID)

    payload = captured["requests"][0][1]
    assert payload["input"].get("language_hints") == hints
    assert "language_type" not in payload["input"]


def test_main_window_passes_the_target_language_to_cloned_voices(monkeypatch):
    """Kanji-only Japanese must not be read as Chinese by a cloned voice."""
    from src.ui_qt.main_window import MainWindow

    window = MainWindow.__new__(MainWindow)
    window._current_tgt_lang = "ja"
    window._config = {
        "translation": {"output_format": "translated_only"},
        "tts": {"engine": "qwen_vc", "qwen_vc": _engine_config()},
    }

    engine_cfg = MainWindow._current_tts_engine_config(window)

    assert engine_cfg["language_type"] == "Japanese"
    engine = QwenVoiceCloneTTS(engine_cfg)
    captured = _capture_synthesis(monkeypatch, engine)
    engine.synthesize("大丈夫", "")
    assert captured["requests"][0][1]["input"]["language_hints"] == ["ja"]

    window._config["translation"]["output_format"] = "original_only"
    assert "language_type" not in MainWindow._current_tts_engine_config(window)
