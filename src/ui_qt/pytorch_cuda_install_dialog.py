from __future__ import annotations

import re
import time

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from src.utils.gpu_support import (
    cuda_pytorch_installed,
    cuda_install_environment_vars,
    pip_bootstrap_commands,
    pip_check_command,
    pytorch_cuda_install_command,
    pytorch_cuda_verify_command,
)
from src.utils.i18n import tr


_PERCENT_RE = re.compile(r"(\d{1,3})%")


class PytorchCudaInstallDialog(QDialog):
    _STEP_PROGRESS = {
        "check_pip": 4,
        "bootstrap_pip": 12,
        "upgrade_pip": 22,
        "install_cuda": 32,
        "verify": 92,
    }

    def __init__(self, ui_language: str, parent=None) -> None:
        super().__init__(parent)
        self._ui_language = ui_language
        self._process: QProcess | None = None
        self._current_step = ""
        self._paused_step = ""
        self._paused = False
        self._cancelled = False
        self._failed = False
        self._finished = False
        self._percent = 0
        self._started_at = time.monotonic()

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._refresh_elapsed)

        self.setWindowTitle(tr(self._ui_language, "tts_gpu_install_title"))
        self.resize(760, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel(tr(self._ui_language, "tts_gpu_install_title"))
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        self._status_label = QLabel(tr(self._ui_language, "tts_gpu_install_starting"))
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("%p%")
        layout.addWidget(self._progress_bar)

        self._elapsed_label = QLabel("")
        self._elapsed_label.setObjectName("hintLabel")
        layout.addWidget(self._elapsed_label)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self._log, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self._pause_btn = QPushButton(tr(self._ui_language, "tts_gpu_install_pause"))
        self._pause_btn.clicked.connect(self._toggle_pause)
        row.addWidget(self._pause_btn)

        self._cancel_btn = QPushButton(tr(self._ui_language, "tts_gpu_install_cancel"))
        self._cancel_btn.clicked.connect(self._cancel_install)
        row.addWidget(self._cancel_btn)

        self._retry_btn = QPushButton(tr(self._ui_language, "tts_gpu_install_retry"))
        self._retry_btn.clicked.connect(self._retry_install)
        self._retry_btn.hide()
        row.addWidget(self._retry_btn)

        self._close_btn = QPushButton(tr(self._ui_language, "close"))
        self._close_btn.clicked.connect(self.close)
        row.addWidget(self._close_btn)
        layout.addLayout(row)

        self._elapsed_timer.start()
        self._refresh_elapsed()
        self._start_flow()

    def _append_log(self, text: str) -> None:
        if not text:
            return
        self._log.moveCursor(QTextCursor.MoveOperation.End)
        self._log.insertPlainText(text)
        self._log.moveCursor(QTextCursor.MoveOperation.End)
        self._update_progress_from_output(text)

    def _set_percent(self, value: int) -> None:
        self._percent = max(self._percent, max(0, min(100, int(value))))
        self._progress_bar.setValue(self._percent)

    def _refresh_elapsed(self) -> None:
        elapsed = max(0, int(time.monotonic() - self._started_at))
        minutes, seconds = divmod(elapsed, 60)
        self._elapsed_label.setText(
            tr(
                self._ui_language,
                "tts_gpu_install_elapsed",
                percent=self._progress_bar.value(),
                elapsed=f"{minutes:02d}:{seconds:02d}",
            )
        )

    def _start_flow(self) -> None:
        self._cancelled = False
        self._failed = False
        self._finished = False
        self._started_with_existing_cuda = False
        self._paused = False
        self._paused_step = ""
        self._started_at = time.monotonic()
        self._percent = 0
        self._progress_bar.setValue(0)
        self._retry_btn.hide()
        self._pause_btn.show()
        self._cancel_btn.show()
        self._pause_btn.setEnabled(True)
        self._pause_btn.setText(tr(self._ui_language, "tts_gpu_install_pause"))
        self._status_label.setText(tr(self._ui_language, "tts_gpu_install_starting"))
        self._started_with_existing_cuda = cuda_pytorch_installed()
        if self._started_with_existing_cuda:
            self._append_log("\n" + tr(self._ui_language, "tts_gpu_install_already_installed") + "\n")
            self._run_step("verify")
            return
        self._run_step("check_pip")

    def _run_step(self, step: str) -> None:
        command = self._command_for_step(step)
        if command is None:
            self._fail(tr(self._ui_language, "tts_gpu_install_unsupported"))
            return
        self._current_step = step
        self._set_percent(self._STEP_PROGRESS.get(step, self._percent))
        self._status_label.setText(self._status_for_step(step))
        program, args = command
        self._append_log(f"\n> {program} {' '.join(args)}\n")
        if step in {"bootstrap_pip", "upgrade_pip", "install_cuda"}:
            self._append_log(tr(self._ui_language, "tts_gpu_install_cache_hint") + "\n")

        process = QProcess(self)
        self._process = process
        process.setProgram(program)
        process.setArguments(args)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        for key, value in cuda_install_environment_vars().items():
            env.insert(key, value)
        process.setProcessEnvironment(env)
        process.readyReadStandardOutput.connect(self._read_process_output)
        process.readyReadStandardError.connect(self._read_process_output)
        process.finished.connect(self._on_step_finished)
        process.start()

    def _command_for_step(self, step: str) -> tuple[str, list[str]] | None:
        if step == "check_pip":
            return pip_check_command()
        if step == "bootstrap_pip":
            commands = pip_bootstrap_commands()
            return commands[0] if commands else None
        if step == "upgrade_pip":
            commands = pip_bootstrap_commands()
            return commands[1] if len(commands) > 1 else None
        if step == "install_cuda":
            return pytorch_cuda_install_command()
        if step == "verify":
            return pytorch_cuda_verify_command()
        return None

    def _status_for_step(self, step: str) -> str:
        keys = {
            "check_pip": "tts_gpu_install_checking_pip",
            "bootstrap_pip": "tts_gpu_install_pip_missing",
            "upgrade_pip": "tts_gpu_install_pip_readying",
            "install_cuda": "tts_gpu_install_running",
            "verify": "tts_gpu_install_verifying",
        }
        return tr(self._ui_language, keys.get(step, "tts_gpu_install_running"))

    def _read_process_output(self) -> None:
        process = self._process
        if process is None:
            return
        stdout = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        stderr = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
        self._append_log(stdout + stderr)

    def _on_step_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        if self._paused or self._cancelled:
            return
        step = self._current_step
        self._process = None

        if step == "check_pip":
            if exit_code == 0:
                self._set_percent(18)
                self._run_step("install_cuda")
            else:
                self._append_log("\n" + tr(self._ui_language, "tts_gpu_install_pip_missing") + "\n")
                self._run_step("bootstrap_pip")
            return

        if step == "bootstrap_pip":
            if exit_code == 0:
                self._set_percent(20)
                self._run_step("upgrade_pip")
            else:
                self._fail(tr(self._ui_language, "tts_gpu_pip_install_failed"))
            return

        if step == "upgrade_pip":
            if exit_code == 0:
                self._set_percent(30)
                self._run_step("install_cuda")
            else:
                self._fail(tr(self._ui_language, "tts_gpu_pip_install_failed"))
            return

        if step == "install_cuda":
            if exit_code == 0:
                self._set_percent(90)
                self._run_step("verify")
            else:
                self._fail(tr(self._ui_language, "tts_gpu_install_failed"))
            return

        if step == "verify":
            self._finished = True
            self._pause_btn.hide()
            self._cancel_btn.hide()
            self._elapsed_timer.stop()
            if exit_code == 0:
                self._set_percent(100)
                self._status_label.setText(tr(self._ui_language, "tts_gpu_install_done"))
                self._append_log("\n" + tr(self._ui_language, "tts_gpu_install_done") + "\n")
            else:
                key = (
                    "tts_gpu_install_existing_verify_failed"
                    if self._started_with_existing_cuda
                    else "tts_gpu_install_verify_failed"
                )
                self._fail(tr(self._ui_language, key))

    def _update_progress_from_output(self, text: str) -> None:
        if not text:
            return
        if self._current_step == "install_cuda":
            for match in _PERCENT_RE.findall(text):
                try:
                    self._set_percent(32 + int(int(match) * 0.55))
                except ValueError:
                    pass
            lowered = text.lower()
            if "collecting " in lowered:
                self._set_percent(36)
            if "downloading " in lowered:
                self._set_percent(45)
            if "installing collected packages" in lowered:
                self._set_percent(76)
            if "successfully installed" in lowered:
                self._set_percent(90)
        elif self._current_step == "bootstrap_pip":
            if "successfully installed" in text.lower():
                self._set_percent(20)
        elif self._current_step == "upgrade_pip":
            if "successfully installed" in text.lower() or "requirement already satisfied" in text.lower():
                self._set_percent(30)

    def _toggle_pause(self) -> None:
        if self._paused:
            step = self._paused_step or self._current_step
            self._paused = False
            self._paused_step = ""
            self._pause_btn.setText(tr(self._ui_language, "tts_gpu_install_pause"))
            self._append_log("\n" + tr(self._ui_language, "tts_gpu_install_resuming") + "\n")
            self._run_step(step)
            return

        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        self._paused = True
        self._paused_step = self._current_step
        self._status_label.setText(tr(self._ui_language, "tts_gpu_install_paused"))
        self._pause_btn.setText(tr(self._ui_language, "tts_gpu_install_resume"))
        self._append_log("\n" + tr(self._ui_language, "tts_gpu_install_paused") + "\n")
        process.kill()

    def _cancel_install(self) -> None:
        self._cancelled = True
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
        self._process = None
        self._status_label.setText(tr(self._ui_language, "tts_gpu_install_cancelled"))
        self._pause_btn.hide()
        self._cancel_btn.hide()
        self._retry_btn.show()
        self._elapsed_timer.stop()

    def _retry_install(self) -> None:
        self._append_log("\n" + tr(self._ui_language, "tts_gpu_install_retry") + "\n")
        self._start_flow()

    def _fail(self, message: str) -> None:
        self._failed = True
        self._finished = True
        self._status_label.setText(message)
        self._append_log("\n" + message + "\n")
        self._pause_btn.hide()
        self._cancel_btn.hide()
        self._retry_btn.show()
        self._elapsed_timer.stop()

    def closeEvent(self, event) -> None:
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
        event.accept()
