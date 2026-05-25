import queue
import threading

from src.ui.main_window import MainWindow, UI_CALLBACK_DRAIN_MS


def _window_for_ui_callbacks():
    window = object.__new__(MainWindow)
    window._destroying = False
    window._ui_thread_id = threading.get_ident()
    window._ui_callback_queue = queue.Queue()
    window._ui_callback_drain_after_id = None
    return window


def test_call_in_ui_from_ui_thread_uses_tk_after():
    window = _window_for_ui_callbacks()
    scheduled: list[tuple[int, object]] = []

    def fake_after(delay_ms, callback):
        scheduled.append((delay_ms, callback))
        return "after-id"

    window.after = fake_after

    called: list[str] = []
    assert window._call_in_ui(lambda: called.append("ran"), delay_ms=12) is True

    assert window._ui_callback_queue.empty()
    assert scheduled[0][0] == 12
    scheduled[0][1]()
    assert called == ["ran"]


def test_call_in_ui_from_worker_thread_only_queues_callback():
    window = _window_for_ui_callbacks()
    after_called = threading.Event()
    result: list[bool] = []

    def fake_after(_delay_ms, _callback):
        after_called.set()
        return "after-id"

    window.after = fake_after

    worker = threading.Thread(
        target=lambda: result.append(window._call_in_ui(lambda: None, delay_ms=7))
    )
    worker.start()
    worker.join(timeout=1.0)

    assert result == [True]
    assert not after_called.is_set()
    assert window._ui_callback_queue.get_nowait()[0] == 7


def test_drain_ui_callback_queue_runs_on_ui_thread_and_reschedules():
    window = _window_for_ui_callbacks()
    scheduled: list[tuple[int, object]] = []

    def fake_after(delay_ms, callback):
        scheduled.append((delay_ms, callback))
        return f"after-{delay_ms}"

    window.after = fake_after
    called: list[str] = []
    window._ui_callback_queue.put_nowait((0, lambda: called.append("now")))
    window._ui_callback_queue.put_nowait((15, lambda: called.append("later")))

    window._drain_ui_callback_queue()

    assert called == ["now"]
    assert [delay for delay, _ in scheduled] == [15, UI_CALLBACK_DRAIN_MS]
    assert window._ui_callback_drain_after_id == f"after-{UI_CALLBACK_DRAIN_MS}"


def test_settings_listen_state_change_updates_chatbox_toggle():
    window = _window_for_ui_callbacks()
    window._desktop_capture_enabled = False
    window._listen_overlay_enabled = False
    window._config = {
        "vrc_listen": {
            "enabled": False,
            "show_overlay": False,
            "send_to_chatbox": False,
        }
    }
    synced: list[bool] = []
    saved: list[bool] = []

    window._desktop_capture_config = lambda: window._config.setdefault("vrc_listen", {})
    window._sync_settings_window_vrc_listen_state = (
        lambda: synced.append(bool(window._config["vrc_listen"].get("send_to_chatbox", False)))
    )
    window._save_config_now = lambda: saved.append(True)
    window._listen_send_to_chatbox_enabled = (
        lambda: bool(window._config["vrc_listen"].get("send_to_chatbox", True))
    )

    MainWindow._on_settings_listen_state_changed(window, None, None, True)

    assert window._config["vrc_listen"]["send_to_chatbox"] is True
    assert saved == [True]
    assert synced[-1] is True
