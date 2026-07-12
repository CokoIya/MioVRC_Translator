from src.ui_qt.main_window import DESKTOP_SOURCE, MIC_SOURCE, MainWindow


class _ASR:
    def __init__(self, device: str, supports_partial: bool = True) -> None:
        self.runtime_device = device
        self.supports_partial = supports_partial


def _window(engine: str, device: str, supports_partial: bool = True):
    window = MainWindow.__new__(MainWindow)
    window._config = {"asr": {"engine": engine}}
    window._asr = _ASR(device, supports_partial)
    window._listen_asr = window._asr
    return window


def test_cpu_online_asr_skips_partial_asr():
    window = _window("qwen3-asr", "cpu", supports_partial=False)

    assert window._should_process_partial_asr(MIC_SOURCE) is False


def test_cuda_online_asr_skips_partial_asr():
    window = _window("qwen3-asr", "cuda", supports_partial=False)

    assert window._should_process_partial_asr(MIC_SOURCE) is False


def test_cpu_sensevoice_skips_partial_asr():
    window = _window("sensevoice-small", "cpu")

    assert window._should_process_partial_asr(MIC_SOURCE) is False


def test_cuda_sensevoice_keeps_partial_asr():
    window = _window("sensevoice-small", "cuda")

    assert window._should_process_partial_asr(MIC_SOURCE) is True


def test_desktop_partial_asr_is_disabled_to_avoid_cross_source_contention():
    window = _window("sensevoice-small", "cuda")

    assert window._should_process_partial_asr(DESKTOP_SOURCE) is False


def test_partial_result_waits_for_configured_compatible_stability_hits():
    window = _window("sensevoice-small", "cuda")
    window._config["asr"]["streaming"] = {"partial_stability_hits": 2}
    displayed: list[str] = []
    window._set_source_text = displayed.append

    window._on_partial_result("hello", MIC_SOURCE)
    window._on_partial_result("hello world", MIC_SOURCE)

    assert displayed == ["hello world"]


def test_partial_result_never_writes_desktop_text_into_microphone_pane():
    window = _window("sensevoice-small", "cuda")
    displayed: list[str] = []
    window._set_source_text = displayed.append

    window._on_partial_result("desktop speech", DESKTOP_SOURCE)

    assert displayed == []
