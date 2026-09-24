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


def test_a_larger_text_size_makes_a_taller_board(qapp):
    from src.ui_qt.vr_overlay_panel import MAX_FONT_SCALE

    small = VROverlayPanel()
    large = VROverlayPanel(font_scale=1.6)
    for panel in (small, large):
        panel.add_message(translated="你好，今天过得怎么样？" * 3, original="Hello, how are you today?" * 2)

    assert large.height() > small.height()
    assert large.set_font_scale(99) and large.font_scale == MAX_FONT_SCALE
    assert not large.set_font_scale(99)


def test_each_side_carries_its_own_shape_not_just_a_colour(qapp):
    from src.ui_qt.vr_overlay_panel import OTHER_MARK, SELF_MARK

    panel = VROverlayPanel()
    panel.add_message(translated="theirs", original="原文", source="listen")
    panel.add_message(translated="mine", original="我的", source="mic")

    texts = [translation.text() for translation, _original, _ in panel._rows]
    assert texts == [f"{OTHER_MARK} theirs", f"{SELF_MARK} mine"]

    assert panel.set_speaker_marks(False)
    assert [translation.text() for translation, _o, _ in panel._rows] == ["theirs", "mine"]


def test_board_text_settings_are_normalised():
    from src.utils.config_manager import _ensure_vrc_listen_config

    config = {"vrc_listen": {"vr_overlay": {"font_scale": 9, "speaker_marks": "yes"}}}
    _ensure_vrc_listen_config(config)

    board = config["vrc_listen"]["vr_overlay"]
    assert board["font_scale"] == 1.0
    assert board["speaker_marks"] is True
