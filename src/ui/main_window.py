import logging
import queue
import re
import threading
import time
import webbrowser
import sys
import subprocess
from collections import deque
from pathlib import Path
import customtkinter as ctk
from tkinter import messagebox, PhotoImage
import sounddevice as sd

from src.utils import config_manager
from src.utils.i18n import tr
from src.utils.translation_error_formatter import format_translation_error
from src.utils.ui_config import (
    ASR_HINT_LANGUAGE_CODES,
    UI_LANGUAGE_OPTIONS,
    get_manual_source_language_options,
    get_target_language_options,
    get_ui_language,
    normalize_output_format,
    target_language_osc_value,
)
from src.audio.recorder import AudioRecorder
from src.audio.desktop_recorder import (
    DesktopAudioRecorder,
    list_output_devices as _list_desktop_output_devices,
    _loopback_name_candidates,
)
from src.audio.windows_audio import (
    default_output_device_name,
    detect_process_output_device_name,
    inspect_process_output_state,
    is_process_running,
)
from src.asr.factory import create_asr
from src.asr.model_manager import download_model, model_exists
from src.asr.model_registry import get_asr_runtime_spec
from src.asr.streaming_merger import StreamingMerger
from src.translators.factory import create_translator
from src.osc.sender import VRCOSCSender
from src.utils.lang_detect import detect_language
from .floating_window import FloatingWindow
from .settings_window import SettingsWindow
from .window_effects import apply_window_icon, present_popup
from src.updater.update_checker import check_for_update
from src.ui.update_window import UpdateWindow

BG_PRIMARY = "#f5f5f7"
BG_SECONDARY = "#eef2f7"
BG_TOP = "#f3f4f6"
BG_PANEL = "#ffffff"
GLASS_BG = "#f7f9fc"
GLASS_BORDER = "#d6dbe4"
GLASS_HOVER = "#e9edf5"
ACCENT = "#0a84ff"
ACCENT_HOVER = "#006ae6"
DANGER = "#ff453a"
DANGER_HOVER = "#e2352b"
SUCCESS = "#34c759"
TEXT_PRI = "#1d1d1f"
TEXT_SEC = "#6e6e73"
DIVIDER = "#e0e4eb"
GITHUB_REPO_URL = "https://github.com/CokoIya/MioVRC_Translator"
QQ_GROUP_URL = "https://qm.qq.com/q/1PThd3QBTS"
LINE_GROUP_URL = "https://line.me/ti/g2/uLhASjhfQcsd5tYsEpFr8GWsCcuYVIq1I6iGwA?utm_source=invitation&utm_medium=link_copy&utm_campaign=default"
ICON_GITHUB_FILE = "github.png"
ICON_QQ_FILE = "qq.png"
ICON_LINE_FILE = "line.png"
ICON_SPONSOR_FILE = "sponsor.png"
APP_ICON_ICO_FILE = "app_icon_mio.ico"
APP_ICON_PNG_FILE = "app_icon_mio.png"
SPONSOR_IMAGE_CANDIDATES = (
    "zanzhu.png",
    "sponsor_qr.png",
    "sponsor_qr.jpg",
    "sponsor_qr.jpeg",
    "sponsor.png",
    "sponsor.jpg",
)

PARTIAL_TASK_QUEUE_MAXSIZE = 1
FINAL_TASK_QUEUE_MAXSIZE = 8
CONFIG_SAVE_DEBOUNCE_MS = 280
MIC_SOURCE = "mic"
LISTEN_SOURCE = "vrc_listen"
DESKTOP_SOURCE = LISTEN_SOURCE
LISTEN_PREFIX = "[听]"
MIN_CHATBOX_COOLDOWN_S = 1.6
DEFAULT_LISTEN_SELF_SUPPRESS_S = 0.65
DEFAULT_LISTEN_SEGMENT_DURATION_S = 2.0
DEFAULT_LISTEN_TAIL_SILENCE_S = 1.2
LISTEN_RESULT_DEDUPE_WINDOW_S = 1.6
AUTO_INPUT_DEVICE_TOKEN = "__auto_follow_default__"
LISTEN_DIAGNOSTIC_IDLE_S = 15.0
logger = logging.getLogger(__name__)

MAIN_COPY = {
    "settings_short": {
        "zh-CN": "设置",
        "en": "Settings",
        "ja": "設定",
        "ru": "Settings",
        "ko": "설정",
    },
    "guide_short": {
        "zh-CN": "开 OSC",
        "en": "OSC",
        "ja": "OSC",
        "ru": "OSC",
        "ko": "OSC",
    },
    "ui_badge": {
        "zh-CN": "界面",
        "en": "UI",
        "ja": "表示",
        "ru": "UI",
        "ko": "UI",
    },
    "desktop_audio_off": {
        "zh-CN": "反向翻译",
        "en": "Reverse TL",
        "ja": "逆翻訳",
        "ru": "Reverse TL",
        "ko": "역번역",
    },
    "desktop_audio_on": {
        "zh-CN": "反向翻译",
        "en": "Reverse TL",
        "ja": "逆翻訳",
        "ru": "Reverse TL",
        "ko": "역번역",
    },
    "desktop_audio_unavailable_title": {
        "zh-CN": "暂时无法开启反向翻译",
        "en": "VRC Listen Unavailable",
        "ja": "VRC 音声リスンを開始できません",
        "ru": "VRC Listen Unavailable",
        "ko": "VRC 음성 리슨을 사용할 수 없습니다",
    },
    "desktop_audio_unavailable_body": {
        "zh-CN": "没有找到可用的播放设备。你也可以去设置里手动选一个耳机或音箱。",
        "en": "No playback device was found. You can also choose a headset or speaker manually in Settings.",
        "ja": "使える再生デバイスが見つかりません。必要なら設定でヘッドセットやスピーカーを手動で選べます。",
        "ru": "No playback device was found. You can also choose a headset or speaker manually in Settings.",
        "ko": "사용 가능한 재생 장치를 찾지 못했습니다. 필요하면 설정에서 헤드셋이나 스피커를 직접 고를 수 있습니다.",
    },
    "desktop_audio_failed": {
        "zh-CN": "反向翻译启动失败：{message}",
        "en": "VRC listen failed: {message}",
        "ja": "VRC 音声リスンの開始に失敗しました: {message}",
        "ru": "VRC listen failed: {message}",
        "ko": "VRC 음성 리슨 시작 실패: {message}",
    },
    "desktop_audio_runtime_stopped": {
        "zh-CN": "桌面音频采集已中断，请检查当前播放设备后重新开启反向翻译。",
        "en": "Desktop audio capture stopped. Check the current playback device and enable VRC listen again.",
        "ja": "デスクトップ音声の取り込みが中断しました。現在の再生デバイスを確認して、もう一度有効にしてください。",
        "ru": "Desktop audio capture stopped. Check the current playback device and enable VRC listen again.",
        "ko": "데스크톱 오디오 캡처가 중단되었습니다. 현재 재생 장치를 확인한 뒤 다시 켜 주세요.",
    },
    "desktop_audio_saved": {
        "zh-CN": "反向翻译已切换",
        "en": "VRC listen updated",
        "ja": "VRC 音声リスンを更新しました",
        "ru": "VRC listen updated",
        "ko": "VRC 음성 리슨이 변경되었습니다",
    },
    "history_resend_sent": {
        "zh-CN": "已将选中记录重新发送到 VRC",
        "en": "Resent the selected record to VRC",
        "ja": "選択した履歴を VRC に再送しました",
        "ru": "Выбранная запись снова отправлена в VRC",
        "ko": "선택한 기록을 VRC로 다시 보냈습니다",
    },
    "desktop_audio_requires_vrchat": {
        "zh-CN": "请先运行 VRChat 后再开启反向翻译。",
        "en": "Start VRChat first, then enable VRC listen.",
        "ja": "先に VRChat を起動してから有効にしてください。",
        "ru": "Сначала запустите VRChat, затем включите прослушивание.",
        "ko": "먼저 VRChat을 실행한 뒤 켜 주세요.",
    },
    "desktop_audio_requires_translation": {
        "zh-CN": "请先把聊天框输出格式改成不是“仅原文”后，再开启反向翻译。",
        "en": "Change the chatbox output format to something other than Original Only before enabling VRC listen.",
        "ja": "VRC リスンを有効にする前に、チャット出力形式を「原文のみ」以外へ変更してください。",
        "ru": "Перед включением VRC listen смените формат вывода чата на любой, кроме Original Only.",
        "ko": "VRC 리슨을 켜기 전에 채팅 출력 형식을 '원문만' 이외로 바꿔 주세요.",
    },
    "listen_overlay_off": {
        "zh-CN": "悬浮窗",
        "en": "Overlay",
        "ja": "オーバーレイ",
        "ru": "Overlay",
        "ko": "오버레이",
    },
    "listen_overlay_on": {
        "zh-CN": "悬浮窗",
        "en": "Overlay",
        "ja": "オーバーレイ",
        "ru": "Overlay",
        "ko": "오버레이",
    },
    "mic_device_auto_option": {
        "zh-CN": "自动跟随系统默认",
        "en": "Auto Follow System Default",
        "ja": "システム既定を自動追従",
        "ru": "Авто следовать системному устройству",
        "ko": "시스템 기본 장치 자동 추종",
    },
    "mic_device_auto_current": {
        "zh-CN": "自动 · {name}",
        "en": "Auto · {name}",
        "ja": "自動 · {name}",
        "ru": "Авто · {name}",
        "ko": "자동 · {name}",
    },
    "mic_device_none": {
        "zh-CN": "未检测到麦克风",
        "en": "No microphone detected",
        "ja": "マイクが見つかりません",
        "ru": "Микрофон не найден",
        "ko": "마이크를 찾지 못했습니다",
    },
    "mic_auto_switch_notice": {
        "zh-CN": "麦克风已自动切换到：{name}",
        "en": "Microphone auto-switched to: {name}",
        "ja": "マイクを自動切替しました: {name}",
        "ru": "Микрофон автоматически переключен: {name}",
        "ko": "마이크가 자동으로 전환되었습니다: {name}",
    },
    "mic_missing_fallback_notice": {
        "zh-CN": "原固定麦克风不可用，已临时回退到系统默认：{name}",
        "en": "Configured microphone is unavailable. Falling back to system default: {name}",
        "ja": "固定マイクが使えないため、システム既定へ一時的に切り替えました: {name}",
        "ru": "Выбранный микрофон недоступен. Выполнен откат на системный микрофон: {name}",
        "ko": "고정 마이크를 사용할 수 없어 시스템 기본 장치로 임시 전환했습니다: {name}",
    },
    "mic_runtime_stopped": {
        "zh-CN": "麦克风采集已中断，请检查输入设备。",
        "en": "Microphone capture stopped. Check the current input device.",
        "ja": "マイク入力が中断しました。現在の入力デバイスを確認してください。",
        "ru": "Захват микрофона остановился. Проверьте текущее устройство ввода.",
        "ko": "마이크 캡처가 중단되었습니다. 현재 입력 장치를 확인해 주세요.",
    },
    "update_badge": {
        "zh-CN": "有更新",
        "en": "Update",
        "ja": "更新あり",
        "ru": "Обновление",
        "ko": "업데이트",
    },
}

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


class MainWindow(ctk.CTk):
    def __init__(self, config: dict):
        super().__init__()
        self._config = config
        self._ui_lang = get_ui_language(config)
        self.title(tr(self._ui_lang, "window_title"))
        self.geometry("860x500")
        self.minsize(760, 450)
        self.configure(fg_color=BG_PRIMARY)

        self._recorder: AudioRecorder | None = None
        self._listen_recorder: AudioRecorder | None = None
        self._asr = create_asr(config)
        self._listen_asr = create_asr(config)
        self._translator = None
        self._sender: VRCOSCSender | None = None
        self._translating = False
        self._src_placeholder = tr(self._ui_lang, "source_placeholder")
        self._src_text = ""
        self._src_rendered_text = ""
        self._src_rendered_color = TEXT_SEC
        self._src_rendered_count = 0
        self._last_tgt_text = ""
        self._tgt_rendered_text = ""

        self._running = False
        self._listen_session = 0
        self._merge_lock = threading.Lock()
        self._partial_generation = 0
        self._desktop_partial_generation = 0
        self._partial_merger = self._create_streaming_merger()
        self._desktop_partial_merger = self._create_streaming_merger()
        self._partial_task_queues: dict[
            str,
            queue.Queue[tuple[object, str | None, int, int, str] | None],
        ] = {
            MIC_SOURCE: queue.Queue(maxsize=PARTIAL_TASK_QUEUE_MAXSIZE),
            DESKTOP_SOURCE: queue.Queue(maxsize=PARTIAL_TASK_QUEUE_MAXSIZE),
        }
        self._final_task_queues: dict[
            str,
            queue.Queue[tuple[object, str | None, str | None, int, str] | None],
        ] = {
            MIC_SOURCE: queue.Queue(maxsize=FINAL_TASK_QUEUE_MAXSIZE),
            DESKTOP_SOURCE: queue.Queue(maxsize=FINAL_TASK_QUEUE_MAXSIZE),
        }
        self._current_tgt_lang: str = self._config.get("translation", {}).get("target_language", "ja")
        self._current_src_lang: str | None = None
        self._current_asr_lang: str | None = None
        self._desktop_capture_enabled = bool(
            self._vrc_listen_config().get("enabled", False)
        )
        self._listen_overlay_enabled = bool(
            self._vrc_listen_config().get("show_overlay", False)
        )
        self._desktop_devices: dict[str, int] = {}
        self._mic_in_speech = False
        self._desktop_in_speech = False
        self._listen_running = False
        self._listen_available = False
        self._listen_unavailable_reason: str | None = None
        self._listen_transition_lock = threading.Lock()
        self._listen_transitioning = False
        self._listen_toggle_cooldown_until = 0.0
        self._translation_state_lock = threading.Lock()
        self._active_translation_jobs = 0
        self._listen_send_lock = threading.Lock()
        self._listen_last_send_at = 0.0
        self._own_msgs: set[str] = set()
        self._last_mic_activity_at = 0.0
        self._last_mic_result_at = 0.0
        self._recent_mic_texts: deque[tuple[float, str]] = deque(maxlen=8)
        self._recent_listen_texts: deque[tuple[float, str]] = deque(maxlen=8)
        self._last_listen_result_at = 0.0
        self._last_listen_diagnostic_log_at = 0.0
        self._avatar_error_after_id: str | None = None
        self._settings_window: SettingsWindow | None = None
        self._device_picker_win: ctk.CTkToplevel | None = None
        self._sponsor_win: ctk.CTkToplevel | None = None
        self._floating_window: FloatingWindow | None = None
        self._social_icons: dict[str, ctk.CTkImage] = {}
        self._window_icon: PhotoImage | None = None
        self._status_text = self._t("status_ready")
        self._status_color = SUCCESS
        self._bottom_text = (
            self._t("model_ready")
            if model_exists(self._asr_model_spec())
            else self._t("model_unloaded")
        )
        self._bottom_progress_visible = False
        self._bottom_progress_value = 0.0
        self._bottom_progress_indeterminate = False
        self._bottom_progress_running = False
        self._model_prepare_running = False
        self._config_save_after_id: str | None = None
        self._desktop_audio_watch_after_id: str | None = None
        self._mic_audio_watch_after_id: str | None = None
        self._destroying = False
        self._active_listen_output_device_name: str | None = None
        self._last_desktop_device_signature: tuple[tuple[str, ...], str | None] | None = None
        self._listen_recovery_in_progress = False
        self._active_mic_input_device_name: str | None = None
        self._last_mic_device_signature: tuple[
            tuple[str, ...],
            str | None,
            str,
            str | None,
            str | None,
        ] | None = None
        self._mic_recovery_in_progress = False

        self._set_window_icon()
        self._start_background_workers()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._build()
        self._load_devices()
        self._sync_all_avatar_params(force=True)
        self.after(300, self._maybe_show_osc_guide)
        self._schedule_desktop_audio_watch(2200)
        self._schedule_mic_audio_watch(2200)
        self.after(3000, self._check_for_update)
        self._update_recheck_ms = 30 * 60 * 1000  # 30 minutes
        logger.info("MainWindow initialized")

    def _t(self, key: str, **kwargs) -> str:
        return tr(self._ui_lang, key, **kwargs)

    def _copy(self, key: str, **kwargs) -> str:
        values = MAIN_COPY.get(key, {})
        if self._ui_lang in values:
            template = values[self._ui_lang]
        else:
            base_lang = self._ui_lang.split("-", 1)[0]
            template = next(
                (
                    text
                    for lang, text in values.items()
                    if lang.split("-", 1)[0] == base_lang
                ),
                values.get("en", key),
            )
        if kwargs:
            return template.format(**kwargs)
        return template

    def _format_translation_error(self, error: object):
        translation_cfg = self._config.get("translation", {})
        backend = translation_cfg.get("backend")
        return format_translation_error(error, backend=backend, ui_language=self._ui_lang)

    def _start_background_workers(self) -> None:
        self._partial_workers: dict[str, threading.Thread] = {}
        self._final_workers: dict[str, threading.Thread] = {}
        for source in (MIC_SOURCE, DESKTOP_SOURCE):
            partial_worker = threading.Thread(
                target=self._partial_worker_loop,
                args=(source,),
                daemon=True,
                name=f"partial-{source}",
            )
            partial_worker.start()
            self._partial_workers[source] = partial_worker

            final_worker = threading.Thread(
                target=self._final_worker_loop,
                args=(source,),
                daemon=True,
                name=f"final-{source}",
            )
            final_worker.start()
            self._final_workers[source] = final_worker
        logger.debug("Background workers started for sources: %s", list(self._partial_workers))

    @staticmethod
    def _drain_queue(work_queue: queue.Queue) -> None:
        while True:
            try:
                work_queue.get_nowait()
            except queue.Empty:
                return

    @staticmethod
    def _enqueue_latest(work_queue: queue.Queue, payload) -> str:
        try:
            work_queue.put_nowait(payload)
            return "enqueued"
        except queue.Full:
            pass

        try:
            work_queue.get_nowait()
        except queue.Empty:
            return "dropped"

        try:
            work_queue.put_nowait(payload)
            return "replaced"
        except queue.Full:
            return "dropped"

    def _partial_task_queue_for_source(
        self,
        source: str,
    ) -> queue.Queue[tuple[object, str | None, int, int, str] | None]:
        return self._partial_task_queues[DESKTOP_SOURCE if source == DESKTOP_SOURCE else MIC_SOURCE]

    def _final_task_queue_for_source(
        self,
        source: str,
    ) -> queue.Queue[tuple[object, str | None, str | None, int, str] | None]:
        return self._final_task_queues[DESKTOP_SOURCE if source == DESKTOP_SOURCE else MIC_SOURCE]

    def _call_in_ui(self, callback, delay_ms: int = 0) -> bool:
        if self._destroying:
            return False
        try:
            self.after(delay_ms, callback)
            return True
        except Exception:
            return False

    def _schedule_config_save(self, delay_ms: int = CONFIG_SAVE_DEBOUNCE_MS) -> None:
        if self._destroying:
            return
        if self._config_save_after_id is not None:
            try:
                self.after_cancel(self._config_save_after_id)
            except Exception:
                pass
        self._config_save_after_id = self.after(delay_ms, self._flush_config_save)

    def _flush_config_save(self) -> None:
        self._config_save_after_id = None
        config_manager.save_config(self._config)

    def _save_config_now(self) -> None:
        if self._config_save_after_id is not None:
            try:
                self.after_cancel(self._config_save_after_id)
            except Exception:
                pass
            self._config_save_after_id = None
        config_manager.save_config(self._config)

    def _audio_config(self) -> dict:
        audio_cfg = self._config.setdefault("audio", {})
        if not isinstance(audio_cfg, dict):
            audio_cfg = {}
            self._config["audio"] = audio_cfg
        return audio_cfg

    def _translation_config(self) -> dict:
        trans_cfg = self._config.setdefault("translation", {})
        if not isinstance(trans_cfg, dict):
            trans_cfg = {}
            self._config["translation"] = trans_cfg
        return trans_cfg

    def _mic_input_device_mode(self) -> str:
        audio_cfg = self._audio_config()
        mode = str(audio_cfg.get("input_device_mode", "")).strip().lower()
        if mode not in {"auto", "fixed"}:
            mode = "fixed" if str(audio_cfg.get("input_device") or "").strip() else "auto"
            audio_cfg["input_device_mode"] = mode
        return mode

    def _configured_mic_input_device_name(self) -> str | None:
        configured = str(self._audio_config().get("input_device") or "").strip()
        return configured or None

    def _current_default_input_device_name(self, devices: list[dict] | None = None) -> str | None:
        device_list = devices if devices is not None else list(getattr(self, "_devices", {}).keys())
        if isinstance(device_list, list) and device_list and isinstance(device_list[0], dict):
            names = [str(device.get("name", "")).strip() for device in device_list if str(device.get("name", "")).strip()]
            devices_payload = device_list
        else:
            names = [str(name).strip() for name in device_list or [] if str(name).strip()]
            devices_payload = [{"name": name} for name in names]
        if not names:
            return None
        return self._get_system_default_input(devices_payload, names)

    def _best_available_input_device_name(self, devices: list[dict] | None = None) -> str | None:
        device_list = devices if devices is not None else list(getattr(self, "_devices", {}).keys())
        if isinstance(device_list, list) and device_list and isinstance(device_list[0], dict):
            names = [str(device.get("name", "")).strip() for device in device_list if str(device.get("name", "")).strip()]
        else:
            names = [str(name).strip() for name in device_list or [] if str(name).strip()]
        return names[0] if names else None

    def _resolve_mic_input_device_name(self, *, refresh_devices: bool = False) -> str | None:
        if refresh_devices:
            devices = AudioRecorder.list_devices()
            self._devices = {d["name"]: d["index"] for d in devices}
        else:
            devices = [{"name": name, "index": index} for name, index in getattr(self, "_devices", {}).items()]
        mode = self._mic_input_device_mode()
        configured = self._configured_mic_input_device_name()
        available_names = {str(device.get("name", "")).strip() for device in devices}
        default_name = self._current_default_input_device_name(devices)
        if mode == "fixed" and configured and configured in available_names:
            return configured
        if default_name and default_name in available_names:
            return default_name
        if configured and configured in available_names:
            return configured
        return self._best_available_input_device_name(devices)

    def _current_device_display_name(self, device_name: str | None = None) -> str:
        if device_name is not None:
            resolved_name = str(device_name).strip()
        elif hasattr(self, "_device_var"):
            resolved_name = str(self._device_var.get()).strip()
        else:
            resolved_name = ""
        if self._mic_input_device_mode() == "auto":
            if resolved_name:
                return self._copy("mic_device_auto_current", name=resolved_name)
            if not getattr(self, "_devices", {}):
                return self._copy("mic_device_none")
            return self._copy("mic_device_auto_option")
        return resolved_name or self._copy("mic_device_none")

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, corner_radius=0, fg_color=BG_TOP)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text=self._t("creator_banner"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=TEXT_PRI,
            justify="left",
            wraplength=900,
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 8))

        controls = ctk.CTkFrame(self, fg_color=BG_SECONDARY, corner_radius=0)
        controls.grid(row=1, column=0, sticky="ew")
        controls.grid_columnconfigure(0, weight=1)

        control_row = ctk.CTkFrame(controls, fg_color="transparent")
        control_row.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 6))
        control_row.grid_columnconfigure(2, weight=1)

        self._status_label = ctk.CTkLabel(
            control_row,
            text=self._status_text,
            text_color=self._status_color,
            font=ctk.CTkFont(size=12),
        )
        self._status_label.grid(row=0, column=0, sticky="w", padx=(0, 18))

        mic_group = ctk.CTkFrame(control_row, fg_color="transparent")
        mic_group.grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(
            mic_group,
            text=self._t("microphone"),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11),
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._device_var = ctk.StringVar()
        device_picker = ctk.CTkFrame(
            mic_group,
            fg_color=GLASS_BG,
            corner_radius=12,
            border_width=1,
            border_color=GLASS_BORDER,
        )
        device_picker.grid(row=0, column=1, sticky="w")
        device_picker.grid_columnconfigure(0, weight=1)

        self._device_button = ctk.CTkButton(
            device_picker,
            text="Loading...",
            width=206,
            height=34,
            anchor="w",
            fg_color="transparent",
            hover_color="#ecf3ff",
            corner_radius=10,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=11),
            command=self._open_device_picker,
        )
        self._device_button.grid(row=0, column=0, sticky="ew", padx=(2, 0), pady=2)

        ctk.CTkButton(
            device_picker,
            text="v",
            width=30,
            height=34,
            fg_color="#e5edf8",
            hover_color="#dbe7f5",
            corner_radius=10,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._open_device_picker,
        ).grid(row=0, column=1, padx=2, pady=2)

        self._update_badge_btn = ctk.CTkButton(
            control_row,
            text=self._copy("update_badge"),
            width=80,
            height=28,
            fg_color="#ff9f0a",
            hover_color="#e08800",
            corner_radius=14,
            text_color="#ffffff",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._open_update_window,
        )
        if getattr(self, "_pending_update", None):
            self._update_badge_btn.grid(row=0, column=2, sticky="w", padx=(8, 0))

        action_buttons = ctk.CTkFrame(control_row, fg_color="transparent")
        action_buttons.grid(row=0, column=3, sticky="e")

        self._start_btn = ctk.CTkButton(
            action_buttons,
            text=self._t("start_listening"),
            width=120,
            height=34,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            corner_radius=9,
            text_color="#ffffff",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._toggle_listening,
        )
        self._start_btn.pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            action_buttons,
            text=self._copy("settings_short"),
            width=104,
            height=34,
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=9,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=11),
            command=self._open_settings,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            action_buttons,
            text=self._copy("guide_short"),
            width=88,
            height=34,
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=9,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=11),
            command=self._open_osc_guide,
        ).pack(side="right", padx=(6, 0))

        self._all_target_lang_options = list(
            get_target_language_options(ui_language=self._ui_lang)
        )
        self._target_lang_codes = {
            label: code for label, code in self._all_target_lang_options
        }
        self._target_lang_reverse = {
            code: label for label, code in self._all_target_lang_options
        }
        target_labels = [
            label
            for label, _ in get_target_language_options(ui_language=self._ui_lang)
        ]
        initial_tgt = self._config.get("translation", {}).get("target_language", "ja")
        self._tgt_var = ctk.StringVar(
            value=self._target_lang_reverse.get(initial_tgt, target_labels[0])
        )

        self._build_translate_panel()

        bottom = ctk.CTkFrame(self, fg_color=BG_PRIMARY)
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.grid_columnconfigure(0, weight=1)

        self._bottom_bar = ctk.CTkLabel(
            bottom,
            text=self._bottom_text,
            font=ctk.CTkFont(size=10),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=920,
        )
        self._bottom_bar.grid(row=0, column=0, sticky="w", padx=12, pady=(4, 2))

        self._bottom_progress = ctk.CTkProgressBar(
            bottom,
            height=10,
            fg_color=BG_SECONDARY,
            progress_color=ACCENT,
        )
        self._bottom_progress.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self._bottom_progress.set(self._bottom_progress_value)

        self._set_status(self._status_text, self._status_color)
        self._set_bottom(self._bottom_text)
        self._refresh_start_button()
        if self._bottom_progress_visible:
            self._show_bottom_progress(
                self._bottom_progress_value,
                indeterminate=self._bottom_progress_indeterminate,
            )
        else:
            self._hide_bottom_progress()

    def _build_translate_panel(self):
        outer = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=0)
        outer.grid(row=2, column=0, sticky="nsew")
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(outer, fg_color=BG_SECONDARY, corner_radius=0, height=38)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(3, weight=1)
        hdr.grid_propagate(False)

        self._all_manual_lang_options = list(
            get_manual_source_language_options(ui_language=self._ui_lang)
        )
        src_labels = [
            label
            for label, _ in get_manual_source_language_options(
                {self._current_tgt_lang},
                ui_language=self._ui_lang,
            )
        ]
        self._src_lang_codes = {
            label: code for label, code in self._all_manual_lang_options
        }
        self._src_lang_var = ctk.StringVar(value=src_labels[0])
        self._src_lang_menu = ctk.CTkOptionMenu(
            hdr,
            values=src_labels,
            variable=self._src_lang_var,
            width=126,
            fg_color=BG_SECONDARY,
            button_color=BG_SECONDARY,
            button_hover_color=GLASS_HOVER,
            dropdown_fg_color=BG_PANEL,
            dropdown_hover_color="#e8f2ff",
            dropdown_text_color=TEXT_PRI,
            corner_radius=6,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=12),
        )
        self._src_lang_menu.grid(row=0, column=0, sticky="w", padx=(8, 4), pady=6)

        ctk.CTkButton(
            hdr,
            text="⇄",
            width=34,
            height=22,
            fg_color="transparent",
            hover_color=GLASS_HOVER,
            corner_radius=6,
            text_color=TEXT_SEC,
            command=self._swap_langs,
        ).grid(row=0, column=1, sticky="w", padx=(0, 6), pady=6)

        self._tgt_menu = ctk.CTkOptionMenu(
            hdr,
            values=[
                label
                for label, _ in get_target_language_options(ui_language=self._ui_lang)
            ],
            variable=self._tgt_var,
            fg_color=BG_SECONDARY,
            button_color=BG_SECONDARY,
            button_hover_color=GLASS_HOVER,
            dropdown_fg_color=BG_PANEL,
            dropdown_hover_color="#e8f2ff",
            dropdown_text_color=TEXT_PRI,
            corner_radius=6,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=12, weight="bold"),
            width=148,
        )
        self._tgt_menu.grid(row=0, column=2, sticky="w", padx=(0, 6), pady=6)

        ui_lang_labels = [label for label, _ in UI_LANGUAGE_OPTIONS]
        self._ui_lang_codes = {label: code for label, code in UI_LANGUAGE_OPTIONS}
        self._ui_lang_reverse = {code: label for label, code in UI_LANGUAGE_OPTIONS}
        self._ui_lang_var = ctk.StringVar(
            value=self._ui_lang_reverse.get(self._ui_lang, ui_lang_labels[0])
        )

        right_controls = ctk.CTkFrame(hdr, fg_color="transparent")
        right_controls.grid(row=0, column=4, sticky="e", padx=(8, 10), pady=5)

        self._desktop_audio_button = ctk.CTkButton(
            right_controls,
            text="",
            width=92,
            height=26,
            fg_color="#eef5ff",
            hover_color="#dfeeff",
            border_width=1,
            border_color="#bfdbff",
            corner_radius=13,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._toggle_listen,
        )
        self._desktop_audio_button.pack(side="left", padx=(0, 8))

        self._listen_overlay_button = ctk.CTkButton(
            right_controls,
            text="",
            width=68,
            height=26,
            fg_color="#eef5ff",
            hover_color="#dfeeff",
            border_width=1,
            border_color="#bfdbff",
            corner_radius=13,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._toggle_listen_overlay,
        )
        self._listen_overlay_button.pack(side="left", padx=(0, 8))

        lang_badge = ctk.CTkFrame(
            right_controls,
            fg_color="#eef5ff",
            corner_radius=12,
            border_width=1,
            border_color="#bfdbff",
        )
        lang_badge.pack(side="left")

        ctk.CTkLabel(
            lang_badge,
            text=self._copy("ui_badge"),
            text_color=ACCENT,
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left", padx=(8, 5), pady=2)

        self._ui_lang_menu = ctk.CTkOptionMenu(
            lang_badge,
            values=ui_lang_labels,
            variable=self._ui_lang_var,
            command=self._on_ui_lang_selected,
            width=104,
            height=24,
            fg_color="#eef5ff",
            button_color="#cfe5ff",
            button_hover_color=GLASS_HOVER,
            dropdown_fg_color=BG_PANEL,
            dropdown_hover_color="#e8f2ff",
            dropdown_text_color=TEXT_PRI,
            corner_radius=10,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=11),
        )
        self._ui_lang_menu.pack(side="left", padx=(0, 4), pady=2)
        self._refresh_desktop_capture_button()
        self._refresh_listen_overlay_button()
        self._tgt_var.trace_add("write", self._on_tgt_lang_change)
        self._on_tgt_lang_change()
        self._src_lang_var.trace_add("write", self._on_src_lang_change)
        self._on_src_lang_change()

        text_row = ctk.CTkFrame(outer, fg_color=BG_PANEL, corner_radius=0)
        text_row.grid(row=1, column=0, sticky="nsew")
        text_row.grid_columnconfigure(0, weight=1)
        text_row.grid_columnconfigure(2, weight=1)
        text_row.grid_rowconfigure(0, weight=1)
        text_row.configure(height=226)

        left = ctk.CTkFrame(text_row, fg_color=BG_PANEL, corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)

        self._src_input = ctk.CTkTextbox(
            left,
            height=180,
            font=ctk.CTkFont(size=16),
            wrap="word",
            state="disabled",
            fg_color=BG_PANEL,
            corner_radius=0,
            text_color=TEXT_SEC,
            border_width=0,
        )
        self._src_input.grid(row=0, column=0, sticky="nsew", padx=8, pady=(6, 0))
        self._set_source_text("")

        ctk.CTkFrame(text_row, width=1, fg_color=DIVIDER).grid(
            row=0,
            column=1,
            sticky="ns",
            pady=6,
        )

        right = ctk.CTkFrame(text_row, fg_color=BG_PANEL, corner_radius=0)
        right.grid(row=0, column=2, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        self._tgt_output = ctk.CTkTextbox(
            right,
            height=180,
            font=ctk.CTkFont(size=16),
            wrap="word",
            state="disabled",
            fg_color=BG_PANEL,
            corner_radius=0,
            text_color=TEXT_PRI,
            border_width=0,
        )
        self._tgt_output.grid(row=0, column=0, sticky="nsew", padx=8, pady=(6, 0))

        action_bar = ctk.CTkFrame(text_row, fg_color=BG_SECONDARY, corner_radius=0, height=34)
        action_bar.grid(row=1, column=0, columnspan=3, sticky="ew")
        action_bar.grid_propagate(False)
        action_bar.grid_columnconfigure(0, weight=1)
        action_bar.grid_columnconfigure(1, weight=0)

        left_actions = ctk.CTkFrame(action_bar, fg_color="transparent")
        left_actions.grid(row=0, column=0, sticky="w", padx=10)

        self._char_label = ctk.CTkLabel(
            left_actions,
            text=self._t("char_count", count=0),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=10),
        )
        self._char_label.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            left_actions,
            text=self._t("manual_input"),
            width=98,
            height=22,
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=6,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=11),
            command=self._open_text_input_popup,
        ).pack(side="left", padx=(0, 8))

        self._translate_btn = ctk.CTkButton(
            left_actions,
            text=self._t("translate"),
            width=82,
            height=22,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            corner_radius=8,
            text_color="#ffffff",
            font=ctk.CTkFont(size=11),
            command=self._translate_manual,
        )
        self._translate_btn.pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            left_actions,
            text=self._t("clear"),
            width=62,
            height=22,
            fg_color="transparent",
            hover_color=GLASS_HOVER,
            corner_radius=6,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11),
            command=self._clear_input,
        ).pack(side="left")

        right_actions = ctk.CTkFrame(action_bar, fg_color="transparent")
        right_actions.grid(row=0, column=1, sticky="e", padx=10)

        ctk.CTkButton(
            right_actions,
            text=self._t("copy"),
            width=78,
            height=22,
            fg_color="transparent",
            hover_color=GLASS_HOVER,
            corner_radius=6,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11),
            command=self._copy_result,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            right_actions,
            text=self._t("send_to_vrc"),
            width=106,
            height=22,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            corner_radius=8,
            text_color="#ffffff",
            font=ctk.CTkFont(size=11),
            command=self._send_to_vrc,
        ).pack(side="left")

        social_bar = ctk.CTkFrame(outer, fg_color=BG_SECONDARY, corner_radius=0, height=56)
        social_bar.grid(row=2, column=0, sticky="ew")
        social_bar.grid_propagate(False)

        social_center = ctk.CTkFrame(social_bar, fg_color="transparent")
        social_center.pack(expand=True, pady=8)

        github_icon = self._load_social_icon(ICON_GITHUB_FILE)
        qq_icon = self._load_social_icon(ICON_QQ_FILE)
        line_icon = self._load_social_icon(ICON_LINE_FILE)
        sponsor_icon = self._load_social_icon(ICON_SPONSOR_FILE)

        self._add_social_button(
            parent=social_center,
            icon=github_icon,
            fallback_text="Git",
            fg="#2b3137",
            hover="#1f2328",
            command=lambda: self._open_external_url(GITHUB_REPO_URL),
        )
        self._add_social_button(
            parent=social_center,
            icon=qq_icon,
            fallback_text="QQ",
            fg="#12B7F5",
            hover="#0F9ED8",
            command=lambda: self._open_external_url(QQ_GROUP_URL),
        )
        self._add_social_button(
            parent=social_center,
            icon=line_icon,
            fallback_text="LINE",
            fg="#06C755",
            hover="#04A946",
            command=lambda: self._open_external_url(LINE_GROUP_URL),
        )
        self._add_social_button(
            parent=social_center,
            icon=sponsor_icon,
            fallback_text="赞助",
            fg="#D4A638",
            hover="#BC912D",
            command=self._open_sponsor_window,
        )

    def _refresh_start_button(self):
        if self._running:
            self._start_btn.configure(
                text=self._t("stop_listening"),
                state="normal",
                fg_color=DANGER,
                hover_color=DANGER_HOVER,
            )
            return

        self._start_btn.configure(
            text=self._t("start_listening"),
            state="disabled" if self._model_prepare_running else "normal",
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
        )

    def _set_source_text(self, text: str, text_color: str | None = None):
        safe = (text or "").strip()
        if len(safe) > 500:
            safe = safe[:500]
        self._src_text = safe

        shown = safe or self._src_placeholder
        color = text_color or (TEXT_PRI if safe else TEXT_SEC)
        char_count = len(safe)

        if (
            shown == self._src_rendered_text
            and color == self._src_rendered_color
            and char_count == self._src_rendered_count
        ):
            return

        self._src_input.configure(state="normal")
        self._src_input.delete("1.0", "end")
        self._src_input.insert("1.0", shown)
        self._src_input.configure(text_color=color, state="disabled")
        self._src_rendered_text = shown
        self._src_rendered_color = color
        self._src_rendered_count = char_count
        char_label = getattr(self, "_char_label", None)
        if char_label is not None:
            try:
                if char_label.winfo_exists():
                    char_label.configure(text=self._t("char_count", count=char_count))
            except Exception:
                pass

    def _open_text_input_popup(self, _event=None):
        popup = ctk.CTkToplevel(self)
        popup.title(self._t("manual_input"))
        apply_window_icon(popup)
        popup.geometry("480x208")
        popup._popup_size = (480, 208)
        popup.resizable(False, False)
        popup.attributes("-topmost", True)
        popup.transient(self)
        popup.grab_set()
        popup.configure(fg_color=BG_PANEL)
        popup.grid_columnconfigure(0, weight=1)
        popup.grid_rowconfigure(0, weight=1)

        content = ctk.CTkFrame(
            popup,
            fg_color=BG_PANEL,
            corner_radius=0,
            border_width=0,
        )
        content.grid(row=0, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)

        box = ctk.CTkTextbox(
            content,
            font=ctk.CTkFont(size=13),
            fg_color=BG_PANEL,
            text_color=TEXT_PRI,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=14,
        )
        box.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 8))
        if self._src_text:
            box.insert("1.0", self._src_text)
        box.focus_set()

        btn_row = ctk.CTkFrame(content, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="e", padx=10, pady=(0, 10))

        def do_send():
            text = box.get("1.0", "end").strip()
            self._set_source_text(text)
            popup.destroy()
            if text:
                self._translate_manual()

        ctk.CTkButton(
            btn_row,
            text=self._t("apply"),
            width=100,
            height=36,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            corner_radius=12,
            text_color="#ffffff",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=do_send,
        ).pack(side="right", padx=4)

        ctk.CTkButton(
            btn_row,
            text=self._t("cancel"),
            width=100,
            height=36,
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=12,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=12),
            command=popup.destroy,
        ).pack(side="right", padx=4)
        present_popup(popup, parent=self)

    def _on_tgt_lang_change(self, *_):
        tgt_code = self._target_lang_codes.get(self._tgt_var.get(), "ja")
        self._current_tgt_lang = tgt_code
        self._sync_target_language_to_config(tgt_code)
        self._sync_avatar_target_language()

        values = [
            label
            for label, _ in get_manual_source_language_options(
                {tgt_code},
                ui_language=self._ui_lang,
            )
        ]
        self._src_lang_menu.configure(values=values)
        if self._src_lang_var.get() not in values:
            self._src_lang_var.set(values[0])

    def _on_src_lang_change(self, *_):
        label = self._src_lang_var.get()
        code = self._src_lang_codes.get(label, "auto")
        self._current_src_lang = None if code == "auto" else code
        if self._current_src_lang in ASR_HINT_LANGUAGE_CODES:
            self._current_asr_lang = self._current_src_lang
        else:
            self._current_asr_lang = None

        exclude_codes = {self._current_src_lang} if self._current_src_lang else set()
        values = [
            label
            for label, _ in get_target_language_options(
                exclude_codes,
                ui_language=self._ui_lang,
            )
        ]
        self._tgt_menu.configure(values=values)
        if self._tgt_var.get() not in values:
            self._tgt_var.set(values[0])

    def _refresh_desktop_capture_button(self) -> None:
        button = getattr(self, "_desktop_audio_button", None)
        if button is None:
            return
        enabled = self._desktop_capture_enabled
        running = self._listen_running
        available = self._listen_available
        transitioning = self._listen_transitioning
        state = "disabled" if transitioning else "normal"
        if transitioning:
            text_color = TEXT_SEC
            fg_color = BG_SECONDARY
            hover_color = BG_SECONDARY
            border_color = GLASS_BORDER
        elif running:
            text_color = "#166534"
            fg_color = "#dcfce7"
            hover_color = "#bbf7d0"
            border_color = "#86efac"
        elif enabled and not available:
            text_color = "#9a3412"
            fg_color = "#ffedd5"
            hover_color = "#fed7aa"
            border_color = "#fdba74"
        elif enabled:
            text_color = "#ffffff"
            fg_color = ACCENT
            hover_color = ACCENT_HOVER
            border_color = ACCENT_HOVER
        else:
            text_color = TEXT_PRI
            fg_color = "#eef5ff"
            hover_color = "#dfeeff"
            border_color = "#bfdbff"
        button.configure(
            text=self._copy("desktop_audio_on" if enabled else "desktop_audio_off"),
            text_color=text_color,
            text_color_disabled=text_color,
            fg_color=fg_color,
            hover_color=hover_color,
            border_color=border_color,
            state=state,
        )

    def _refresh_listen_overlay_button(self) -> None:
        button = getattr(self, "_listen_overlay_button", None)
        if button is None:
            return
        enabled = self._listen_overlay_enabled
        button.configure(
            text=self._copy("listen_overlay_on" if enabled else "listen_overlay_off"),
            text_color="#ffffff" if enabled else TEXT_PRI,
            fg_color="#0ea5e9" if enabled else "#eef5ff",
            hover_color="#0284c7" if enabled else "#dfeeff",
            border_color="#0284c7" if enabled else "#bfdbff",
        )

    def _swap_langs(self):
        src_code = self._src_lang_codes.get(self._src_lang_var.get(), "auto")
        tgt_code = self._target_lang_codes.get(self._tgt_var.get())

        if src_code and src_code != "auto" and tgt_code:
            src_reverse = {code: label for label, code in self._all_manual_lang_options}
            new_src_label = src_reverse.get(tgt_code)
            new_tgt_label = self._target_lang_reverse.get(src_code)
            if new_src_label and new_tgt_label:
                self._src_lang_var.set(new_src_label)
                self._tgt_var.set(new_tgt_label)

        src_text = self._src_text
        tgt_text = self._tgt_output.get("1.0", "end").strip()
        self._set_source_text(tgt_text)
        self._show_tgt(src_text)

    def _clear_input(self):
        self._set_source_text("")
        self._last_tgt_text = ""
        self._tgt_output.configure(state="normal")
        self._tgt_output.delete("1.0", "end")
        self._tgt_output.configure(state="disabled")
        self._tgt_rendered_text = ""

    def _copy_result(self):
        text = self._tgt_output.get("1.0", "end").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)

    def _on_ui_lang_selected(self, selected_label: str):
        new_lang = self._ui_lang_codes.get(selected_label, self._ui_lang)
        if new_lang == self._ui_lang:
            return

        device_name = self._current_device_name()

        ui_cfg = self._config.setdefault("ui", {})
        ui_cfg["language"] = new_lang
        ui_cfg["language_source"] = "manual"
        self._schedule_config_save()

        self._ui_lang = new_lang
        self.title(self._t("window_title"))
        model_spec = self._asr_model_spec()
        if self._running:
            self._status_text = self._t("status_listening")
            self._status_color = SUCCESS
        elif self._status_color == SUCCESS:
            self._status_text = self._t("status_ready")
        if self._model_prepare_running:
            self._bottom_text = self._t("model_downloading")
        elif model_exists(model_spec):
            self._bottom_text = self._t("model_ready")
        else:
            self._bottom_text = self._t("model_unloaded")

        self._rebuild_ui(device_name=device_name)

    def _asr_model_spec(self):
        return get_asr_runtime_spec(self._config)

    def _maybe_prepare_runtime_model(self):
        if self._model_prepare_running:
            return

        model_spec = self._asr_model_spec()
        if model_exists(model_spec):
            if self._bottom_text == self._t("model_unloaded"):
                self._set_bottom(self._t("model_ready"))
            return

        self._model_prepare_running = True
        self._refresh_start_button()
        self._set_bottom(self._t("model_downloading"))
        self._show_bottom_progress(0.0, indeterminate=True)
        threading.Thread(
            target=self._prepare_runtime_model,
            args=(model_spec,),
            daemon=True,
        ).start()

    def _prepare_runtime_model(self, model_spec):
        try:
            download_model(
                model_spec,
                progress_callback=lambda event: self._call_in_ui(
                    lambda e=event: self._handle_model_progress(e)
                ),
            )
        except Exception as exc:
            self._call_in_ui(lambda m=str(exc): self._on_model_prepare_failed(m))
            return
        self._call_in_ui(self._on_model_prepare_ready)

    def _on_model_prepare_ready(self):
        self._model_prepare_running = False
        self._refresh_start_button()
        self._hide_bottom_progress()
        self._set_bottom(self._t("model_ready"))

    def _on_model_prepare_failed(self, message: str):
        self._model_prepare_running = False
        self._refresh_start_button()
        self._hide_bottom_progress()
        self._set_bottom(message)

    def _handle_model_progress(self, event):
        if isinstance(event, dict):
            stage = str(event.get("stage", "")).strip()
            progress_value = event.get("progress")
            progress = float(progress_value) if isinstance(progress_value, (int, float)) else None
            indeterminate = bool(event.get("indeterminate", False))

            if stage == "download_complete":
                self._set_bottom(self._t("model_ready"))
                self._show_bottom_progress(1.0, indeterminate=False)
                return

            if stage in {"download_prepare", "download"}:
                text = self._t("model_downloading")
                if progress is not None:
                    text = f"{text} {progress * 100:.0f}%"
                self._set_bottom(text)
                self._show_bottom_progress(progress, indeterminate=indeterminate)
                return

            if stage == "loading":
                self._set_bottom(self._t("model_loading"))
                self._show_bottom_progress(progress, indeterminate=True)
                return

            if stage == "ready":
                self._set_bottom(self._t("model_ready"))
                self._hide_bottom_progress()
                return

            message = str(event.get("message", "")).strip()
            if message:
                self._set_bottom(message)
                self._show_bottom_progress(progress, indeterminate=indeterminate)
            return

        message = str(event).strip()
        if not message:
            return
        self._set_bottom(message)
        self._show_bottom_progress(None, indeterminate=True)

    def _show_bottom_progress(self, progress: float | None, *, indeterminate: bool):
        self._bottom_progress_visible = True
        self._bottom_progress_indeterminate = indeterminate
        if progress is not None:
            self._bottom_progress_value = max(0.0, min(float(progress), 1.0))

        if not hasattr(self, "_bottom_progress"):
            return

        self._bottom_progress.grid()
        if self._bottom_progress_running:
            self._bottom_progress.stop()
            self._bottom_progress_running = False

        if indeterminate:
            self._bottom_progress.configure(mode="indeterminate")
            self._bottom_progress.start()
            self._bottom_progress_running = True
            return

        self._bottom_progress.configure(mode="determinate")
        self._bottom_progress.set(self._bottom_progress_value if progress is not None else 0.0)

    def _hide_bottom_progress(self):
        self._bottom_progress_visible = False
        self._bottom_progress_indeterminate = False
        self._bottom_progress_value = 0.0

        if not hasattr(self, "_bottom_progress"):
            return

        if self._bottom_progress_running:
            self._bottom_progress.stop()
            self._bottom_progress_running = False
        self._bottom_progress.configure(mode="determinate")
        self._bottom_progress.set(0.0)
        self._bottom_progress.grid_remove()

    @staticmethod
    def _open_external_url(url: str):
        try:
            webbrowser.open_new_tab(url)
        except Exception:
            pass

    @staticmethod
    def _runtime_base_dirs() -> list[Path]:
        if not getattr(sys, "frozen", False):
            return [Path(__file__).resolve().parents[2]]

        dirs = [Path(sys.executable).resolve().parent]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            meipass_path = Path(meipass)
            if meipass_path not in dirs:
                dirs.append(meipass_path)
        return dirs

    @staticmethod
    def _assets_dirs() -> list[Path]:
        dirs = []
        for base in MainWindow._runtime_base_dirs():
            assets_dir = base / "assets"
            if assets_dir.exists():
                dirs.append(assets_dir)
        if dirs:
            return dirs
        return [MainWindow._runtime_base_dirs()[0] / "assets"]

    @staticmethod
    def _icons_dirs() -> list[Path]:
        dirs = []
        for assets_dir in MainWindow._assets_dirs():
            icons_dir = assets_dir / "icons"
            if icons_dir.exists():
                dirs.append(icons_dir)
        if dirs:
            return dirs
        return [MainWindow._assets_dirs()[0] / "icons"]

    @staticmethod
    def _find_asset_file(filename: str) -> Path | None:
        for assets_dir in MainWindow._assets_dirs():
            candidate = assets_dir / filename
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _find_icon_file(filename: str) -> Path | None:
        for icons_dir in MainWindow._icons_dirs():
            candidate = icons_dir / filename
            if candidate.exists():
                return candidate
        return MainWindow._find_asset_file(filename)

    def _set_window_icon(self):
        apply_window_icon(self)
        self._window_icon = getattr(self, "_window_icon_ref", None)

    def _load_social_icon(self, filename: str) -> ctk.CTkImage | None:
        icon_path = None
        for icons_dir in self._icons_dirs():
            candidate = icons_dir / filename
            if candidate.exists():
                icon_path = candidate
                break
        if icon_path is None:
            return None
        try:
            from PIL import Image
            img = Image.open(icon_path).convert("RGBA")
        except Exception:
            return None

        icon = ctk.CTkImage(light_image=img, dark_image=img, size=(28, 28))
        self._social_icons[filename] = icon
        return icon

    @staticmethod
    def _add_social_button(parent, icon, fallback_text: str, fg: str, hover: str, command):
        ctk.CTkButton(
            parent,
            image=icon,
            text="" if icon else fallback_text,
            width=68,
            height=40,
            corner_radius=10,
            fg_color=fg,
            hover_color=hover,
            text_color="#ffffff",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=command,
        ).pack(side="left", padx=8, pady=8)

    @staticmethod
    def _find_sponsor_image() -> Path | None:
        for assets_dir in MainWindow._assets_dirs():
            for name in SPONSOR_IMAGE_CANDIDATES:
                p = assets_dir / name
                if p.exists():
                    return p

            if not assets_dir.exists():
                continue
            for p in sorted(assets_dir.iterdir()):
                if not p.is_file():
                    continue
                if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                    return p
        return None

    def _open_sponsor_window(self):
        image_path = self._find_sponsor_image()
        if image_path is None:
            messagebox.showinfo(
                "赞助入口",
                "未找到赞助收款图。\n请把图片放到 assets/sponsor_qr.png（或 sponsor_qr.jpg）后重试。",
            )
            return

        if self._sponsor_win and self._sponsor_win.winfo_exists():
            self._sponsor_win.deiconify()
            self._sponsor_win.lift()
            return

        try:
            from PIL import Image
        except Exception:
            messagebox.showerror("赞助入口", "缺少 Pillow 依赖，无法显示赞助图片。")
            return

        img = Image.open(image_path)
        img.thumbnail((520, 520))
        ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)

        self._sponsor_win = ctk.CTkToplevel(self)
        self._sponsor_win.title("赞助入口")
        apply_window_icon(self._sponsor_win)
        self._sponsor_win._popup_size = (img.size[0] + 20, img.size[1] + 52)
        self._sponsor_win.resizable(False, False)
        self._sponsor_win.attributes("-topmost", True)
        self._sponsor_win.transient(self)
        self._sponsor_win.configure(fg_color=BG_PRIMARY)

        outer = ctk.CTkFrame(self._sponsor_win, fg_color=BG_PRIMARY)
        outer.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(
            outer,
            text="感谢支持：酒寄 みお",
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(pady=(4, 6))

        img_label = ctk.CTkLabel(outer, text="", image=ctk_img)
        img_label.image = ctk_img
        img_label.pack(padx=2, pady=(0, 2))

        present_popup(self._sponsor_win, parent=self)

    @staticmethod
    def _is_vrchat_running() -> bool:
        return is_process_running(("VRChat.exe",))

    def _create_sender(self) -> VRCOSCSender:
        osc_cfg = self._config.get("osc", {})
        try:
            min_send_interval_s = float(osc_cfg.get("min_send_interval_s", 0.8))
        except (TypeError, ValueError):
            min_send_interval_s = 0.8
        return VRCOSCSender(
            host=osc_cfg.get("send_host", "127.0.0.1"),
            port=osc_cfg.get("send_port", 9000),
            min_send_interval_s=min_send_interval_s,
        )

    def _ensure_sender(self) -> VRCOSCSender:
        if self._sender is None:
            self._sender = self._create_sender()
        return self._sender

    def _avatar_sync_config(self) -> dict:
        osc_cfg = self._config.setdefault("osc", {})
        avatar_cfg = osc_cfg.setdefault("avatar_sync", {})
        avatar_cfg.setdefault("enabled", False)
        params = avatar_cfg.setdefault("params", {})
        params.setdefault("translating", "MioTranslating")
        params.setdefault("speaking", "MioSpeaking")
        params.setdefault("error", "MioError")
        params.setdefault("target_language", "MioTargetLanguage")
        return avatar_cfg

    def _avatar_sync_enabled(self) -> bool:
        return bool(self._avatar_sync_config().get("enabled", False))

    def _avatar_param_name(self, key: str) -> str:
        avatar_cfg = self._avatar_sync_config()
        params = avatar_cfg.get("params", {})
        if not isinstance(params, dict):
            return ""
        return str(params.get(key, "")).strip()

    def _sync_avatar_bool(self, key: str, value: bool, *, force: bool = False) -> bool:
        if not self._avatar_sync_enabled():
            return False
        param_name = self._avatar_param_name(key)
        if not param_name:
            return False
        try:
            return self._ensure_sender().send_avatar_bool(param_name, value, force=force)
        except Exception:
            return False

    def _sync_avatar_int(self, key: str, value: int, *, force: bool = False) -> bool:
        if not self._avatar_sync_enabled():
            return False
        param_name = self._avatar_param_name(key)
        if not param_name:
            return False
        try:
            return self._ensure_sender().send_avatar_int(param_name, value, force=force)
        except Exception:
            return False

    def _current_translating_state(self) -> bool:
        with self._translation_state_lock:
            return self._active_translation_jobs > 0

    def _sync_avatar_target_language(self, *, force: bool = False) -> None:
        self._sync_avatar_int(
            "target_language",
            target_language_osc_value(self._current_tgt_lang),
            force=force,
        )

    def _sync_avatar_speaking_state(self, *, force: bool = False) -> None:
        self._sync_avatar_bool(
            "speaking",
            self._mic_in_speech or self._desktop_in_speech,
            force=force,
        )

    def _sync_avatar_translating_state(self, *, force: bool = False) -> None:
        self._sync_avatar_bool(
            "translating",
            self._current_translating_state(),
            force=force,
        )

    def _sync_all_avatar_params(self, *, force: bool = False) -> None:
        self._sync_avatar_target_language(force=force)
        self._sync_avatar_speaking_state(force=force)
        self._sync_avatar_translating_state(force=force)
        self._sync_avatar_bool("error", False, force=force)

    def _reset_avatar_params(self) -> None:
        if not self._avatar_sync_enabled():
            return
        try:
            sender = self._ensure_sender()
        except Exception:
            return
        sender.clear_avatar_state(
            [
                (self._avatar_param_name("translating"), False),
                (self._avatar_param_name("speaking"), False),
                (self._avatar_param_name("error"), False),
                (self._avatar_param_name("target_language"), 0),
            ]
        )

    def _pulse_avatar_error(self) -> None:
        if self._avatar_error_after_id is not None:
            try:
                self.after_cancel(self._avatar_error_after_id)
            except Exception:
                pass
            self._avatar_error_after_id = None
        self._sync_avatar_bool("error", True, force=True)
        self._avatar_error_after_id = self.after(1400, self._clear_avatar_error)

    def _clear_avatar_error(self) -> None:
        self._avatar_error_after_id = None
        self._sync_avatar_bool("error", False, force=True)

    def _refresh_runtime_status(self) -> None:
        self._refresh_desktop_capture_button()
        if self._current_translating_state():
            self._set_status(self._t("translating"), ACCENT)
            return
        if self._running:
            if self._mic_in_speech or self._desktop_in_speech:
                self._set_status(self._t("status_speaking"), ACCENT)
            else:
                self._set_status(self._t("status_listening"), SUCCESS)
            return
        if self._status_text == self._t("status_stopped") and self._status_color == DANGER:
            return
        if self._model_prepare_running:
            return
        self._set_status(self._t("status_ready"), SUCCESS)

    def _set_translating_state(self, active: bool) -> None:
        with self._translation_state_lock:
            if active:
                self._active_translation_jobs += 1
            else:
                self._active_translation_jobs = max(0, self._active_translation_jobs - 1)
        self._sync_avatar_translating_state()
        self._call_in_ui(self._refresh_runtime_status)

    def _on_source_vad_state(self, source: str, in_speech: bool) -> None:
        if source == DESKTOP_SOURCE:
            self._desktop_in_speech = in_speech
        else:
            self._mic_in_speech = in_speech
            self._last_mic_activity_at = time.monotonic()
        self._sync_avatar_speaking_state()
        self._call_in_ui(self._refresh_runtime_status)

    @staticmethod
    def _normalize_audio_device_name(name: str) -> str:
        return " ".join(str(name or "").strip().lower().split())

    def _vrc_listen_config(self) -> dict:
        listen_cfg = self._config.setdefault("vrc_listen", {})
        audio_cfg = self._config.setdefault("audio", {})
        legacy_cfg = audio_cfg.get("desktop_capture", {})
        if not isinstance(legacy_cfg, dict):
            legacy_cfg = {}
        if "enabled" not in listen_cfg:
            listen_cfg["enabled"] = bool(legacy_cfg.get("enabled", False))
        if "loopback_device" not in listen_cfg:
            legacy_device = str(legacy_cfg.get("output_device", "")).strip()
            listen_cfg["loopback_device"] = legacy_device or None
        if not str(listen_cfg.get("source_language", "")).strip():
            listen_cfg["source_language"] = "auto"
        if not str(listen_cfg.get("target_language", "")).strip():
            listen_cfg["target_language"] = "zh"
        try:
            segment_duration_s = float(
                listen_cfg.get("segment_duration_s", DEFAULT_LISTEN_SEGMENT_DURATION_S)
            )
            if segment_duration_s <= 0:
                raise ValueError
        except (TypeError, ValueError):
            listen_cfg["segment_duration_s"] = DEFAULT_LISTEN_SEGMENT_DURATION_S
        try:
            tail_silence_s = float(
                listen_cfg.get("tail_silence_s", self._config.get("audio", {}).get(
                    "vad_silence_threshold",
                    DEFAULT_LISTEN_TAIL_SILENCE_S,
                ))
            )
            if tail_silence_s <= 0:
                raise ValueError
        except (TypeError, ValueError):
            listen_cfg["tail_silence_s"] = DEFAULT_LISTEN_TAIL_SILENCE_S
        if "self_suppress" not in listen_cfg:
            listen_cfg["self_suppress"] = False
        try:
            suppress_seconds = float(listen_cfg.get("self_suppress_seconds", DEFAULT_LISTEN_SELF_SUPPRESS_S))
            if suppress_seconds <= 0:
                raise ValueError
        except (TypeError, ValueError):
            listen_cfg["self_suppress_seconds"] = DEFAULT_LISTEN_SELF_SUPPRESS_S
        if "show_overlay" not in listen_cfg:
            listen_cfg["show_overlay"] = False
        return listen_cfg

    def _desktop_capture_config(self) -> dict:
        return self._vrc_listen_config()

    def _sync_settings_window_vrc_listen_state(self) -> None:
        win = getattr(self, "_settings_window", None)
        if win is None:
            return
        try:
            if not win.winfo_exists():
                self._settings_window = None
                return
            win.sync_vrc_listen_state(
                enabled=self._desktop_capture_enabled,
                show_overlay=self._listen_overlay_enabled,
            )
        except Exception:
            self._settings_window = None

    def _on_settings_window_closed(self) -> None:
        self._settings_window = None

    def _compute_listen_availability(
        self,
        *,
        refresh_devices: bool = False,
    ) -> tuple[bool, str | None]:
        if refresh_devices:
            self._load_desktop_devices()
        if not self._listen_translation_available():
            return False, self._copy("desktop_audio_requires_translation")
        if not self._is_vrchat_running():
            return False, self._copy("desktop_audio_requires_vrchat")
        if self._desktop_output_device_name() is None:
            return False, self._copy("desktop_audio_unavailable_body")
        return True, None

    def _refresh_listen_availability(self, *, refresh_devices: bool = False) -> bool:
        available, reason = self._compute_listen_availability(
            refresh_devices=refresh_devices,
        )
        self._listen_available = available
        self._listen_unavailable_reason = reason
        return available

    def _log_listen_environment(self, stage: str) -> None:
        try:
            process_audio = inspect_process_output_state(("VRChat.exe",))
        except Exception:
            logger.exception("Failed to inspect VRChat output state")
            process_audio = {}
        logger.info(
            "Desktop listen environment [%s] enabled=%s running=%s available=%s selected_output=%s active_output=%s default_output=%s process_audio=%s",
            stage,
            self._desktop_capture_enabled,
            self._listen_running,
            self._listen_available,
            self._desktop_output_device_name(),
            self._active_listen_output_device_name,
            default_output_device_name(),
            process_audio,
        )

    def _maybe_log_listen_diagnostics(self) -> None:
        now = time.monotonic()
        if (now - self._last_listen_diagnostic_log_at) < LISTEN_DIAGNOSTIC_IDLE_S:
            return
        recorder = self._listen_recorder
        if recorder is None or not isinstance(recorder, DesktopAudioRecorder):
            return
        if (now - self._last_listen_result_at) < LISTEN_DIAGNOSTIC_IDLE_S:
            return
        stats = recorder.diagnostics_snapshot()
        last_non_silent_at = float(stats.get("last_non_silent_at") or 0.0)
        audio_state = "no_loopback_audio"
        if last_non_silent_at > 0 and (now - last_non_silent_at) < LISTEN_DIAGNOSTIC_IDLE_S:
            audio_state = "audio_present_but_no_result"
        process_audio = inspect_process_output_state(("VRChat.exe",))
        log_fn = logger.warning
        if audio_state == "no_loopback_audio" and not bool(process_audio.get("has_active_audio_session", False)):
            log_fn = logger.info
        log_fn(
            "Desktop listen diagnostics state=%s idle_for=%.1fs stats=%s process_audio=%s mic_active=%s output_format=%s self_suppress=%s",
            audio_state,
            now - self._last_listen_result_at if self._last_listen_result_at > 0 else now,
            stats,
            process_audio,
            self._active_mic_input_device_name,
            self._get_output_format(),
            bool(self._desktop_capture_config().get("self_suppress", False)),
        )
        self._last_listen_diagnostic_log_at = now

    def _begin_listen_transition(self) -> bool:
        if self._destroying:
            return False
        if time.monotonic() < self._listen_toggle_cooldown_until:
            return False
        if not self._listen_transition_lock.acquire(blocking=False):
            return False
        self._listen_transitioning = True
        self._refresh_desktop_capture_button()
        return True

    def _end_listen_transition(self) -> None:
        self._listen_transitioning = False
        self._listen_toggle_cooldown_until = time.monotonic() + 0.35
        self._refresh_desktop_capture_button()
        try:
            self._listen_transition_lock.release()
        except RuntimeError:
            pass

    def _load_desktop_devices(self) -> None:
        # PyAudioWPatch (= PortAudio) names are identical to sounddevice names, so
        # there is no cross-library mismatch between device list and loopback lookup.
        devices: dict[str, int] = {}
        for device in _list_desktop_output_devices():
            name = str(device.get("name", "")).strip()
            if name and name not in devices:
                devices[name] = 0  # value unused; presence is what matters
        if not devices:
            # Fallback: sounddevice WASAPI output list (same PortAudio names)
            for device in AudioRecorder.list_loopback_devices():
                name = str(device.get("name", "")).strip()
                if name and name not in devices:
                    devices[name] = int(device.get("index", -1))
        self._desktop_devices = devices

    def _match_desktop_device_name(self, preferred_name: str | None) -> str | None:
        configured = str(preferred_name or "").strip()
        if not configured:
            return None
        # Exact match (common case — all names now in PortAudio namespace)
        if configured in self._desktop_devices:
            return configured
        # Fuzzy match: handles COM full names ("Speakers (Realtek HD Audio)")
        # vs. PortAudio names ("Speakers (Realtek HD Aud", truncated to ~31 chars)
        pref_candidates = set(_loopback_name_candidates(configured))
        for name in self._desktop_devices:
            if pref_candidates & set(_loopback_name_candidates(name)):
                return name
        # Last-resort substring match on normalized names
        norm = self._normalize_audio_device_name(configured)
        for name in self._desktop_devices:
            candidate = self._normalize_audio_device_name(name)
            if norm and (norm in candidate or candidate in norm):
                return name
        return None

    def _auto_detect_listen_device_name(self) -> str | None:
        # 1. VRChat process detection — most specific signal
        raw_vrc = detect_process_output_device_name(("VRChat.exe",))
        detected = self._match_desktop_device_name(raw_vrc)
        if detected is not None:
            return detected
        # 2. Default output device — PyAudioWPatch and sounddevice share PortAudio
        #    so the name from either matches our device list directly.
        raw_default = default_output_device_name()
        matched_default = self._match_desktop_device_name(raw_default)
        return matched_default

    def _desktop_output_device_name(self) -> str | None:
        desktop_cfg = self._desktop_capture_config()
        configured = self._match_desktop_device_name(
            str(desktop_cfg.get("loopback_device") or "").strip()
        )
        if configured is not None:
            return configured
        return self._auto_detect_listen_device_name()

    def _desktop_input_device_index(self) -> int | None:
        device_name = self._desktop_output_device_name()
        if not device_name:
            return None
        return self._desktop_devices.get(device_name)

    def _listen_uses_auto_output_device(self) -> bool:
        # 用户未手动指定回环设备时为自动模式
        configured = str(self._desktop_capture_config().get("loopback_device") or "").strip()
        return not configured

    def _desktop_device_signature(self) -> tuple[tuple[str, ...], str | None]:
        # 返回 (当前设备名称集合, 活动设备名)，用于检测设备列表或默认输出的变化
        self._load_desktop_devices()
        return tuple(sorted(self._desktop_devices)), self._desktop_output_device_name()

    def _listen_target_language(self) -> str:
        listen_cfg = self._desktop_capture_config()
        target = str(listen_cfg.get("target_language", "zh")).strip() or "zh"
        return target

    def _listen_send_to_chatbox_enabled(self) -> bool:
        listen_cfg = self._desktop_capture_config()
        return bool(listen_cfg.get("send_to_chatbox", True))

    def _mic_send_to_chatbox_enabled(self) -> bool:
        return bool(self._translation_config().get("send_to_chatbox", True))

    def _listen_source_language(self) -> str | None:
        listen_cfg = self._desktop_capture_config()
        source = str(listen_cfg.get("source_language", "auto")).strip() or "auto"
        return None if source == "auto" else source

    def _listen_self_suppress_seconds(self) -> float:
        listen_cfg = self._desktop_capture_config()
        try:
            value = float(listen_cfg.get("self_suppress_seconds", DEFAULT_LISTEN_SELF_SUPPRESS_S))
            if value <= 0:
                raise ValueError
            return value
        except (TypeError, ValueError):
            return DEFAULT_LISTEN_SELF_SUPPRESS_S

    def _listen_asr_language(self) -> str | None:
        source = self._listen_source_language()
        if source in ASR_HINT_LANGUAGE_CODES:
            return source
        return None

    def _listen_segment_duration_s(self) -> float:
        listen_cfg = self._desktop_capture_config()
        try:
            value = float(
                listen_cfg.get("segment_duration_s", DEFAULT_LISTEN_SEGMENT_DURATION_S)
            )
            if value <= 0:
                raise ValueError
            return value
        except (TypeError, ValueError):
            legacy_interval_ms = self._config.get("audio", {}).get("desktop_chunk_interval_ms")
            try:
                fallback = float(legacy_interval_ms) / 1000.0
                if fallback <= 0:
                    raise ValueError
                return fallback
            except (TypeError, ValueError):
                return DEFAULT_LISTEN_SEGMENT_DURATION_S

    def _listen_tail_silence_s(self) -> float:
        listen_cfg = self._desktop_capture_config()
        try:
            value = float(
                listen_cfg.get("tail_silence_s", DEFAULT_LISTEN_TAIL_SILENCE_S)
            )
            if value <= 0:
                raise ValueError
            return value
        except (TypeError, ValueError):
            try:
                fallback = float(
                    self._config.get("audio", {}).get(
                        "vad_silence_threshold",
                        DEFAULT_LISTEN_TAIL_SILENCE_S,
                    )
                )
                if fallback <= 0:
                    raise ValueError
                return fallback
            except (TypeError, ValueError):
                return DEFAULT_LISTEN_TAIL_SILENCE_S

    def _listen_feature_configured(self) -> bool:
        if not self._desktop_capture_enabled:
            return False
        available, _ = self._compute_listen_availability(refresh_devices=True)
        return available

    @staticmethod
    def _create_loopback_extra_settings():
        try:
            return sd.WasapiSettings(loopback=True)
        except Exception:
            return None

    def _set_desktop_capture_enabled(self, enabled: bool, *, persist: bool) -> None:
        self._desktop_capture_enabled = bool(enabled)
        self._refresh_desktop_capture_button()
        self._sync_settings_window_vrc_listen_state()
        logger.info(
            "Desktop listen preference updated (enabled=%s persist=%s running=%s available=%s)",
            self._desktop_capture_enabled,
            persist,
            self._listen_running,
            self._listen_available,
        )
        if not persist:
            return
        desktop_cfg = self._desktop_capture_config()
        if bool(desktop_cfg.get("enabled", False)) != self._desktop_capture_enabled:
            desktop_cfg["enabled"] = self._desktop_capture_enabled
            self._save_config_now()

    def _set_listen_overlay_enabled(self, enabled: bool, *, persist: bool) -> None:
        self._listen_overlay_enabled = bool(enabled)
        self._refresh_listen_overlay_button()
        self._sync_settings_window_vrc_listen_state()
        logger.info(
            "Listen overlay preference updated (enabled=%s persist=%s)",
            self._listen_overlay_enabled,
            persist,
        )
        if self._listen_overlay_enabled:
            if self._floating_window is None:
                self._floating_window = FloatingWindow(
                    self,
                    self._ui_lang,
                    on_resend=self._resend_history_to_vrc,
                    on_close=lambda: self._set_listen_overlay_enabled(False, persist=True),
                )
            try:
                self._floating_window.reveal()
            except Exception:
                pass
        elif self._floating_window is not None:
            try:
                self._floating_window.hide()
            except Exception:
                pass
        if not persist:
            return
        desktop_cfg = self._desktop_capture_config()
        if bool(desktop_cfg.get("show_overlay", False)) != self._listen_overlay_enabled:
            desktop_cfg["show_overlay"] = self._listen_overlay_enabled
            self._save_config_now()

    def _start_listen(self) -> None:
        if self._listen_recorder is not None:
            return

        self._log_listen_environment("before_start")
        self._load_desktop_devices()
        device_name = self._desktop_output_device_name()
        if device_name is None:
            raise RuntimeError(self._copy("desktop_audio_unavailable_body"))

        audio_cfg = self._config.get("audio", {})
        listen_segment_duration_s = self._listen_segment_duration_s()
        listen_tail_silence_s = self._listen_tail_silence_s()
        # Use a shorter partial cadence than the final segment length so reverse
        # translation can surface text earlier during continuous speech, while
        # still keeping a larger recognition window for steadier results.
        desktop_chunk_interval_ms = min(
            max(int(round(listen_segment_duration_s * 500.0)), 700),
            1400,
        )
        desktop_chunk_window_s = listen_segment_duration_s
        logger.debug(
            "Desktop listen partial cadence configured (segment_s=%.2f chunk_interval_ms=%s chunk_window_s=%.2f)",
            listen_segment_duration_s,
            desktop_chunk_interval_ms,
            desktop_chunk_window_s,
        )
        self._listen_recorder = DesktopAudioRecorder(
            on_segment=lambda audio: self._on_audio_segment(audio, DESKTOP_SOURCE),
            on_chunk=lambda audio: self._on_audio_chunk(audio, DESKTOP_SOURCE),
            sample_rate=audio_cfg.get("sample_rate", 16000),
            frame_duration_ms=audio_cfg.get("frame_duration_ms", 30),
            # Use DesktopAudioRecorder's game-audio defaults when not explicitly configured
            vad_sensitivity=audio_cfg.get("vad_sensitivity", 1),
            silence_threshold_s=listen_tail_silence_s,
            vad_speech_ratio=audio_cfg.get("vad_speech_ratio", 0.72),
            vad_activation_threshold_s=audio_cfg.get("vad_activation_threshold_s", 0.24),
            vad_min_rms=audio_cfg.get("vad_min_rms", 0.02),
            min_segment_s=audio_cfg.get("min_segment_s", 0.45),
            partial_min_speech_s=audio_cfg.get("partial_min_speech_s", 0.45),
            max_segment_s=audio_cfg.get("max_segment_s", 12.0),
            # Keep desktop listen aligned with the UI copy: the denoise preset is
            # for microphone preprocessing only.
            denoise_strength=0.0,
            output_device_name=device_name,
            on_vad_state=lambda state: self._on_source_vad_state(DESKTOP_SOURCE, state),
            chunk_interval_ms=desktop_chunk_interval_ms,
            chunk_window_s=desktop_chunk_window_s,
            on_runtime_error=lambda message: self._call_in_ui(
                lambda m=message: self._handle_desktop_capture_runtime_error(m)
            ),
        )
        try:
            self._listen_recorder.start()
            self._listen_running = True
            self._active_listen_output_device_name = device_name
            self._last_desktop_device_signature = (tuple(sorted(self._desktop_devices)), device_name)
            logger.info("Desktop listen started successfully on output device: %s", device_name)
            self._log_listen_environment("after_start")
        except Exception:
            self._listen_recorder = None
            self._listen_running = False
            self._active_listen_output_device_name = None
            logger.exception("Desktop listen failed to start on output device: %s", device_name)
            raise
        finally:
            self._call_in_ui(lambda: self._refresh_listen_availability(refresh_devices=False))
            self._call_in_ui(self._refresh_runtime_status)

    def _start_desktop_capture(self) -> None:
        self._start_listen()

    def _stop_listen(self) -> None:
        self._desktop_in_speech = False
        self._listen_running = False
        self._active_listen_output_device_name = None
        self._last_desktop_device_signature = None
        self._reset_streaming_state(DESKTOP_SOURCE)
        if self._listen_recorder is not None:
            self._listen_recorder.stop()
            self._listen_recorder = None
        logger.info("Desktop listen stopped")
        self._sync_avatar_speaking_state(force=True)
        self._call_in_ui(lambda: self._refresh_listen_availability(refresh_devices=False))
        self._call_in_ui(self._refresh_runtime_status)

    def _stop_desktop_capture(self) -> None:
        self._stop_listen()

    def _toggle_listen(self) -> None:
        if self._listen_transitioning or time.monotonic() < self._listen_toggle_cooldown_until:
            return
        target_enabled = not self._desktop_capture_enabled
        logger.info("Desktop listen toggle requested -> %s", target_enabled)
        if target_enabled:
            self._set_desktop_capture_enabled(True, persist=True)
            available = self._refresh_listen_availability(refresh_devices=True)
            self._refresh_desktop_capture_button()
            if not self._running:
                self._set_bottom(self._copy("desktop_audio_saved"))
                if not available and self._listen_unavailable_reason:
                    messagebox.showwarning(
                        self._copy("desktop_audio_unavailable_title"),
                        self._listen_unavailable_reason,
                    )
                return
            if not available:
                self._set_bottom(self._listen_unavailable_reason or self._copy("desktop_audio_saved"))
                self._log_listen_environment("enable_blocked")
                messagebox.showwarning(
                    self._copy("desktop_audio_unavailable_title"),
                    self._listen_unavailable_reason or self._copy("desktop_audio_unavailable_body"),
                )
                return
            if self._listen_running:
                self._set_bottom(self._copy("desktop_audio_saved"))
                return
            if not self._begin_listen_transition():
                return
            failed_message: str | None = None
            try:
                self._start_listen()
            except Exception as exc:
                failed_message = str(exc)
            finally:
                self._end_listen_transition()
            if failed_message is not None:
                self._on_desktop_capture_start_failed(failed_message)
                return
            self._refresh_runtime_status()
            self._set_bottom(self._copy("desktop_audio_saved"))
            return

        self._set_desktop_capture_enabled(False, persist=True)
        if self._running and self._listen_recorder is not None:
            if not self._begin_listen_transition():
                return
            try:
                self._stop_listen()
            finally:
                self._end_listen_transition()
            self._refresh_runtime_status()
        else:
            self._refresh_listen_availability(refresh_devices=False)
            self._refresh_desktop_capture_button()
        self._set_bottom(self._copy("desktop_audio_saved"))

    def _toggle_desktop_capture(self) -> None:
        self._toggle_listen()

    def _toggle_listen_overlay(self) -> None:
        self._set_listen_overlay_enabled(
            not self._listen_overlay_enabled,
            persist=True,
        )

    def _on_desktop_capture_start_failed(self, message: str) -> None:
        self._refresh_listen_availability(refresh_devices=True)
        self._refresh_desktop_capture_button()
        self._set_bottom(self._copy("desktop_audio_failed", message=message))
        logger.warning("Desktop listen start/restart failed: %s", message)
        self._log_listen_environment("start_failed")
        messagebox.showwarning(
            self._copy("desktop_audio_unavailable_title"),
            self._copy("desktop_audio_failed", message=message),
        )

    def _handle_desktop_capture_runtime_error(self, message: str) -> None:
        # 桌面录音运行时报错的 UI 线程回调，触发重启流程
        if self._destroying or not self._running or not self._desktop_capture_enabled:
            return
        self._restart_desktop_capture(message=message)

    def _restart_desktop_capture(self, message: str | None = None) -> None:
        # 停止当前采集 → 重新加载设备 → 重新启动；重启失败则关闭功能并弹窗提示
        if self._listen_recovery_in_progress or self._destroying:
            return
        if not self._begin_listen_transition():
            return
        logger.warning("Restarting desktop listen (reason=%s)", message or "unknown")

        recorder = self._listen_recorder
        self._listen_recovery_in_progress = True
        try:
            self._desktop_in_speech = False
            self._listen_running = False
            self._active_listen_output_device_name = None
            self._last_desktop_device_signature = None
            self._reset_streaming_state(DESKTOP_SOURCE)

            if recorder is not None:
                self._listen_recorder = None
                try:
                    recorder.stop()
                except Exception:
                    pass

            self._sync_avatar_speaking_state(force=True)
            self._refresh_runtime_status()

            if not self._running or not self._desktop_capture_enabled:
                return

            if not self._refresh_listen_availability(refresh_devices=True):
                failure = self._listen_unavailable_reason or str(message or "").strip()
                if not failure:
                    failure = self._copy("desktop_audio_runtime_stopped")
                self._on_desktop_capture_start_failed(failure)
                return
            self._start_listen()
            self._refresh_runtime_status()
        except Exception as exc:
            self._refresh_listen_availability(refresh_devices=True)
            self._refresh_desktop_capture_button()
            failure = str(exc).strip() or str(message or "").strip()
            if not failure:
                failure = self._copy("desktop_audio_runtime_stopped")
            self._on_desktop_capture_start_failed(failure)
        finally:
            self._listen_recovery_in_progress = False
            self._end_listen_transition()

    def _schedule_desktop_audio_watch(self, delay_ms: int = 2500) -> None:
        # 调度下次轮询，取消旧的待执行任务后重新注册
        if self._destroying:
            return
        if self._desktop_audio_watch_after_id is not None:
            try:
                self.after_cancel(self._desktop_audio_watch_after_id)
            except Exception:
                pass
        self._desktop_audio_watch_after_id = self.after(delay_ms, self._poll_desktop_audio_watch)

    def _poll_desktop_audio_watch(self) -> None:
        self._desktop_audio_watch_after_id = None
        if self._destroying:
            return

        try:
            was_available = self._listen_available
            self._refresh_listen_availability(refresh_devices=True)
            self._refresh_desktop_capture_button()
            recorder = self._listen_recorder
            # 检查1：录音线程意外停止 → 重启
            if (
                recorder is not None
                and not recorder.is_running
                and self._running
                and self._desktop_capture_enabled
            ):
                logger.warning("Desktop listen recorder stopped unexpectedly; triggering restart")
                self._restart_desktop_capture(
                    message=recorder.last_error or self._copy("desktop_audio_runtime_stopped")
                )
                return

            if (
                self._running
                and self._desktop_capture_enabled
                and self._listen_recorder is None
                and not self._listen_running
                and not self._listen_transitioning
                and not was_available
                and self._listen_available
            ):
                self._restart_desktop_capture()
                return

            if self._running and self._desktop_capture_enabled and self._listen_recorder is not None:
                self._maybe_log_listen_diagnostics()

            # 检查2：仅自动模式下监测默认输出设备变更 → 重启以跟随新设备
            if not (
                self._running
                and self._desktop_capture_enabled
                and self._listen_recorder is not None
                and self._listen_uses_auto_output_device()
            ):
                self._last_desktop_device_signature = None
                return

            signature = self._desktop_device_signature()
            previous = self._last_desktop_device_signature
            self._last_desktop_device_signature = signature
            if previous is None:
                return

            previous_active = self._normalize_audio_device_name(previous[1] or "")
            current_active = self._normalize_audio_device_name(signature[1] or "")
            if current_active and current_active != previous_active:
                logger.info(
                    "Desktop output device changed in auto mode (previous=%s current=%s)",
                    previous[1],
                    signature[1],
                )
                self._restart_desktop_capture()
        except Exception:
            logger.exception("Desktop audio watch failed")
        finally:
            if not self._destroying:
                self._schedule_desktop_audio_watch()

    def _format_listen_text(self, text: str) -> str:
        clean = str(text or "").strip()
        if not clean:
            return ""
        return f"{LISTEN_PREFIX} {clean}"

    def _ensure_floating_window(self) -> FloatingWindow:
        if self._floating_window is None:
            self._floating_window = FloatingWindow(
                self,
                self._ui_lang,
                on_resend=self._resend_history_to_vrc,
                on_close=lambda: self._set_listen_overlay_enabled(False, persist=True),
            )
        return self._floating_window

    def _show_listen_translation(
        self,
        text: str,
        *,
        source: str = "listen",
        payload: str | None = None,
    ) -> None:
        if not self._listen_overlay_enabled:
            return
        translated = str(text or "").strip()
        if not translated:
            return
        self._ensure_floating_window().show_translation(
            translated,
            source=source,
            payload=payload,
        )

    def _add_mic_history(self, text: str, payload: str | None = None) -> None:
        # 将自身麦克风翻译写入悬浮窗历史（悬浮窗不存在时跳过）
        if self._floating_window is None:
            return
        msg = str(text or "").strip()
        if not msg:
            return
        try:
            self._floating_window.add_history_entry(msg, source="mic", payload=payload)
        except Exception:
            pass

    def _resend_history_to_vrc(self, text: str, source: str = "listen") -> None:
        message = VRCOSCSender._normalize_text(text)
        if not message:
            return
        if not self._is_vrchat_running():
            messagebox.showwarning(
                self._t("game_not_running_title"),
                self._t("game_not_running_message"),
            )
            return

        try:
            sent = self._ensure_sender().send_chatbox(message, force=True)
            if sent:
                self._own_msgs.add(sent)
                self._set_bottom(self._copy("history_resend_sent"))
        except Exception as exc:
            self._pulse_avatar_error()
            messagebox.showerror(self._t("send_failed_title"), str(exc))

    @staticmethod
    def _format_listen_translation(original: str, translated: str) -> str:
        src = str(original or "").strip()
        tgt = str(translated or "").strip()
        if not src:
            return tgt
        if not tgt or src == tgt:
            return src
        return f"{src}（{tgt}）"

    @staticmethod
    def _normalize_compare_text(text: str) -> str:
        cleaned = re.sub(r"\W+", "", str(text or "").lower(), flags=re.UNICODE)
        return cleaned.strip()

    def _remember_recent_mic_texts(self, *texts: str) -> None:
        now = time.monotonic()
        self._prune_recent_mic_texts(now)
        for text in texts:
            normalized = self._normalize_compare_text(text)
            if normalized:
                self._recent_mic_texts.append((now, normalized))

    def _prune_recent_mic_texts(self, now: float | None = None) -> None:
        cutoff = (time.monotonic() if now is None else now) - self._listen_self_suppress_seconds()
        while self._recent_mic_texts and self._recent_mic_texts[0][0] < cutoff:
            self._recent_mic_texts.popleft()

    def _remember_recent_listen_text(self, text: str) -> None:
        normalized = self._normalize_compare_text(text)
        if not normalized:
            return
        now = time.monotonic()
        self._prune_recent_listen_texts(now)
        self._recent_listen_texts.append((now, normalized))

    def _prune_recent_listen_texts(self, now: float | None = None) -> None:
        cutoff = (time.monotonic() if now is None else now) - LISTEN_RESULT_DEDUPE_WINDOW_S
        while self._recent_listen_texts and self._recent_listen_texts[0][0] < cutoff:
            self._recent_listen_texts.popleft()

    def _is_recent_duplicate_listen_text(self, text: str) -> bool:
        return self._recent_duplicate_listen_reason(text) is not None

    def _recent_duplicate_listen_reason(self, text: str) -> str | None:
        normalized = self._normalize_compare_text(text)
        if not normalized:
            return None
        now = time.monotonic()
        self._prune_recent_listen_texts(now)
        if any(normalized == recent for _, recent in self._recent_listen_texts):
            return "recent_duplicate"
        return None

    @staticmethod
    def _translation_context_source(source: str) -> str:
        if source == DESKTOP_SOURCE:
            return "listen"
        if source == MIC_SOURCE:
            return "mic"
        return "default"

    @staticmethod
    def _listen_translation_source_language(selected_src_lang: str | None) -> str:
        if selected_src_lang:
            return selected_src_lang
        return "auto"

    def _should_suppress_listen_result(self, text: str) -> bool:
        return self._listen_suppress_reason(text) is not None

    def _listen_suppress_reason(self, text: str) -> str | None:
        if not bool(self._desktop_capture_config().get("self_suppress", False)):
            return None
        now = time.monotonic()
        suppress_seconds = self._listen_self_suppress_seconds()
        if self._mic_in_speech:
            return "mic_in_speech"
        if (now - max(self._last_mic_activity_at, self._last_mic_result_at)) <= suppress_seconds:
            return "recent_mic_activity"

        normalized = self._normalize_compare_text(text)
        if not normalized:
            return None

        self._prune_recent_mic_texts(now)
        for _, recent in self._recent_mic_texts:
            if normalized == recent:
                return "same_as_recent_mic_result"
            if len(normalized) >= 8 and len(recent) >= 8:
                if normalized in recent or recent in normalized:
                    return "similar_to_recent_mic_result"
        return None

    def _send_listen_chatbox(self, text: str, session_id: int) -> None:
        message = VRCOSCSender._normalize_text(text)
        if not message or self._destroying:
            return

        try:
            with self._listen_send_lock:
                if self._destroying or not self._running or session_id != self._listen_session:
                    return
                wait_s = MIN_CHATBOX_COOLDOWN_S - (
                    time.monotonic() - self._listen_last_send_at
                )
                if wait_s > 0:
                    time.sleep(wait_s)
                if self._destroying or not self._running or session_id != self._listen_session:
                    return
                sent = self._ensure_sender().send_chatbox(message)
                if sent:
                    self._listen_last_send_at = time.monotonic()
                    self._own_msgs.add(sent)
                    logger.debug("Desktop listen chatbox send queued (session=%s text=%s)", session_id, sent)
        except Exception as exc:
            error_text = str(exc).strip() or exc.__class__.__name__
            logger.warning("Desktop listen chatbox send failed: %s", error_text)
            self._call_in_ui(
                lambda m=error_text[:120]: self._set_bottom(
                    f"Chatbox send failed: {m}"
                )
            )
            self._call_in_ui(self._pulse_avatar_error)

    def _send_listen_chatbox_async(self, text: str, session_id: int) -> None:
        message = VRCOSCSender._normalize_text(text)
        if not message or self._destroying:
            return
        logger.debug("Scheduling desktop listen chatbox send (session=%s text=%s)", session_id, message)
        threading.Thread(
            target=self._send_listen_chatbox,
            args=(message, session_id),
            daemon=True,
        ).start()

    def _asr_for_source(self, source: str):
        if source == DESKTOP_SOURCE:
            return self._listen_asr
        return self._asr

    def _listen_translation_available(self) -> bool:
        return self._get_output_format() != "original_only"

    def _ensure_translator_ready(self) -> bool:
        if self._translator is not None:
            return True

        try:
            self._translator = create_translator(self._config)
            return True
        except ValueError:
            messagebox.showwarning(
                self._t("api_missing_title"),
                self._t("api_missing_message"),
            )
            return False
        except Exception as e:
            friendly = self._format_translation_error(e)
            messagebox.showerror(
                self._t("translation_init_failed_title"),
                friendly.detailed_message,
            )
            return False

    def _get_output_format(self) -> str:
        return normalize_output_format(
            self._config.get("translation", {}).get("output_format")
        )

    def _listening_requires_translation(self) -> bool:
        return self._listen_translation_available()

    def _streaming_config(self) -> dict:
        return self._config.get("asr", {}).get("streaming", {})

    def _create_streaming_merger(self) -> StreamingMerger:
        streaming_cfg = self._streaming_config()
        return StreamingMerger(
            stable_repeats=streaming_cfg.get("partial_stability_hits", 2)
        )

    def _reset_streaming_state(self, source: str | None = None):
        with self._merge_lock:
            if source in (None, MIC_SOURCE):
                self._partial_generation += 1
                self._partial_merger.reset()
            if source in (None, DESKTOP_SOURCE):
                self._desktop_partial_generation += 1
                self._desktop_partial_merger.reset()

    def _source_generation(self, source: str) -> int:
        if source == DESKTOP_SOURCE:
            return self._desktop_partial_generation
        return self._partial_generation

    def _source_merger(self, source: str) -> StreamingMerger:
        if source == DESKTOP_SOURCE:
            return self._desktop_partial_merger
        return self._partial_merger

    def _partial_worker_loop(self, source: str) -> None:
        task_queue = self._partial_task_queue_for_source(source)
        while True:
            payload = task_queue.get()
            if payload is None:
                return
            audio, asr_lang, generation, session_id, source = payload
            self._process_partial_audio_chunk(audio, asr_lang, generation, session_id, source)

    def _final_worker_loop(self, source: str) -> None:
        task_queue = self._final_task_queue_for_source(source)
        while True:
            payload = task_queue.get()
            if payload is None:
                return
            audio, asr_lang, selected_src_lang, session_id, source = payload
            self._process_final_audio_segment(audio, asr_lang, selected_src_lang, session_id, source)

    def _on_audio_chunk(self, audio, source: str = MIC_SOURCE):
        if not self._running:
            return

        generation = self._source_generation(source)
        asr_lang = self._listen_asr_language() if source == DESKTOP_SOURCE else self._current_asr_lang
        queue_result = self._enqueue_latest(
            self._partial_task_queue_for_source(source),
            (audio, asr_lang, generation, self._listen_session, source),
        )
        if queue_result != "enqueued":
            logger.debug(
                "Partial queue update result=%s source=%s generation=%s",
                queue_result,
                source,
                generation,
            )

    def _process_partial_audio_chunk(
        self,
        audio,
        asr_lang,
        generation: int,
        session_id: int,
        source: str,
    ):
        if not self._running or session_id != self._listen_session:
            return
        try:
            asr_started_at = time.monotonic()
            text = self._asr_for_source(source).transcribe(
                audio,
                language=asr_lang,
                is_final=False,
            )
            if (
                not text
                or generation != self._source_generation(source)
                or not self._running
                or session_id != self._listen_session
            ):
                return

            with self._merge_lock:
                merged = self._source_merger(source).ingest_partial(text)
            if (
                merged
                and generation == self._source_generation(source)
                and self._running
                and session_id == self._listen_session
            ):
                self._call_in_ui(
                    lambda t=merged, g=generation, s=source: self._show_partial_text(t, g, s)
                )
            elif merged:
                logger.debug(
                    "Dropped partial result after generation/session changed (source=%s generation=%s session=%s)",
                    source,
                    generation,
                    session_id,
                )
            logger.debug(
                "Partial ASR processed (source=%s chars=%s duration_ms=%.0f)",
                source,
                len(text or ""),
                (time.monotonic() - asr_started_at) * 1000.0,
            )
        except Exception:
            logger.exception("Partial ASR processing failed (source=%s)", source)

    def _show_partial_text(self, text: str, generation: int, source: str):
        if not self._running or generation != self._source_generation(source) or not text:
            return
        rendered = self._format_listen_text(text) if source == DESKTOP_SOURCE else text
        text_color = "#0f766e" if source == DESKTOP_SOURCE else TEXT_SEC
        self._set_source_text(rendered, text_color=text_color)

    def _process_final_audio_segment(
        self,
        audio,
        asr_lang,
        selected_src_lang,
        session_id: int,
        source: str,
    ):
        if not self._running or session_id != self._listen_session:
            return
        fmt = self._get_output_format()
        requires_translation = source == DESKTOP_SOURCE or fmt != "original_only"
        try:
            if requires_translation:
                self._set_translating_state(True)
            asr_started_at = time.monotonic()
            text = self._asr_for_source(source).transcribe(
                audio,
                language=asr_lang,
                is_final=True,
            )
            if not self._running or session_id != self._listen_session:
                return

            with self._merge_lock:
                text = self._source_merger(source).ingest_final(text)
            if not text:
                logger.debug(
                    "Dropped empty final ASR result (source=%s asr_ms=%.0f)",
                    source,
                    (time.monotonic() - asr_started_at) * 1000.0,
                )
                return
            # Drop noise-only ASR outputs for desktop audio (single chars, punctuation, etc.)
            if source == DESKTOP_SOURCE and len(text.strip()) < 2:
                logger.debug("Dropped short desktop ASR result: %r", text)
                return
            if not self._running or session_id != self._listen_session:
                return
            if source == DESKTOP_SOURCE:
                suppress_reason = self._listen_suppress_reason(text)
                if suppress_reason:
                    logger.debug(
                        "Suppressed desktop ASR result (reason=%s text=%r)",
                        suppress_reason,
                        text,
                    )
                    return

            translator = self._translator
            context_source = self._translation_context_source(source)
            rendered_source = self._format_listen_text(text) if source == DESKTOP_SOURCE else text
            self._call_in_ui(lambda t=rendered_source: self._set_source_text(t))

            translated = None
            if source == DESKTOP_SOURCE:
                duplicate_reason = self._recent_duplicate_listen_reason(text)
                if duplicate_reason:
                    logger.debug(
                        "Dropped duplicate desktop ASR result (reason=%s text=%r)",
                        duplicate_reason,
                        text,
                    )
                    return
                self._remember_recent_listen_text(text)
                src_lang = self._listen_translation_source_language(selected_src_lang)
                tgt_lang = self._listen_target_language()
                if src_lang != "auto" and src_lang == tgt_lang:
                    translated = text
                elif translator is None:
                    raise RuntimeError("Translator is not ready")
                else:
                    translate_started_at = time.monotonic()
                    translated = translator.translate(
                        text,
                        src_lang,
                        tgt_lang,
                        context_source=context_source,
                    )
                    logger.debug(
                        "Desktop translation finished (src_lang=%s tgt_lang=%s asr_ms=%.0f translate_ms=%.0f)",
                        src_lang,
                        tgt_lang,
                        (translate_started_at - asr_started_at) * 1000.0,
                        (time.monotonic() - translate_started_at) * 1000.0,
                    )
                if not self._running or session_id != self._listen_session:
                    return

                listen_text = self._format_listen_translation(text, translated)
                chatbox_text = self._format_listen_text(listen_text)
                self._call_in_ui(lambda t=chatbox_text: self._show_tgt(t))
                self._call_in_ui(
                    lambda t=listen_text, payload=chatbox_text: self._show_listen_translation(
                        t,
                        payload=payload,
                    )
                )
                self._last_listen_result_at = time.monotonic()
            elif fmt == "original_only":
                chatbox_text = text
            else:
                src_lang = selected_src_lang if selected_src_lang else detect_language(text)
                tgt_lang = self._current_tgt_lang
                if src_lang == tgt_lang:
                    translated = text
                elif translator is None:
                    raise RuntimeError("Translator is not ready")
                else:
                    translate_started_at = time.monotonic()
                    translated = translator.translate(
                        text,
                        src_lang,
                        tgt_lang,
                        context_source=context_source,
                    )
                    logger.debug(
                        "Microphone translation finished (src_lang=%s tgt_lang=%s asr_ms=%.0f translate_ms=%.0f)",
                        src_lang,
                        tgt_lang,
                        (translate_started_at - asr_started_at) * 1000.0,
                        (time.monotonic() - translate_started_at) * 1000.0,
                    )
                if not self._running or session_id != self._listen_session:
                    return

                if src_lang == tgt_lang:
                    chatbox_text = text
                elif fmt == "translated_only":
                    chatbox_text = translated
                elif fmt == "original_with_translated":
                    chatbox_text = f"{text}({translated})"
                else:
                    chatbox_text = f"{translated}({text})"
                self._call_in_ui(lambda t=translated: self._show_tgt(t))

            if not self._running or session_id != self._listen_session:
                return
            if source == DESKTOP_SOURCE:
                if self._listen_send_to_chatbox_enabled():
                    self._send_listen_chatbox_async(chatbox_text, session_id)
                else:
                    logger.info("Desktop listen result kept local only because send_to_chatbox is disabled")
            else:
                self._last_mic_result_at = time.monotonic()
                self._remember_recent_mic_texts(text, translated or "", chatbox_text)
                if self._mic_send_to_chatbox_enabled():
                    sent = self._ensure_sender().send_chatbox(chatbox_text)
                    if sent:
                        self._own_msgs.add(sent)
                else:
                    logger.info("Microphone result kept local only because send_to_chatbox is disabled")
                if self._desktop_capture_enabled:
                    mic_display = translated or text
                    self._call_in_ui(
                        lambda t=mic_display, payload=chatbox_text: self._add_mic_history(
                            t,
                            payload=payload,
                        )
                    )
        except Exception as exc:
            friendly = self._format_translation_error(exc)
            logger.exception("Final audio segment processing failed (source=%s)", source)
            self._call_in_ui(
                lambda message=friendly.short_message: self._set_bottom(message)
            )
            if source == DESKTOP_SOURCE:
                self._call_in_ui(
                    lambda message=friendly.inline_message: self._show_listen_translation(
                        message,
                        source="error",
                    )
                )
            self._call_in_ui(self._pulse_avatar_error)
        finally:
            if requires_translation:
                self._set_translating_state(False)
            if self._running and session_id == self._listen_session:
                self._call_in_ui(self._refresh_runtime_status)

    def _send_to_vrc(self):
        tgt_text = self._last_tgt_text
        src_text = self._src_text
        if not tgt_text and not src_text:
            return
        if not self._is_vrchat_running():
            messagebox.showwarning(
                self._t("game_not_running_title"),
                self._t("game_not_running_message"),
            )
            return

        fmt = self._get_output_format()
        if fmt == "original_only":
            chatbox_text = src_text or tgt_text
        elif fmt == "translated_only":
            chatbox_text = tgt_text or src_text
        elif fmt == "original_with_translated":
            chatbox_text = f"{src_text}({tgt_text})" if src_text and tgt_text else src_text or tgt_text
        else:
            chatbox_text = f"{tgt_text}({src_text})" if src_text and tgt_text else tgt_text or src_text

        try:
            sent = self._ensure_sender().send_chatbox(chatbox_text)
            if sent:
                self._own_msgs.add(sent)
        except Exception as e:
            self._pulse_avatar_error()
            messagebox.showerror(self._t("send_failed_title"), str(e))

    def _translate_manual(self):
        src_text = self._src_text
        if not src_text:
            return
        if self._translating:
            return
        if self._get_output_format() == "original_only":
            self._show_tgt(src_text)
            return

        src_code = self._src_lang_codes.get(self._src_lang_var.get(), "auto")
        if src_code == "auto":
            src_code = detect_language(src_text)
        tgt_code = self._target_lang_codes.get(self._tgt_var.get(), "ja")

        if src_code == tgt_code:
            self._show_tgt(src_text)
            return

        if not self._ensure_translator_ready():
            return

        self._translating = True
        self._set_translating_state(True)
        self._refresh_runtime_status()
        self._translate_btn.configure(state="disabled", text=self._t("translating"))
        threading.Thread(
            target=self._do_translate,
            args=(src_text, src_code, tgt_code),
            daemon=True,
        ).start()

    def _do_translate(self, text: str, src_lang: str, tgt_lang: str):
        try:
            result = self._translator.translate(
                text,
                src_lang,
                tgt_lang,
                context_source="manual",
            )
            self._call_in_ui(lambda: self._show_tgt(result))
        except Exception as e:
            friendly = self._format_translation_error(e)
            self._call_in_ui(self._pulse_avatar_error)
            self._call_in_ui(lambda: self._set_bottom(friendly.short_message))
            self._call_in_ui(
                lambda: self._show_tgt(friendly.inline_message, is_error=True)
            )
        finally:
            self._call_in_ui(self._reset_translate_btn)

    def _show_tgt(self, text: str, *, is_error: bool = False):
        if not is_error:
            self._last_tgt_text = text
        if text == self._tgt_rendered_text:
            return
        self._tgt_output.configure(state="normal")
        self._tgt_output.delete("1.0", "end")
        self._tgt_output.insert("1.0", text)
        self._tgt_output.configure(state="disabled")
        self._tgt_rendered_text = text

    def _reset_translate_btn(self):
        self._translating = False
        self._set_translating_state(False)
        self._translate_btn.configure(state="normal", text=self._t("translate"))
        self._refresh_runtime_status()

    def _load_devices(self):
        devices = AudioRecorder.list_devices()
        self._devices = {d["name"]: d["index"] for d in devices}
        self._load_desktop_devices()
        self._refresh_listen_availability(refresh_devices=False)
        preferred = self._resolve_mic_input_device_name(refresh_devices=False)
        self._set_selected_device(
            preferred,
            persist=False,
            mode=self._mic_input_device_mode(),
        )
        logger.debug(
            "Loaded audio devices (mode=%s configured=%s resolved=%s available=%s)",
            self._mic_input_device_mode(),
            self._configured_mic_input_device_name(),
            preferred,
            list(self._devices.keys()),
        )
        self._refresh_desktop_capture_button()

    @staticmethod
    def _format_device_label(name: str, limit: int = 30) -> str:
        text = (name or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[: max(0, limit - 3)]}..."

    def _set_selected_device(self, device_name: str | None, *, persist: bool, mode: str | None = None) -> None:
        safe_device_name = str(device_name or "").strip()
        target_mode = mode or self._mic_input_device_mode()
        self._device_var.set(safe_device_name)
        if hasattr(self, "_device_button") and self._device_button is not None:
            self._device_button.configure(
                text=self._format_device_label(
                    self._current_device_display_name(safe_device_name)
                )
            )
        if not persist:
            return
        audio_cfg = self._audio_config()
        desired_input = safe_device_name if target_mode == "fixed" and safe_device_name else None
        changed = False
        if audio_cfg.get("input_device_mode") != target_mode:
            audio_cfg["input_device_mode"] = target_mode
            changed = True
        if audio_cfg.get("input_device") != desired_input:
            audio_cfg["input_device"] = desired_input
            changed = True
        if changed:
            self._save_config_now()
            logger.info(
                "Microphone selection updated (mode=%s configured_input=%s active_display=%s)",
                target_mode,
                desired_input,
                safe_device_name,
            )

    def _choose_device(self, device_name: str) -> None:
        if device_name == AUTO_INPUT_DEVICE_TOKEN:
            resolved = self._resolve_mic_input_device_name(refresh_devices=True)
            self._set_selected_device(resolved, persist=True, mode="auto")
            if self._running:
                self._restart_microphone_capture(reason="user selected auto microphone mode")
            self._close_device_picker()
            return
        if device_name in getattr(self, "_devices", {}):
            self._set_selected_device(device_name, persist=True, mode="fixed")
            if self._running and self._normalize_audio_device_name(device_name) != self._normalize_audio_device_name(self._active_mic_input_device_name or ""):
                self._restart_microphone_capture(reason=f"user selected microphone: {device_name}")
        self._close_device_picker()

    def _open_device_picker(self) -> None:
        if self._device_picker_win and self._device_picker_win.winfo_exists():
            self._device_picker_win.deiconify()
            self._device_picker_win.lift()
            return

        popup = ctk.CTkToplevel(self)
        self._device_picker_win = popup
        popup.title(self._t("microphone"))
        apply_window_icon(popup)
        popup.geometry("430x400")
        popup._popup_size = (430, 400)
        popup.resizable(False, False)
        popup.attributes("-topmost", True)
        popup.transient(self)
        popup.grab_set()
        popup.configure(fg_color=BG_PRIMARY)
        popup.protocol("WM_DELETE_WINDOW", self._close_device_picker)

        outer = ctk.CTkFrame(popup, fg_color=BG_PRIMARY)
        outer.pack(fill="both", expand=True, padx=18, pady=18)

        ctk.CTkLabel(
            outer,
            text=self._t("microphone"),
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(anchor="w", pady=(0, 4))

        ctk.CTkLabel(
            outer,
            text=self._format_device_label(self._current_device_display_name(), limit=54),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=380,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(0, 12))

        card = ctk.CTkFrame(
            outer,
            fg_color=BG_PANEL,
            corner_radius=22,
            border_width=1,
            border_color=GLASS_BORDER,
        )
        card.pack(fill="both", expand=True)

        list_frame = ctk.CTkScrollableFrame(
            card,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color="#c9d5e3",
            scrollbar_button_hover_color="#b7c6d8",
        )
        list_frame.pack(fill="both", expand=True, padx=10, pady=(10, 6))

        current_name = self._device_var.get()
        auto_selected = self._mic_input_device_mode() == "auto"
        auto_label = self._copy("mic_device_auto_option")
        if current_name:
            auto_label = self._copy("mic_device_auto_current", name=current_name)
        ctk.CTkButton(
            list_frame,
            text=auto_label,
            anchor="w",
            height=40,
            fg_color="#eef5ff" if auto_selected else "transparent",
            hover_color="#edf3fb",
            border_width=1 if auto_selected else 0,
            border_color="#bfdbff",
            corner_radius=14,
            text_color=ACCENT if auto_selected else TEXT_PRI,
            font=ctk.CTkFont(size=12, weight="bold" if auto_selected else "normal"),
            command=lambda: self._choose_device(AUTO_INPUT_DEVICE_TOKEN),
        ).pack(fill="x", padx=4, pady=4)
        for name in self._devices.keys():
            is_selected = not auto_selected and name == current_name
            ctk.CTkButton(
                list_frame,
                text=name,
                anchor="w",
                height=40,
                fg_color="#eef5ff" if is_selected else "transparent",
                hover_color="#edf3fb",
                border_width=1 if is_selected else 0,
                border_color="#bfdbff",
                corner_radius=14,
                text_color=ACCENT if is_selected else TEXT_PRI,
                font=ctk.CTkFont(size=12, weight="bold" if is_selected else "normal"),
                command=lambda selected=name: self._choose_device(selected),
            ).pack(fill="x", padx=4, pady=4)

        footer = ctk.CTkFrame(outer, fg_color="transparent")
        footer.pack(fill="x", pady=(12, 0))

        ctk.CTkButton(
            footer,
            text=self._t("cancel"),
            width=84,
            height=34,
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=10,
            text_color=TEXT_PRI,
            command=self._close_device_picker,
        ).pack(side="right")

        present_popup(popup, parent=self)

    def _close_device_picker(self) -> None:
        if self._device_picker_win and self._device_picker_win.winfo_exists():
            self._device_picker_win.destroy()
        self._device_picker_win = None

    @staticmethod
    def _get_system_default_input(devices: list[dict], names: list[str]) -> str:
        try:
            default_idx = sd.default.device[0]  # 0 is the input device slot
            if default_idx is not None and default_idx >= 0:
                default_name = sd.query_devices(default_idx)["name"]
                match = next((d["name"] for d in devices if d["name"] == default_name), None)
                if match and match in names:
                    return match
        except Exception:
            pass
        _SKIP = ("microsoft 映射", "microsoft sound mapper", "立体声混音", "stereo mix")
        return next(
            (n for n in names if not any(s in n.lower() for s in _SKIP)),
            names[0],
        )

    def _microphone_device_signature(self) -> tuple[tuple[str, ...], str | None, str, str | None, str | None]:
        devices = AudioRecorder.list_devices()
        self._devices = {d["name"]: d["index"] for d in devices}
        default_name = self._current_default_input_device_name(devices)
        mode = self._mic_input_device_mode()
        configured = self._configured_mic_input_device_name()
        resolved = self._resolve_mic_input_device_name(refresh_devices=False)
        return tuple(sorted(self._devices)), default_name, mode, configured, resolved

    def _create_microphone_recorder(self, dev_idx: int | None) -> AudioRecorder:
        audio_cfg = self._config.get("audio", {})
        streaming_cfg = self._streaming_config()
        return AudioRecorder(
            on_segment=lambda audio: self._on_audio_segment(audio, MIC_SOURCE),
            on_chunk=lambda audio: self._on_audio_chunk(audio, MIC_SOURCE),
            sample_rate=audio_cfg.get("sample_rate", 16000),
            frame_duration_ms=audio_cfg.get("frame_duration_ms", 30),
            vad_sensitivity=audio_cfg.get("vad_sensitivity", 2),
            silence_threshold_s=audio_cfg.get("vad_silence_threshold", 0.8),
            vad_speech_ratio=audio_cfg.get("vad_speech_ratio", 0.72),
            vad_activation_threshold_s=audio_cfg.get("vad_activation_threshold_s", 0.24),
            vad_min_rms=audio_cfg.get("vad_min_rms", 0.012),
            min_segment_s=audio_cfg.get("min_segment_s", 0.45),
            partial_min_speech_s=audio_cfg.get("partial_min_speech_s", 0.45),
            max_segment_s=audio_cfg.get("max_segment_s", 12.0),
            denoise_strength=audio_cfg.get("denoise_strength", 0.0),
            input_device=dev_idx,
            on_vad_state=lambda state: self._on_source_vad_state(MIC_SOURCE, state),
            chunk_interval_ms=streaming_cfg.get("chunk_interval_ms", 250),
            chunk_window_s=streaming_cfg.get("chunk_window_s", 1.6),
            ring_buffer_s=streaming_cfg.get("ring_buffer_s", 4.0),
            recent_speech_hold_s=streaming_cfg.get("recent_speech_hold_s", 0.8),
        )

    def _start_microphone_capture(self, device_name: str | None = None) -> None:
        target_name = str(device_name or self._resolve_mic_input_device_name(refresh_devices=True) or "").strip()
        if not target_name:
            raise RuntimeError(self._copy("mic_device_none"))
        dev_idx = self._devices.get(target_name)
        self._recorder = self._create_microphone_recorder(dev_idx)
        self._recorder.start()
        self._active_mic_input_device_name = (
            self._recorder.active_input_device_name or target_name
        )
        self._last_mic_device_signature = self._microphone_device_signature()
        self._call_in_ui(
            lambda name=self._active_mic_input_device_name, mode=self._mic_input_device_mode():
            self._set_selected_device(name, persist=False, mode=mode)
        )
        logger.info(
            "Microphone capture started (mode=%s configured=%s target=%s active=%s index=%s)",
            self._mic_input_device_mode(),
            self._configured_mic_input_device_name(),
            target_name,
            self._active_mic_input_device_name,
            dev_idx,
        )

    def _stop_microphone_capture(self) -> None:
        self._mic_in_speech = False
        self._reset_streaming_state(MIC_SOURCE)
        if self._recorder:
            self._recorder.stop()
            self._recorder = None
        logger.info("Microphone capture stopped (active=%s)", self._active_mic_input_device_name)
        self._active_mic_input_device_name = None

    def _on_microphone_capture_start_failed(self, message: str) -> None:
        logger.warning("Microphone capture start/restart failed: %s", message)
        self._set_bottom(message)

    def _restart_microphone_capture(self, reason: str) -> None:
        if self._destroying or not self._running or self._mic_recovery_in_progress:
            return
        logger.warning("Restarting microphone capture (reason=%s)", reason)
        self._mic_recovery_in_progress = True
        try:
            self._stop_microphone_capture()
            self._start_microphone_capture()
            current_name = self._active_mic_input_device_name or self._copy("mic_device_none")
            if "configured microphone unavailable" in reason:
                self._set_bottom(self._copy("mic_missing_fallback_notice", name=current_name))
            else:
                self._set_bottom(self._copy("mic_auto_switch_notice", name=current_name))
            self._refresh_runtime_status()
        except Exception as exc:
            failure = str(exc).strip() or self._copy("mic_runtime_stopped")
            self._on_microphone_capture_start_failed(failure)
        finally:
            self._mic_recovery_in_progress = False

    def _schedule_mic_audio_watch(self, delay_ms: int = 2500) -> None:
        if self._destroying:
            return
        if self._mic_audio_watch_after_id is not None:
            try:
                self.after_cancel(self._mic_audio_watch_after_id)
            except Exception:
                pass
        self._mic_audio_watch_after_id = self.after(delay_ms, self._poll_mic_audio_watch)

    def _poll_mic_audio_watch(self) -> None:
        self._mic_audio_watch_after_id = None
        if self._destroying:
            return

        try:
            previous = self._last_mic_device_signature
            signature = self._microphone_device_signature()
            self._last_mic_device_signature = signature
            _, default_name, mode, configured_name, resolved_name = signature
            if resolved_name != self._device_var.get():
                self._set_selected_device(resolved_name, persist=False, mode=mode)

            if not self._running:
                return

            recorder = self._recorder
            if recorder is None and not self._mic_recovery_in_progress:
                self._restart_microphone_capture("microphone recorder missing while running")
                return
            if recorder is not None and not recorder.is_running:
                self._restart_microphone_capture("microphone recorder stopped unexpectedly")
                return

            active_norm = self._normalize_audio_device_name(self._active_mic_input_device_name or "")
            resolved_norm = self._normalize_audio_device_name(resolved_name or "")
            if previous is None:
                return

            previous_default = previous[1]
            previous_resolved = previous[4]
            if mode == "auto":
                if resolved_norm and resolved_norm != active_norm and (
                    previous_default != default_name
                    or self._normalize_audio_device_name(previous_resolved or "") != resolved_norm
                ):
                    logger.info(
                        "Detected default microphone change (previous_default=%s current_default=%s active=%s resolved=%s)",
                        previous_default,
                        default_name,
                        self._active_mic_input_device_name,
                        resolved_name,
                    )
                    self._restart_microphone_capture("system default microphone changed")
                    return
            else:
                configured_exists = bool(configured_name and configured_name in self._devices)
                if not configured_exists and resolved_norm and resolved_norm != active_norm:
                    logger.warning(
                        "Configured microphone is unavailable, falling back (configured=%s fallback=%s)",
                        configured_name,
                        resolved_name,
                    )
                    self._restart_microphone_capture("configured microphone unavailable")
                    return
        except Exception:
            logger.exception("Microphone device watch failed")
        finally:
            if not self._destroying:
                self._schedule_mic_audio_watch()

    def _toggle_listening(self):
        if self._running:
            self._stop()
            return

        if self._model_prepare_running:
            self._set_bottom(self._t("model_download_wait"))
            return

        self._start()

    def _start(self):
        self._set_status(self._t("starting"), ACCENT)
        self._start_btn.configure(state="disabled", text=self._t("starting"))
        logger.info("Start requested")
        self._listen_session += 1
        self._reset_streaming_state()
        self._mic_in_speech = False
        self._desktop_in_speech = False
        self._last_mic_activity_at = 0.0
        self._last_mic_result_at = 0.0
        self._recent_mic_texts.clear()
        self._recent_listen_texts.clear()
        self._last_listen_result_at = 0.0
        self._last_listen_diagnostic_log_at = 0.0
        with self._translation_state_lock:
            self._active_translation_jobs = 0
        self._clear_avatar_error()
        for work_queue in (
            *self._partial_task_queues.values(),
            *self._final_task_queues.values(),
        ):
            self._drain_queue(work_queue)
        threading.Thread(target=self._init_and_run, daemon=True).start()

    def _init_and_run(self):
        try:
            if self._listening_requires_translation():
                logger.info("Initializing translator for listening")
                translator_started_at = time.monotonic()
                try:
                    self._translator = create_translator(self._config)
                except ValueError:
                    raise RuntimeError(self._t("listen_requires_api"))
                logger.info(
                    "Translator initialized in %.0f ms",
                    (time.monotonic() - translator_started_at) * 1000.0,
                )
            else:
                self._translator = None
            mic_asr_started_at = time.monotonic()
            self._asr.load(
                progress_callback=lambda event: self._call_in_ui(
                    lambda e=event: self._handle_model_progress(e)
                )
            )
            logger.info(
                "Microphone ASR ready in %.0f ms",
                (time.monotonic() - mic_asr_started_at) * 1000.0,
            )
            if self._listen_feature_configured():
                listen_asr_started_at = time.monotonic()
                self._listen_asr.load()
                logger.info(
                    "Desktop listen ASR ready in %.0f ms",
                    (time.monotonic() - listen_asr_started_at) * 1000.0,
                )
            self._sender = self._create_sender()
            self._start_microphone_capture()
            if self._listen_feature_configured():
                try:
                    self._start_listen()
                except Exception as exc:
                    self._call_in_ui(
                        lambda m=str(exc): self._on_desktop_capture_start_failed(m)
                    )
            self._running = True
            self._call_in_ui(self._on_started)
        except Exception as e:
            if self._listen_recorder:
                try:
                    self._listen_recorder.stop()
                except Exception:
                    pass
                self._listen_recorder = None
                self._listen_running = False
            if self._recorder:
                try:
                    self._recorder.stop()
                except Exception:
                    pass
                self._recorder = None
            if self._sender:
                try:
                    self._sender.close()
                except Exception:
                    pass
                self._sender = None
            self._running = False
            msg = str(e)
            logger.exception("Failed to initialize listening pipeline")
            self._call_in_ui(lambda: self._on_start_error(msg))

    def _stop(self):
        self._running = False
        logger.info("Stop requested")
        self._reset_streaming_state()
        self._mic_in_speech = False
        self._desktop_in_speech = False
        self._last_mic_activity_at = 0.0
        self._last_mic_result_at = 0.0
        self._recent_mic_texts.clear()
        self._recent_listen_texts.clear()
        self._last_listen_result_at = 0.0
        self._last_listen_diagnostic_log_at = 0.0
        with self._translation_state_lock:
            self._active_translation_jobs = 0
        for work_queue in (
            *self._partial_task_queues.values(),
            *self._final_task_queues.values(),
        ):
            self._drain_queue(work_queue)
        if self._listen_recorder:
            self._listen_recorder.stop()
            self._listen_recorder = None
        self._listen_running = False
        self._stop_microphone_capture()
        self._sync_all_avatar_params(force=True)
        if self._sender:
            self._sender.close()
            self._sender = None
        self._translator = None
        self._set_status(self._t("status_stopped"), DANGER)
        self._refresh_start_button()
        if not self._model_prepare_running:
            self._hide_bottom_progress()
            self._set_bottom(self._t("model_ready"))

    def _on_started(self):
        logger.info(
            "Listening started successfully (mic=%s desktop_enabled=%s desktop_running=%s)",
            self._active_mic_input_device_name,
            self._desktop_capture_enabled,
            self._listen_running,
        )
        self._refresh_runtime_status()
        self._sync_all_avatar_params(force=True)
        self._refresh_start_button()
        self._hide_bottom_progress()
        self._set_bottom(self._t("model_ready"))

    def _on_start_error(self, msg: str):
        logger.error("Listening failed to start: %s", msg)
        self._set_status(self._t("status_error"), DANGER)
        self._refresh_start_button()
        self._hide_bottom_progress()
        messagebox.showerror(self._t("listen_start_failed_title"), msg)

    def _on_vad_state(self, in_speech: bool):
        self._on_source_vad_state(MIC_SOURCE, in_speech)

    def _on_audio_segment(self, audio, source: str = MIC_SOURCE):
        if not self._running:
            return
        self._reset_streaming_state(source)
        asr_lang = self._listen_asr_language() if source == DESKTOP_SOURCE else self._current_asr_lang
        selected_src_lang = self._listen_source_language() if source == DESKTOP_SOURCE else self._current_src_lang
        queue_result = self._enqueue_latest(
            self._final_task_queue_for_source(source),
            (audio, asr_lang, selected_src_lang, self._listen_session, source),
        )
        if queue_result != "enqueued":
            logger.debug(
                "Final queue update result=%s source=%s session=%s",
                queue_result,
                source,
                self._listen_session,
            )

    def _open_settings(self):
        if self._settings_window is not None:
            try:
                if self._settings_window.winfo_exists():
                    self._sync_settings_window_vrc_listen_state()
                    self._settings_window.deiconify()
                    self._settings_window.lift()
                    return
            except Exception:
                self._settings_window = None
        self._settings_window = SettingsWindow(
            self,
            self._config,
            on_save=self._on_config_saved,
            on_close=self._on_settings_window_closed,
        )
        self._sync_settings_window_vrc_listen_state()

    def _check_for_update(self) -> None:
        if getattr(self, "_pending_update", None):
            self.after(self._update_recheck_ms, self._check_for_update)
            return

        def _on_update_available(version: str, url: str, notes: str) -> None:
            self._pending_update = (version, url, notes)
            self.after(0, self._show_update_badge)

        check_for_update(_on_update_available)
        self.after(self._update_recheck_ms, self._check_for_update)

    def _show_update_badge(self) -> None:
        badge = getattr(self, "_update_badge_btn", None)
        if badge is None:
            return
        try:
            badge.configure(text=self._copy("update_badge"))
            badge.grid(row=0, column=2, sticky="w", padx=(8, 0))
        except Exception:
            pass

    def _open_update_window(self) -> None:
        pending = getattr(self, "_pending_update", None)
        if not pending:
            return
        version, url, notes = pending
        win = getattr(self, "_update_win", None)
        if win is not None:
            try:
                if win.winfo_exists():
                    win.deiconify()
                    win.lift()
                    return
            except Exception:
                pass
        self._update_win = UpdateWindow(self, version, url, notes, self._ui_lang)
        present_popup(self._update_win, parent=self)

    def _current_device_name(self) -> str | None:
        if hasattr(self, "_device_var"):
            return self._device_var.get()
        return None

    def _rebuild_ui(self, device_name: str | None = None):
        source_text = self._src_text
        target_text = self._last_tgt_text

        if self._floating_window is not None:
            try:
                self._floating_window.destroy()
            except Exception:
                pass
            self._floating_window = None

        for child in list(self.winfo_children()):
            child.destroy()

        self._char_label = None
        self._src_input = None
        self._tgt_output = None
        self._bottom_bar = None
        self._bottom_progress = None
        self._close_device_picker()
        self._social_icons.clear()
        self._src_placeholder = self._t("source_placeholder")
        self._src_rendered_text = ""
        self._src_rendered_color = TEXT_SEC
        self._src_rendered_count = 0
        self._tgt_rendered_text = ""
        self._build()
        self._load_devices()

        if device_name and device_name in getattr(self, "_devices", {}):
            self._set_selected_device(device_name, persist=False)

        self._set_source_text(source_text)
        if target_text:
            self._show_tgt(target_text)
        else:
            self._last_tgt_text = ""
            self._tgt_output.configure(state="normal")
            self._tgt_output.delete("1.0", "end")
            self._tgt_output.configure(state="disabled")
            self._tgt_rendered_text = ""

    def _maybe_show_osc_guide(self):
        ui_cfg = self._config.setdefault("ui", {})
        if ui_cfg.get("osc_guide_seen"):
            return
        ui_cfg["osc_guide_seen"] = True
        self._schedule_config_save()
        self._open_osc_guide()

    def _guide_pages(self) -> list[dict[str, object]]:
        return [
            {
                "title": self._t("guide_step_1_title"),
                "body": self._t("guide_step_1_body"),
                "path": ["Action Menu", "Options"],
            },
            {
                "title": self._t("guide_step_2_title"),
                "body": self._t("guide_step_2_body"),
                "path": ["Options", "OSC"],
            },
            {
                "title": self._t("guide_step_3_title"),
                "body": self._t("guide_step_3_body"),
                "path": ["OSC", "Enabled"],
            },
        ]

    def _open_osc_guide(self):
        pages = self._guide_pages()
        if not pages:
            return

        if getattr(self, "_guide_win", None) and self._guide_win.winfo_exists():
            self._guide_win.deiconify()
            self._guide_win.lift()
            self._render_guide_page()
            return

        self._guide_page_index = 0
        self._guide_win = ctk.CTkToplevel(self)
        self._guide_win.title(self._t("guide_title"))
        apply_window_icon(self._guide_win)
        self._guide_win.geometry("520x430")
        self._guide_win._popup_size = (520, 430)
        self._guide_win.resizable(False, False)
        self._guide_win.attributes("-topmost", True)
        self._guide_win.transient(self)
        self._guide_win.grab_set()
        self._guide_win.configure(fg_color=BG_PRIMARY)

        outer = ctk.CTkFrame(self._guide_win, fg_color=BG_PRIMARY)
        outer.pack(fill="both", expand=True, padx=18, pady=18)

        self._guide_title_label = ctk.CTkLabel(
            outer,
            text=self._t("guide_title"),
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        self._guide_title_label.pack(anchor="w", pady=(0, 4))

        self._guide_subtitle_label = ctk.CTkLabel(
            outer,
            text=self._t("guide_subtitle"),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=470,
            font=ctk.CTkFont(size=12),
        )
        self._guide_subtitle_label.pack(anchor="w", pady=(0, 14))

        card = ctk.CTkFrame(
            outer,
            fg_color=BG_PANEL,
            corner_radius=24,
            border_width=1,
            border_color=GLASS_BORDER,
        )
        card.pack(fill="both", expand=True)

        self._guide_page_label = ctk.CTkLabel(
            card,
            text="",
            text_color=ACCENT,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self._guide_page_label.pack(anchor="w", padx=22, pady=(18, 10))

        self._guide_step_title_label = ctk.CTkLabel(
            card,
            text="",
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        self._guide_step_title_label.pack(anchor="w", padx=22)

        self._guide_step_body_label = ctk.CTkLabel(
            card,
            text="",
            text_color=TEXT_SEC,
            justify="left",
            wraplength=430,
            font=ctk.CTkFont(size=13),
        )
        self._guide_step_body_label.pack(anchor="w", padx=22, pady=(10, 18))

        self._guide_path_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._guide_path_frame.pack(fill="x", padx=20)

        self._guide_footer_label = ctk.CTkLabel(
            card,
            text=self._t("guide_footer"),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=430,
            font=ctk.CTkFont(size=12),
        )
        self._guide_footer_label.pack(anchor="w", padx=22, pady=(18, 22))

        nav = ctk.CTkFrame(outer, fg_color="transparent")
        nav.pack(fill="x", pady=(14, 0))

        self._guide_prev_btn = ctk.CTkButton(
            nav,
            text=self._t("guide_prev"),
            width=90,
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=10,
            text_color=TEXT_PRI,
            command=self._guide_prev,
        )
        self._guide_prev_btn.pack(side="left")

        self._guide_next_btn = ctk.CTkButton(
            nav,
            text=self._t("guide_next"),
            width=90,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            corner_radius=10,
            text_color="#ffffff",
            command=self._guide_next,
        )
        self._guide_next_btn.pack(side="right")

        self._render_guide_page()
        present_popup(self._guide_win, parent=self)

    def _render_guide_page(self):
        pages = self._guide_pages()
        total = len(pages)
        index = max(0, min(getattr(self, "_guide_page_index", 0), total - 1))
        page = pages[index]

        self._guide_page_label.configure(
            text=self._t("guide_page", current=index + 1, total=total)
        )
        self._guide_step_title_label.configure(text=str(page["title"]))
        self._guide_step_body_label.configure(text=str(page["body"]))

        for child in self._guide_path_frame.winfo_children():
            child.destroy()
        for i, item in enumerate(page["path"]):
            ctk.CTkLabel(
                self._guide_path_frame,
                text=str(item),
                fg_color=ACCENT if i == len(page["path"]) - 1 else "#eef5ff",
                text_color="#ffffff" if i == len(page["path"]) - 1 else TEXT_PRI,
                corner_radius=16,
                padx=12,
                pady=6,
                font=ctk.CTkFont(size=12, weight="bold"),
            ).pack(side="left", padx=(2, 8), pady=(0, 4))

        self._guide_prev_btn.configure(state="normal" if index > 0 else "disabled")
        self._guide_next_btn.configure(
            text=self._t("guide_done") if index == total - 1 else self._t("guide_next")
        )

    def _guide_prev(self):
        self._guide_page_index = max(0, getattr(self, "_guide_page_index", 0) - 1)
        self._render_guide_page()

    def _guide_next(self):
        total = len(self._guide_pages())
        if getattr(self, "_guide_page_index", 0) >= total - 1:
            if self._guide_win and self._guide_win.winfo_exists():
                self._guide_win.destroy()
            return
        self._guide_page_index += 1
        self._render_guide_page()

    def _on_config_saved(self, new_cfg: dict):
        was_running = self._running
        device_name = self._current_device_name()
        if was_running:
            self._set_bottom(self._t("settings_saved_reloading"))
            self._set_status(self._t("status_restarting"), ACCENT)
            self._stop()

        self._config = new_cfg
        self._ui_lang = get_ui_language(new_cfg)
        self.title(self._t("window_title"))
        self._asr = create_asr(new_cfg)
        self._listen_asr = create_asr(new_cfg)
        self._translator = None
        self._desktop_capture_enabled = bool(
            self._vrc_listen_config().get("enabled", False)
        )
        self._listen_overlay_enabled = bool(
            self._vrc_listen_config().get("show_overlay", False)
        )
        self._mic_in_speech = False
        self._desktop_in_speech = False
        self._listen_running = False
        self._listen_transitioning = False
        self._listen_toggle_cooldown_until = 0.0
        self._active_mic_input_device_name = None
        self._last_mic_device_signature = None
        self._last_listen_result_at = 0.0
        self._last_listen_diagnostic_log_at = 0.0
        with self._translation_state_lock:
            self._active_translation_jobs = 0
        with self._merge_lock:
            self._partial_merger = self._create_streaming_merger()
            self._desktop_partial_merger = self._create_streaming_merger()
        self._reset_streaming_state()

        self._rebuild_ui(device_name=device_name)
        if self._listen_overlay_enabled:
            self._set_listen_overlay_enabled(True, persist=False)
        self._sync_all_avatar_params(force=True)
        if was_running:
            self.after(100, self._start)
        else:
            self._set_bottom(
                self._t("model_ready")
                if model_exists(self._asr_model_spec())
                else self._t("model_unloaded")
            )

    def _set_status(self, text: str, color: str = "white"):
        if text == self._status_text and color == self._status_color:
            return
        self._status_text = text
        self._status_color = color
        if hasattr(self, "_status_label"):
            self._status_label.configure(text=text, text_color=color)

    def _set_bottom(self, text: str):
        if text == self._bottom_text:
            return
        self._bottom_text = text
        if hasattr(self, "_bottom_bar"):
            self._bottom_bar.configure(text=text)

    def _sync_target_language_to_config(self, target_code: str) -> None:
        translation_cfg = self._config.setdefault("translation", {})
        if translation_cfg.get("target_language") == target_code:
            return
        translation_cfg["target_language"] = target_code
        self._schedule_config_save()

    def destroy(self):
        if self._destroying:
            return
        self._destroying = True
        logger.info("Application shutdown requested")

        if self._config_save_after_id is not None:
            try:
                self.after_cancel(self._config_save_after_id)
            except Exception:
                pass
            self._config_save_after_id = None
        if self._desktop_audio_watch_after_id is not None:
            try:
                self.after_cancel(self._desktop_audio_watch_after_id)
            except Exception:
                pass
            self._desktop_audio_watch_after_id = None
        if self._mic_audio_watch_after_id is not None:
            try:
                self.after_cancel(self._mic_audio_watch_after_id)
            except Exception:
                pass
            self._mic_audio_watch_after_id = None

        try:
            config_manager.save_config(self._config)
        except Exception:
            pass

        try:
            self._stop()
        except Exception:
            pass

        try:
            self._reset_avatar_params()
        except Exception:
            pass
        if self._sender is not None:
            try:
                self._sender.close()
            except Exception:
                pass
            self._sender = None
        if self._floating_window is not None:
            try:
                self._floating_window.destroy()
            except Exception:
                pass
            self._floating_window = None
        if self._settings_window is not None:
            try:
                self._settings_window.destroy()
            except Exception:
                pass
            self._settings_window = None

        for work_queue in (
            *self._partial_task_queues.values(),
            *self._final_task_queues.values(),
        ):
            self._drain_queue(work_queue)
            try:
                work_queue.put_nowait(None)
            except queue.Full:
                pass

        super().destroy()
        logger.info("Application shutdown complete")
