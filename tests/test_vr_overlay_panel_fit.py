"""The headset board always shows its newest line.

Rows that outgrow the tallest panel allowed used to be clipped at the
bottom, which is where the newest line sits, so a board full of long
sentences looked stuck on old ones. The oldest lines go instead.
"""

from __future__ import annotations

from src.ui_qt.vr_overlay_panel import MAX_PANEL_HEIGHT, MAX_VR_LINES, VROverlayPanel


def test_long_lines_push_out_the_oldest_rather_than_hide_the_newest(qapp):
    panel = VROverlayPanel()
    long_line = "这是一句非常长的话，" * 30

    for i in range(MAX_VR_LINES):
        panel.add_message(translated=f"{i} {long_line}", original=f"{i} original {long_line}", source="listen")

    assert panel.height() <= MAX_PANEL_HEIGHT
    assert len(panel._entries) < MAX_VR_LINES
    assert panel._entries[-1][0].startswith(f"{MAX_VR_LINES - 1} ")
    last_translation = panel._rows[-1][0]
    assert last_translation.geometry().bottom() <= panel.height()


def test_short_lines_keep_the_full_history(qapp):
    panel = VROverlayPanel()

    for i in range(MAX_VR_LINES):
        panel.add_message(translated=f"line {i}", original=f"original {i}", source="listen")

    assert len(panel._entries) == MAX_VR_LINES
    assert panel.height() <= MAX_PANEL_HEIGHT
