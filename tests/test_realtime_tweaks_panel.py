from PySide6.QtCore import Qt

from src.ui_qt.realtime_tweaks_panel import RealtimeTweaksPanel
from src.ui_qt.state_manager import AppState


def test_realtime_tweaks_panel_is_not_topmost_and_uses_icon_assets(qtbot):
    state = AppState()
    panel = RealtimeTweaksPanel(None, state, "zh-CN", "light")
    qtbot.addWidget(panel)

    flags = panel.windowFlags()
    assert not bool(flags & Qt.WindowType.WindowStaysOnTopHint)
    assert bool(flags & Qt.WindowType.FramelessWindowHint)
    assert not panel._close_btn.icon().isNull()
    assert "slider-thumb.svg" in panel.styleSheet()
    assert panel._icon_labels
    assert all(not label.pixmap().isNull() for label, _filename, _strong in panel._icon_labels)


def test_realtime_tweaks_manual_close_toggle_updates_state(qtbot):
    state = AppState()
    panel = RealtimeTweaksPanel(None, state, "zh-CN", "dark")
    qtbot.addWidget(panel)

    assert panel.manual_close_toggle.isChecked()
    assert state.get("tweaks_manual_close") is True

    panel.manual_close_toggle.setChecked(False)

    assert panel._manual_close_only is False
    assert state.get("tweaks_manual_close") is False
