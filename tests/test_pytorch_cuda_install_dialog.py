from __future__ import annotations

from src.ui_qt import pytorch_cuda_install_dialog


def test_cuda_installer_keeps_external_tool_output_out_of_localized_ui(
    qtbot,
    monkeypatch,
):
    monkeypatch.setattr(
        pytorch_cuda_install_dialog.PytorchCudaInstallDialog,
        "_start_flow",
        lambda self: None,
    )
    dialog = pytorch_cuda_install_dialog.PytorchCudaInstallDialog("ru")
    qtbot.addWidget(dialog)

    class FakeProcess:
        def readAllStandardOutput(self):
            return b"Downloading torch 50%\nSuccessfully installed torch\n"

        def readAllStandardError(self):
            return b"pip technical warning\n"

    dialog._current_step = "install_cuda"
    dialog._process = FakeProcess()
    dialog._read_process_output()

    assert "Downloading torch" not in dialog._log.toPlainText()
    assert "pip technical warning" not in dialog._log.toPlainText()
    assert dialog._progress_bar.value() >= 76
    assert dialog._progress_bar.format().endswith("%")


def test_cuda_installer_step_log_uses_localized_status(qtbot, monkeypatch):
    monkeypatch.setattr(
        pytorch_cuda_install_dialog.PytorchCudaInstallDialog,
        "_start_flow",
        lambda self: None,
    )
    dialog = pytorch_cuda_install_dialog.PytorchCudaInstallDialog("ja")
    qtbot.addWidget(dialog)

    assert dialog._status_for_step("verify") != "Verifying installation"
    assert "確認" in dialog._status_for_step("verify")
