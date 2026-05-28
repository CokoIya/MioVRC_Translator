from src.ui_qt.main_window import MIC_SOURCE, MainWindow


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
