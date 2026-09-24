"""VRHandsFrame's second tier: sign colours, reading options, QR codes, panel
looks and two-hand operations, shortcuts, the stick, avatar state, custom
cue sounds."""

from __future__ import annotations

import math
import threading
import types

import numpy as np
import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from src.core.in_place_layout import HeadAnchor, PlacedLine
from src.core.qr_codes import decode_codes, is_link
from src.core.screenshot_translation import ScreenshotTranslator
from src.core.text_blocks import TextBlock, merge_blocks
from src.core.vr_feedback import VRFeedback
from src.core.vr_frame_gesture import MAX_STICK_SCALE, FrameGestureTracker, FrameInputs
from src.core.vr_history import ReadHistory
from src.core.vr_input import TapCounter
from src.ui_qt import main_window as main_window_module
from src.ui_qt.in_place_painter import contrast_ratio, estimate_colors, picture_array, render_translation_card
from src.ui_qt.main_window import MainWindow
from src.ui_qt.vr_result_panel import PANEL_SIZE, VRResultPanelView, panel_style
from tests.test_vr_result_panels import HEAD, _identity, _manager, _open, _pointing


# ------------------------------------------------------------------ colours
def _sign(background=(20, 90, 40), ink=(250, 230, 120)):
    image = QImage(400, 120, QImage.Format.Format_RGBA8888)
    image.fill(QColor(*background))
    painter = QPainter(image)
    painter.setPen(QColor(*ink))
    font = QFont()
    font.setPixelSize(40)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QRect(20, 30, 360, 60), 0, "EXIT HERE")
    painter.end()
    return image


class TestColours:
    def test_background_and_ink_are_told_apart(self, qapp):
        array = picture_array(_sign())

        background, ink = estimate_colors(array[30:90, 20:380])

        assert background == (20, 90, 40)
        assert np.allclose(ink, (250, 230, 120), atol=30)

    def test_ink_too_close_to_the_background_becomes_readable(self):
        pixels = np.array([[100, 100, 100]] * 90 + [[110, 110, 110]] * 10, dtype=np.uint8)

        background, ink = estimate_colors(pixels)

        assert ink in {(255, 255, 255), (0, 0, 0)}
        assert contrast_ratio(background, ink) >= 3.0

    def test_the_card_paints_plates_in_the_sign_colours(self, qapp):
        picture = _sign()
        line = PlacedLine("EXIT HERE", "OUT", 20, 30, 360, 60)

        matched = render_translation_card(picture, [line], (0, 0), match_colors=True)
        plain = render_translation_card(picture, [line], (0, 0), match_colors=False)

        scale = matched.width() / picture.width()
        x, y = int(30 * scale), int(36 * scale)
        green = matched.pixelColor(x, y)
        black = plain.pixelColor(x, y)
        assert green.green() > green.red() and green.green() > 60
        assert black.green() < 40


# ------------------------------------------------------------------ reading options and codes
class _Line:
    def __init__(self, text, left, top, width=200, height=30):
        self.text, self.left, self.top, self.width, self.height = text, left, top, width, height
        self.angle = 0.0


class _Recognized:
    def __init__(self, lines):
        self.lines = lines
        self.error = ""


def _capture():
    return types.SimpleNamespace(png=b"png", width=800, height=600, source="vr_eye", frame_size=(800, 600))


def _run(translator, region):
    done = threading.Event()
    results: list = []
    translator._on_result = lambda result: (results.append(result), done.set())
    assert translator.trigger(region)
    assert done.wait(5)
    return results[0]


class TestReadingOptions:
    def test_merged_blocks_read_as_one_paragraph(self):
        blocks = [
            TextBlock(lines=(_Line("Hello", 10, 10),), left=10, top=10, width=200, height=30),
            TextBlock(lines=(_Line("world", 10, 200),), left=10, top=200, width=150, height=30),
        ]

        merged = merge_blocks(blocks)

        assert merged.text == "Hello world"
        assert (merged.left, merged.top, merged.width, merged.height) == (10, 10, 200, 220)

    def test_ignore_line_breaks_sends_one_text(self):
        seen: list = []
        translator = ScreenshotTranslator(
            capture=_capture,
            recognize=lambda png, lang: _Recognized([_Line("ONE", 10, 10), _Line("TWO", 10, 300)]),
            translate=lambda text: seen.append(text) or text.lower(),
            ocr_language=lambda: "en",
            merge_blocks=lambda: True,
        )

        result = _run(translator, (0.0, 0.0, 1.0, 1.0))

        assert seen == ["ONE TWO"]
        assert result.pairs == [("ONE TWO", "one two")]

    def test_codes_come_back_with_the_text(self):
        translator = ScreenshotTranslator(
            capture=_capture,
            recognize=lambda png, lang: _Recognized([_Line("EXIT", 10, 10)]),
            translate=lambda text: "出口",
            ocr_language=lambda: "en",
            decode_codes=lambda png: ["https://example.com/world"],
        )

        result = _run(translator, None)

        assert result.codes == ["https://example.com/world"]
        assert result.pairs == [("EXIT", "出口"), ("https://example.com/world", "https://example.com/world")]

    def test_a_code_alone_is_a_result_not_no_text(self):
        translator = ScreenshotTranslator(
            capture=_capture,
            recognize=lambda png, lang: _Recognized([]),
            translate=lambda text: text,
            ocr_language=lambda: "en",
            decode_codes=lambda png: ["hello"],
        )

        result = _run(translator, None)

        assert result.error == ""
        assert result.pairs == [("hello", "hello")]

    def test_a_real_qr_code_is_decoded(self):
        cv2 = pytest.importorskip("cv2")
        encoder = cv2.QRCodeEncoder.create()
        code = encoder.encode("https://miovrc.com/")
        code = cv2.resize(code, (code.shape[1] * 8, code.shape[0] * 8), interpolation=cv2.INTER_NEAREST)
        code = cv2.copyMakeBorder(code, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
        ok, png = cv2.imencode(".png", code)
        assert ok

        assert decode_codes(png.tobytes()) == ["https://miovrc.com/"]
        assert decode_codes(b"not a picture") == []

    def test_only_web_addresses_count_as_links(self):
        assert is_link("https://miovrc.com/x")
        assert is_link("http://a.b")
        assert not is_link("javascript:alert(1)")
        assert not is_link("file:///C:/x")
        assert not is_link("hello")


# ------------------------------------------------------------------ panel look
TEXTS = {"close": "Close", "pin": "Keep", "copy": "Copy", "chatbox": "Chat", "open_link": "Link",
         "show_original": "Orig", "show_translation": "Tr", "tip_close": "Close this panel"}


class TestPanelLook:
    def test_presets_and_opacity(self, qapp):
        view = VRResultPanelView(TEXTS, panel_style("light", opacity=0.5))
        view.set_result(title="t", card=None, picture=None, pairs=[("a", "b")])

        pixel = view.render_image().pixelColor(PANEL_SIZE[0] // 2, 8)

        assert pixel.red() > 200 and pixel.alpha() < 140
        assert panel_style("nonsense").background == panel_style("default").background
        assert panel_style("dark", opacity=5).opacity == 1.0

    def test_a_tooltip_replaces_the_title_while_hovering(self, qapp):
        view = VRResultPanelView(TEXTS, panel_style(tooltips=True))
        view.set_result(title="12:00", card=None, picture=None, pairs=[("a", "b")])
        quiet = view.render_image()
        hovering = view.render_image(hover="close")
        view.set_style(panel_style(tooltips=False))
        no_tips = view.render_image(hover="close")

        title_area = QRect(30, 30, 300, 70)
        assert quiet.copy(title_area) != hovering.copy(title_area)
        assert quiet.copy(title_area) == no_tips.copy(title_area)

    def test_a_read_with_a_link_has_a_link_button(self, qapp):
        view = VRResultPanelView(TEXTS)
        view.set_result(title="t", card=None, picture=None, pairs=[("x", "x")], links=["https://a.b"])
        view.render_image()

        assert "open_link" in {button.action for button in view._buttons}
        assert not view.apply("open_link")


# ------------------------------------------------------------------ two hands on a panel
def _held(manager, overlays, *, left=(-0.1, 1.4, -0.3), right=(0.1, 1.4, -0.3), left_trigger=False, right_trigger=False):
    return {
        "left": _pointing(grip=True, trigger=left_trigger, pose=_identity(*left)),
        "right": _pointing(grip=True, trigger=right_trigger, pose=_identity(*right)),
    }


class TestTwoHands:
    def _grab_with_both(self):
        manager, overlays, lasers, views, events, clock = _manager()
        _open(manager, entry_id=1)
        overlays[0].hit = (0.5, 0.5, 0.5)
        free = {"left": _pointing(pose=_identity(-0.1, 1.4, -0.3)), "right": _pointing(pose=_identity(0.1, 1.4, -0.3))}
        manager.poll(free, head=HEAD)
        manager.poll({"left": _pointing(grip=True, pose=_identity(-0.1, 1.4, -0.3)), "right": free["right"]}, head=HEAD)
        manager.poll(_held(manager, overlays), head=HEAD)
        return manager, overlays, views, events

    def test_pulling_the_hands_apart_resizes_the_panel(self):
        manager, overlays, _views, _events = self._grab_with_both()
        width = overlays[0].placed[-1][1]

        manager.poll(_held(manager, overlays, left=(-0.15, 1.4, -0.3), right=(0.15, 1.4, -0.3)), head=HEAD)

        assert overlays[0].placed[-1][1] == pytest.approx(width * 1.5, rel=0.05)

    def test_a_trigger_with_both_hands_runs_that_hands_shortcut(self):
        manager, overlays, views, events = self._grab_with_both()
        manager.two_hand_actions = {"left": "page_prev", "right": "close"}

        manager.poll(_held(manager, overlays, left_trigger=True), head=HEAD)
        assert views[0].applied == ["page_prev"]

        manager.poll(_held(manager, overlays, right_trigger=True), head=HEAD)
        assert manager.count == 0
        assert ("close", 1) in events

    def test_the_same_hands_trigger_gathers_the_panels(self):
        manager, overlays, _lasers, _views, events, _clock = _manager()
        _open(manager, entry_id=1, rows=[[1, 0, 0, 3.0], [0, 1, 0, 1.0], [0, 0, 1, 3.0]])
        overlays[0].hit = (0.5, 0.5, 0.5)
        hand = _identity(0.0, 1.4, -0.2)
        manager.poll({"right": _pointing(pose=hand)}, head=HEAD)
        manager.poll({"right": _pointing(grip=True, pose=hand)}, head=HEAD)

        manager.poll({"right": _pointing(grip=True, trigger=True, pose=hand)}, head=HEAD, head_pose=_identity(0.0, 1.6, 0.0))

        x, _y, z = overlays[0].placed[-1][0][0][3], 0, overlays[0].placed[-1][0][2][3]
        assert z < 0 and abs(x) < 0.3
        assert not manager.grabbing

    def test_the_link_button_goes_to_the_owner(self):
        shared: list = []
        manager, overlays, _lasers, views, _events, _clock = _manager()
        manager._on_share = lambda entry, action, original: shared.append((entry, action))
        _open(manager, entry_id=9)
        views[0].hit_test = lambda x, y: "open_link"
        overlays[0].hit = (0.5, 0.5, 0.5)

        for trigger in (False, True, False):
            manager.poll({"right": _pointing(trigger=trigger)}, head=HEAD)

        assert shared == [(9, "open_link")]


# ------------------------------------------------------------------ input: taps, stick
class TestInputExtras:
    def test_taps_in_a_row_fire_once(self):
        counter = TapCounter(taps=3, window=0.4)
        fired = []
        t = 0.0
        for _ in range(3):
            fired.append(counter.update(True, t))
            fired.append(counter.update(False, t + 0.05))
            t += 0.2
        assert fired.count(True) == 1

    def test_slow_taps_start_over(self):
        counter = TapCounter(taps=2, window=0.3)
        counter.update(True, 0.0)
        counter.update(False, 0.1)

        assert not counter.update(True, 1.0)
        counter.update(False, 1.1)
        assert counter.update(True, 1.2)

    def test_the_stick_scales_an_open_frame(self):
        anchor = HeadAnchor(
            pose=((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0)),
            eye_offset=(0.0, 0.0, 0.0),
            tangents=(-1.0, 1.0, -1.0, 1.0),
        )
        hands = ((-0.1, 0.05, -0.4), (0.1, -0.05, -0.4))

        def tick(now, stick=0.0):
            return tracker.update(
                FrameInputs(anchor=anchor, left=hands[0], right=hands[1], left_grip=True, right_grip=True, now=now, stick=stick)
            )

        tracker = FrameGestureTracker()
        tick(0.0)
        base = tick(0.5).region
        grown = None
        for step in range(1, 11):
            grown = tick(0.5 + step * 0.1, stick=1.0).region
        assert (grown[2] - grown[0]) == pytest.approx((base[2] - base[0]) * 2, rel=0.1)
        for step in range(11, 60):
            tick(0.5 + step * 0.1, stick=1.0)
        assert tracker.scale == pytest.approx(MAX_STICK_SCALE)

        tracker.stick_scaling = False
        before = tracker.scale
        tick(7.0, stick=-1.0)
        assert tracker.scale == before


# ------------------------------------------------------------------ main window
class _Sender:
    def __init__(self):
        self.bools: list = []

    def send_avatar_bool(self, name, value, *, force=False):
        self.bools.append((name, value))
        return True


class TestMainWindowExtras:
    @pytest.fixture
    def window(self, monkeypatch):
        win = MainWindow.__new__(MainWindow)
        win._config = {
            "osc": {"avatar_sync": {"enabled": True}},
            "vrc_listen": {
                "screenshot_translation": {"enabled": True, "frame_gesture": True},
                "vr_controls": {"thumbrest_action": "close_panels"},
            },
        }
        win._frame_tracker = None
        win._vr_result_panels = None
        sender = _Sender()
        monkeypatch.setattr(win, "_ensure_sender", lambda: sender)
        monkeypatch.setattr(win, "_vr_cue", lambda name, hand=None: None)
        monkeypatch.setattr(win, "_refresh_vr_dashboard", lambda: None)
        monkeypatch.setattr(win, "_t", lambda key, **kw: key)
        win._sender_fake = sender
        return win

    def test_avatar_state_is_sent_on_change_only(self, window):
        window._frame_tracker = types.SimpleNamespace(active=True)
        window._vr_result_panels = types.SimpleNamespace(grabbing=False)

        MainWindow._sync_vr_avatar_state(window)
        MainWindow._sync_vr_avatar_state(window)

        assert window._sender_fake.bools == [("MioFraming", True), ("MioPanelHeld", False)]

    def test_shortcuts_run_their_actions(self, window, monkeypatch):
        closed: list = []
        toggled: list = []
        window._vr_result_panels = types.SimpleNamespace(close_all=lambda: closed.append(1) or 1, gather=lambda head: 1)
        monkeypatch.setattr(window, "_toggle_frame_gesture_from_vr", lambda: toggled.append(1))
        vr_input = types.SimpleNamespace(take_shortcuts=lambda: ["thumbrest", "toggle_frame_gesture"])

        MainWindow._poll_vr_shortcuts(window, vr_input, types.SimpleNamespace(head=None))

        assert closed == [1] and toggled == [1]

    def test_the_sticks_are_kept_only_while_framing(self, window):
        assert MainWindow._sticks_kept_from_game(window) == set()
        window._frame_tracker = types.SimpleNamespace(active=True, arming=False)
        assert MainWindow._sticks_kept_from_game(window) == {"left", "right"}
        window._config["vrc_listen"]["screenshot_translation"]["stick_scaling"] = False
        assert MainWindow._sticks_kept_from_game(window) == set()

    def test_recognize_only_skips_the_translator(self, window, monkeypatch):
        window._config["vrc_listen"]["screenshot_translation"]["recognize_only"] = True
        monkeypatch.setattr(window, "_screenshot_translator_client", lambda: pytest.fail("no translation"))

        assert MainWindow._translate_screenshot_line(window, "出口") == "出口"

    def test_codes_are_read_only_when_wanted(self, window, monkeypatch):
        monkeypatch.setattr("src.core.qr_codes.decode_codes", lambda png: ["x"])
        assert MainWindow._decode_screen_codes(window, b"png") == ["x"]
        window._config["vrc_listen"]["screenshot_translation"]["scan_codes"] = False
        assert MainWindow._decode_screen_codes(window, b"png") == []

    def test_a_read_keeps_its_links_and_can_be_copied_at_once(self, window, monkeypatch):
        clipboard = types.SimpleNamespace(text=None)
        clipboard.setText = lambda text: setattr(clipboard, "text", text)
        monkeypatch.setattr(main_window_module.QApplication, "clipboard", staticmethod(lambda: clipboard))
        window._config["vrc_listen"]["screenshot_translation"]["auto_copy"] = True
        result = types.SimpleNamespace(
            pairs=[("EXIT", "出口"), ("https://a.b", "https://a.b")], codes=["https://a.b", "not a link"]
        )

        entry = MainWindow._remember_read(window, result, None)

        assert entry.links == ["https://a.b"]
        assert clipboard.text == "出口\nhttps://a.b"

    def test_open_link_opens_only_web_addresses(self, window, monkeypatch):
        opened: list = []
        monkeypatch.setattr(main_window_module.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString())))
        monkeypatch.setattr(window, "_flash_vr_toast", lambda *a, **k: True)
        history = ReadHistory()
        safe = history.add(card=None, pairs=[("x", "x")], links=["https://a.b/c"])
        unsafe = history.add(card=None, pairs=[("x", "x")], links=["javascript:alert(1)"])
        window._read_history = history

        MainWindow._on_panel_share(window, safe.entry_id, "open_link", False)
        MainWindow._on_panel_share(window, unsafe.entry_id, "open_link", False)

        assert opened == ["https://a.b/c"]


# ------------------------------------------------------------------ custom sounds and config
def test_a_custom_cue_sound_is_played_instead(tmp_path):
    custom = tmp_path / "sounds"
    custom.mkdir()
    (custom / "done.wav").write_bytes(b"RIFF")
    played: list = []
    feedback = VRFeedback(None, sound_dir=tmp_path / "cues", custom_dir=custom, player=played.append)

    feedback.cue("result")
    feedback.cue("frame_ready")

    assert played[0] == custom / "done.wav"
    assert played[1].parent == tmp_path / "cues"


def test_panel_and_shortcut_settings_are_repaired():
    from src.utils.config_manager import _ensure_vrc_listen_config

    config = {
        "vrc_listen": {
            "vr_panels": {"color_preset": "neon", "opacity": 0.1, "two_hand_left": "explode"},
            "vr_controls": {"thumbrest_taps": 20, "thumbrest_action": "dance"},
        }
    }
    _ensure_vrc_listen_config(config)

    panels = config["vrc_listen"]["vr_panels"]
    controls = config["vrc_listen"]["vr_controls"]
    assert panels["color_preset"] == "default"
    assert panels["opacity"] == 1.0
    assert panels["two_hand_left"] == "page_prev"
    assert controls["thumbrest_taps"] == 4
    assert controls["thumbrest_action"] == "frame_gesture"
    shot = config["vrc_listen"]["screenshot_translation"]
    assert shot["match_colors"] is True and shot["scan_codes"] is True and shot["recognize_only"] is False
    assert math.isclose(panels["default_width"], 0.42)
