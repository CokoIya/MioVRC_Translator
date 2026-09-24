"""The compositor frame becomes a Capture with the eye's projection attached."""

from __future__ import annotations

import types


from src.core import vr_eye_capture
from src.core.vr_eye_capture import EyeFrame, capture_eye, eye_frame_size, eye_index, normalize_view_eye


class _Capture:
    def __init__(self, frame):
        self._frame = frame
        self.eyes: list = []

    def capture(self, module, eye="right"):
        self.eyes.append(eye)
        return self._frame


def test_a_frame_becomes_a_capture_with_tangents(monkeypatch, qapp):
    frame = EyeFrame(bytes([10, 20, 30, 255]) * 4, 2, 2, (-1.28, 1.28, -1.28, 1.28), bgra=False)
    monkeypatch.setattr(vr_eye_capture, "shared_eye_capture", lambda: _Capture(frame))

    capture = capture_eye(types.SimpleNamespace())

    assert capture.ok
    assert capture.source == "vr_eye"
    assert capture.frame_size == (2, 2)
    assert capture.tangents == (-1.28, 1.28, -1.28, 1.28)


def test_no_frame_is_an_empty_capture_not_an_exception(monkeypatch, qapp):
    monkeypatch.setattr(vr_eye_capture, "shared_eye_capture", lambda: _Capture(None))

    capture = capture_eye(types.SimpleNamespace())

    assert not capture.ok
    assert capture.source == "vr_eye"


def test_frame_size_comes_from_the_recommended_render_target():
    module = types.SimpleNamespace(
        VRSystem=lambda: types.SimpleNamespace(getRecommendedRenderTargetSize=lambda: (2704, 2704))
    )

    assert eye_frame_size(module) == (2704, 2704)


def test_frame_size_is_none_when_the_runtime_cannot_say():
    module = types.SimpleNamespace(VRSystem=lambda: (_ for _ in ()).throw(RuntimeError("no runtime")))

    assert eye_frame_size(module) is None


def test_device_failure_is_remembered_not_retried_every_frame(monkeypatch):
    capture = vr_eye_capture.VREyeCapture()
    calls = {"n": 0}

    class _D3D:
        def D3D11CreateDevice(self, *args):
            calls["n"] += 1
            return -1

    monkeypatch.setattr(vr_eye_capture.ctypes, "WinDLL", lambda name: _D3D())

    assert capture.capture(types.SimpleNamespace()) is None
    assert capture.capture(types.SimpleNamespace()) is None
    assert calls["n"] == 1
    assert capture.unavailable_reason.startswith("D3D11CreateDevice")


class _Timing:
    def __init__(self, index):
        self.m_nFrameIndex = index


class _Compositor:
    """Frame counter that advances once per poll, like a compositor drawing at its rate."""

    def __init__(self, start=100, advancing=True):
        self.index = start
        self.advancing = advancing
        self.polls = 0

    def getFrameTiming(self, frames_ago=0):
        self.polls += 1
        if self.advancing and self.polls > 1:
            self.index += 1
        return True, _Timing(self.index)


class TestFreshFrame:
    """The mirror copy waits for the compositor to draw new frames.

    A copy taken right after the mirror is requested is the frame from the
    previous capture (measured live), so a fresh read waits for the frame
    counter to move on.
    """

    def test_the_copy_waits_for_two_new_frames(self):
        compositor = _Compositor()
        slept: list = []

        waited = vr_eye_capture.wait_for_fresh_frame(
            compositor, sleep=slept.append, clock=lambda: 0.0
        )

        assert waited == 2
        assert len(slept) == 2

    def test_a_still_compositor_is_given_up_on_after_the_timeout(self):
        compositor = _Compositor(advancing=False)
        now = {"t": 0.0}

        def clock():
            return now["t"]

        def sleep(seconds):
            now["t"] += 0.1

        waited = vr_eye_capture.wait_for_fresh_frame(
            compositor, timeout=0.25, sleep=sleep, clock=clock
        )

        assert waited == 0
        assert now["t"] >= 0.25

    def test_without_frame_timing_a_fixed_pause_stands_in(self):
        class _NoTiming:
            def getFrameTiming(self, frames_ago=0):
                raise RuntimeError("not available")

        slept: list = []

        waited = vr_eye_capture.wait_for_fresh_frame(_NoTiming(), sleep=slept.append)

        assert waited is None
        assert slept == [vr_eye_capture.FRESH_FRAME_FALLBACK_S]

    def test_frame_index_reads_the_binding_tuple(self):
        assert vr_eye_capture.compositor_frame_index(_Compositor(start=7)) == 7

        class _NotFilled:
            def getFrameTiming(self, frames_ago=0):
                return False, _Timing(3)

        assert vr_eye_capture.compositor_frame_index(_NotFilled()) is None


def test_the_chosen_eye_is_the_one_read(monkeypatch, qapp):
    frame = EyeFrame(bytes([10, 20, 30, 255]) * 4, 2, 2, (-1.2, 1.3, -1.2, 1.2), bgra=False)
    source = _Capture(frame)
    monkeypatch.setattr(vr_eye_capture, "shared_eye_capture", lambda: source)

    capture_eye(types.SimpleNamespace(), "left")

    assert source.eyes == ["left"]


def test_the_dominant_eye_defaults_to_the_right_one():
    module = types.SimpleNamespace(Eye_Left=0, Eye_Right=1)

    assert normalize_view_eye(None) == "right"
    assert normalize_view_eye("LEFT") == "left"
    assert normalize_view_eye("nonsense") == "right"
    assert eye_index(module, "left") == 0
    assert eye_index(module, "right") == 1
