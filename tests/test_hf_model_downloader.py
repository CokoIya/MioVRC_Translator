from __future__ import annotations

from collections.abc import Iterable
import hashlib

import pytest

from src.asr import hf_model_downloader as downloader


_REAL_MODEL_URL_VALIDATOR = downloader._is_safe_model_download_url


@pytest.fixture(autouse=True)
def _allow_documentation_hosts(monkeypatch):
    monkeypatch.setattr(
        downloader,
        "_is_safe_model_download_url",
        lambda url: str(url).startswith("https://"),
    )


def _clear_mirror_env(monkeypatch):
    for name in ("MIO_HF_MIRROR_BASES", "HF_ENDPOINT", "HF_HUB_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)


def test_mirror_candidates_allow_custom_mainland_proxy(monkeypatch):
    _clear_mirror_env(monkeypatch)
    monkeypatch.setenv(
        "MIO_HF_MIRROR_BASES",
        "https://mirror.example/hf, https://hf-mirror.com/",
    )

    candidates = downloader._mirror_candidates()

    assert candidates[0] == "https://mirror.example/hf"
    assert candidates.count("https://hf-mirror.com") == 1
    assert "https://huggingface.co" in candidates


def test_select_mirror_uses_locale_preferred_base_when_probes_fail(monkeypatch):
    _clear_mirror_env(monkeypatch)
    monkeypatch.setattr(downloader, "_probe_mirror_throughput", lambda *args: None)
    monkeypatch.setattr(
        downloader, "_preferred_mirror_for_locale", lambda: "https://hf-mirror.com"
    )

    selected = downloader._select_mirror("model/repo", "config.json")

    assert selected == "https://hf-mirror.com"


def test_mirror_fallback_order_keeps_selected_then_locale_then_official(monkeypatch):
    _clear_mirror_env(monkeypatch)
    monkeypatch.setenv("MIO_HF_MIRROR_BASES", "https://mirror.example")
    monkeypatch.setattr(
        downloader, "_preferred_mirror_for_locale", lambda: "https://hf-mirror.com"
    )

    order = downloader._mirror_fallback_order("https://mirror.example")

    assert order[:3] == [
        "https://mirror.example",
        "https://hf-mirror.com",
        "https://huggingface.co",
    ]


def test_asr_model_file_lists_use_real_weight_names_not_default_model_bin():
    assert set(downloader._HF_MODEL_FILES["iic/SenseVoiceSmall"]) == {
        "am.mvn",
        "chn_jpn_yue_eng_ko_spectok.bpe.model",
        "config.yaml",
        "configuration.json",
        "model.pt",
    }
    assert "model.bin" not in downloader._HF_MODEL_FILES["iic/SenseVoiceSmall"]


def test_known_models_use_immutable_revisions_and_complete_hash_manifests():
    for model_id in (
        "ku-nlp/deberta-v2-large-japanese-char-wwm",
        "microsoft/deberta-v3-large",
        "hfl/chinese-roberta-wwm-ext-large",
    ):
        files, hashes = downloader._trusted_model_manifest(model_id)
        assert set(files) == set(hashes)
        assert "/resolve/main/" not in downloader._repo_url(
            "https://huggingface.co",
            model_id,
            files[0],
        )


def test_unknown_model_has_no_downloadable_trust_manifest():
    with pytest.raises(RuntimeError, match="No complete pinned integrity manifest"):
        downloader._trusted_model_manifest("owner/unpinned-model")


def test_model_completeness_requires_matching_sha256(monkeypatch, tmp_path):
    model_id = "owner/test-model"
    payload = b"trusted model"
    model_file = tmp_path / "model.bin"
    model_file.write_bytes(payload)
    monkeypatch.setitem(downloader._HF_MODEL_FILES, model_id, ["model.bin"])
    monkeypatch.setitem(downloader._HF_MODEL_REVISIONS, model_id, "a" * 40)
    monkeypatch.setitem(
        downloader._HF_MODEL_FILE_SHA256,
        model_id,
        {"model.bin": hashlib.sha256(payload).hexdigest()},
    )
    monkeypatch.setattr(downloader, "_model_dir", lambda _model_id: tmp_path)
    downloader._VERIFIED_FILE_CACHE.clear()

    assert downloader.model_is_complete(model_id)

    model_file.write_bytes(b"tampered model")
    downloader._VERIFIED_FILE_CACHE.clear()
    assert not downloader.model_is_complete(model_id)


def test_model_url_validator_rejects_private_targets(monkeypatch):
    monkeypatch.setattr(
        downloader.socket,
        "getaddrinfo",
        lambda host, port, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", port))],
    )

    assert _REAL_MODEL_URL_VALIDATOR("https://cdn.example/model.bin")
    assert not _REAL_MODEL_URL_VALIDATOR("http://cdn.example/model.bin")
    assert not _REAL_MODEL_URL_VALIDATOR("https://127.0.0.1/model.bin")
    assert not _REAL_MODEL_URL_VALIDATOR(
        "https://169.254.169.254/latest/meta-data"
    )


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, object] | None = None,
        chunks: Iterable[bytes] = (),
        url: str = "https://mirror.example/model.bin",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = list(chunks)
        self.url = url
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, *, chunk_size: int):
        del chunk_size
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(
        self,
        *,
        head_response: _FakeResponse | None = None,
        get_responses: Iterable[_FakeResponse] = (),
    ) -> None:
        self.head_response = head_response
        self.get_responses = list(get_responses)
        self.head_calls: list[tuple[str, dict[str, object]]] = []
        self.get_calls: list[tuple[str, dict[str, object]]] = []

    def head(self, url: str, **kwargs):
        self.head_calls.append((url, kwargs))
        assert self.head_response is not None
        return self.head_response

    def get(self, url: str, **kwargs):
        self.get_calls.append((url, kwargs))
        assert self.get_responses
        return self.get_responses.pop(0)


def _run_single_download(
    subject: downloader.HFModelDownloader,
    tmp_path,
    response: _FakeResponse,
    *,
    expected_size: int,
    total_bytes_so_far: int = 0,
) -> int:
    subject._session = _FakeSession(get_responses=[response])
    return subject._download_file_single(
        base_url="https://mirror.example",
        filename="model.bin",
        dest=tmp_path / "model.bin",
        file_index=0,
        file_count=1,
        expected_size=expected_size,
        total_bytes_so_far=total_bytes_so_far,
        total_total=max(expected_size, 0),
    )


def test_throughput_probe_rejects_http_final_redirect_and_closes_response(monkeypatch):
    response = _FakeResponse(url="http://mirror.example/model.bin", chunks=[b"data"])
    monkeypatch.setattr(downloader.requests, "get", lambda *_args, **_kwargs: response)

    result = downloader._probe_mirror_throughput(
        "https://mirror.example",
        "owner/model",
        "model.bin",
    )

    assert result is None
    assert response.closed is True


def test_throughput_probe_rejects_unsafe_redirect_before_request(monkeypatch):
    response = _FakeResponse(
        status_code=302,
        headers={"Location": "http://127.0.0.1/private"},
        url="https://mirror.example/model.bin",
    )
    calls = []

    def request(*_args, **_kwargs):
        calls.append(True)
        return response

    monkeypatch.setattr(downloader.requests, "get", request)

    result = downloader._probe_mirror_throughput(
        "https://mirror.example",
        "owner/model",
        "model.bin",
    )

    assert result is None
    assert calls == [True]
    assert response.closed is True


def test_head_probe_rejects_http_final_redirect():
    response = _FakeResponse(
        headers={"Content-Length": "4"},
        url="http://mirror.example/model.bin",
    )
    subject = downloader.HFModelDownloader("owner/model")
    subject._session = _FakeSession(head_response=response)

    with pytest.raises(RuntimeError, match="not trusted|unsafe response URL"):
        subject._head_size("https://mirror.example", "model.bin")

    assert response.closed is True


def test_single_download_rejects_http_final_redirect(tmp_path):
    response = _FakeResponse(
        headers={"Content-Length": "4"},
        chunks=[b"data"],
        url="http://mirror.example/model.bin",
    )
    subject = downloader.HFModelDownloader("owner/model")

    with pytest.raises(RuntimeError, match="not trusted|unsafe response URL"):
        _run_single_download(subject, tmp_path, response, expected_size=4)

    assert response.closed is True
    assert not (tmp_path / "model.bin").exists()


def test_range_probe_rejects_http_final_redirect():
    response = _FakeResponse(
        status_code=206,
        headers={"Content-Range": "bytes 0-2/3", "Content-Length": "3"},
        chunks=[b"abc"],
        url="http://mirror.example/model.bin",
    )
    subject = downloader.HFModelDownloader("owner/model")
    subject._session = _FakeSession(get_responses=[response])

    assert subject._probe_range(
        "https://mirror.example/model.bin",
        (0, 2),
        3,
    ) is False
    assert response.closed is True


def test_segment_download_rejects_http_final_redirect(tmp_path):
    response = _FakeResponse(
        status_code=206,
        headers={"Content-Range": "bytes 0-2/3", "Content-Length": "3"},
        chunks=[b"abc"],
        url="http://mirror.example/model.bin",
    )
    subject = downloader.HFModelDownloader("owner/model")
    subject._session = _FakeSession(get_responses=[response])

    with pytest.raises(RuntimeError, match="not trusted|unsafe response URL"):
        subject._download_segment(
            url="https://mirror.example/model.bin",
            part_path=tmp_path / "model.bin.part0",
            span=(0, 2),
            total_size=3,
            on_chunk=lambda _size: None,
        )

    assert response.closed is True
    assert not (tmp_path / "model.bin.part0").exists()


@pytest.mark.parametrize("value", ["+5", "five", "5, 5", ["5", "5"]])
def test_content_length_rejects_noncanonical_or_duplicate_values(value):
    response = _FakeResponse(headers={"Content-Length": value})

    with pytest.raises(RuntimeError, match="invalid Content-Length"):
        downloader._response_content_length(response, label="model.bin")


def test_single_download_rejects_zero_content_length_with_nonempty_body(tmp_path):
    response = _FakeResponse(
        headers={"Content-Length": "0"},
        chunks=[b"unexpected"],
    )
    subject = downloader.HFModelDownloader("owner/model")

    with pytest.raises(RuntimeError, match="was empty"):
        _run_single_download(subject, tmp_path, response, expected_size=0)

    assert not (tmp_path / "model.bin").exists()
    assert not (tmp_path / "model.bin.part").exists()


def test_single_download_rejects_truncated_known_size_response(tmp_path):
    response = _FakeResponse(
        headers={"Content-Length": "5"},
        chunks=[b"abc"],
    )
    subject = downloader.HFModelDownloader("owner/model")

    with pytest.raises(RuntimeError, match="was truncated"):
        _run_single_download(subject, tmp_path, response, expected_size=5)

    assert (tmp_path / "model.bin.part").read_bytes() == b"abc"


def test_single_download_rejects_overflowing_known_size_response(tmp_path):
    response = _FakeResponse(
        headers={"Content-Length": "5"},
        chunks=[b"abcdef"],
    )
    subject = downloader.HFModelDownloader("owner/model")

    with pytest.raises(RuntimeError, match="exceeded its allowed size"):
        _run_single_download(subject, tmp_path, response, expected_size=5)

    assert (tmp_path / "model.bin.part").read_bytes() == b""


def test_unknown_size_download_stops_at_byte_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "_MAX_MODEL_FILE_BYTES", 5)
    monkeypatch.setattr(downloader, "_MAX_MODEL_TOTAL_BYTES", 5)
    response = _FakeResponse(chunks=[b"abc", b"def"])
    subject = downloader.HFModelDownloader("owner/model")

    with pytest.raises(RuntimeError, match="exceeded its allowed size"):
        _run_single_download(subject, tmp_path, response, expected_size=0)

    assert (tmp_path / "model.bin.part").read_bytes() == b"abc"


@pytest.mark.parametrize(
    "content_range, requested_start, requested_end, expected_total",
    [
        ("", 0, 4, 5),
        ("bytes 1-4/5", 0, 4, 5),
        ("bytes 0-3/5", 0, 4, 5),
        ("bytes 0-4/6", 0, 4, 5),
        ("items 0-4/5", 0, 4, 5),
    ],
)
def test_content_range_rejects_invalid_or_mismatched_values(
    content_range,
    requested_start,
    requested_end,
    expected_total,
):
    response = _FakeResponse(
        status_code=206,
        headers={
            "Content-Range": content_range,
            "Content-Length": str(requested_end - requested_start + 1),
        },
    )

    with pytest.raises(RuntimeError, match="Content-Range"):
        downloader._validate_content_range(
            response,
            requested_start=requested_start,
            requested_end=requested_end,
            expected_total=expected_total,
            label="segment",
        )


def test_mirror_switch_clears_single_and_parallel_partial_files(monkeypatch, tmp_path):
    subject = downloader.HFModelDownloader("owner/model")
    dest = tmp_path / "model.bin"
    partials = [
        subject._single_partial_path(dest),
        *subject._parallel_part_paths(dest),
    ]
    for index, path in enumerate(partials):
        path.write_bytes(bytes([index + 1]))

    calls: list[str] = []

    def fake_download_file(**kwargs):
        calls.append(kwargs["base_url"])
        if len(calls) == 1:
            assert all(path.exists() for path in partials)
            raise RuntimeError("first mirror failed")
        assert all(not path.exists() for path in partials)
        return 7

    monkeypatch.setattr(subject, "_download_file", fake_download_file)

    result = subject._download_file_with_fallback(
        base_urls=["https://one.example", "https://two.example"],
        filename="model.bin",
        dest=dest,
        file_index=0,
        file_count=1,
        expected_size=7,
        total_bytes_so_far=0,
        total_total=7,
    )

    assert result == 7
    assert calls == ["https://one.example", "https://two.example"]


def test_user_cancellation_during_mirror_failure_stops_fallback(monkeypatch, tmp_path):
    subject = downloader.HFModelDownloader("owner/model")
    calls: list[str] = []

    def fake_download_file(**kwargs):
        calls.append(kwargs["base_url"])
        subject.cancel()
        raise RuntimeError("cancelled while mirror failed")

    monkeypatch.setattr(subject, "_download_file", fake_download_file)

    result = subject._download_file_with_fallback(
        base_urls=["https://one.example", "https://two.example"],
        filename="model.bin",
        dest=tmp_path / "model.bin",
        file_index=0,
        file_count=1,
        expected_size=7,
        total_bytes_so_far=3,
        total_total=10,
    )

    assert result == 3
    assert calls == ["https://one.example"]
    assert subject.state == downloader.DownloadState.CANCELLED
    assert subject._cancel_event.is_set()


def test_internal_worker_cancellation_is_cleared_before_next_mirror(monkeypatch, tmp_path):
    subject = downloader.HFModelDownloader("owner/model")
    calls: list[str] = []

    def fake_download_file(**kwargs):
        calls.append(kwargs["base_url"])
        if len(calls) == 1:
            subject._cancel_event.set()
            raise RuntimeError("range worker failed")
        assert subject.state != downloader.DownloadState.CANCELLED
        assert not subject._cancel_event.is_set()
        return 9

    monkeypatch.setattr(subject, "_download_file", fake_download_file)

    result = subject._download_file_with_fallback(
        base_urls=["https://one.example", "https://two.example"],
        filename="model.bin",
        dest=tmp_path / "model.bin",
        file_index=0,
        file_count=1,
        expected_size=9,
        total_bytes_so_far=0,
        total_total=9,
    )

    assert result == 9
    assert calls == ["https://one.example", "https://two.example"]
    assert not subject._cancel_event.is_set()


def test_start_refuses_restart_while_previous_thread_is_alive(monkeypatch):
    class AliveThread:
        @staticmethod
        def is_alive() -> bool:
            return True

    subject = downloader.HFModelDownloader("owner/model")
    subject._thread = AliveThread()
    subject._progress.state = downloader.DownloadState.ERROR
    subject._progress.error = "previous failure"
    subject._speed_samples.append((1.0, 2))
    monkeypatch.setattr(
        downloader.threading,
        "Thread",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a second thread must not be created")
        ),
    )

    subject.start()

    assert subject.state == downloader.DownloadState.ERROR
    assert subject.progress.error == "previous failure"
    assert list(subject._speed_samples) == [(1.0, 2)]
