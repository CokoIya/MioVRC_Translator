from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from src.ui import sponsor_window


class _FakeLabel:
    instances: list["_FakeLabel"] = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pack_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        _FakeLabel.instances.append(self)

    def pack(self, *args, **kwargs):
        self.pack_calls.append((args, kwargs))


class _FakeFrame:
    instances: list["_FakeFrame"] = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pack_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        _FakeFrame.instances.append(self)

    def pack(self, *args, **kwargs):
        self.pack_calls.append((args, kwargs))


class _DummyChild:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class _DummyContainer:
    def __init__(self):
        self.children = [_DummyChild(), _DummyChild()]

    def winfo_children(self):
        return self.children


class _DummyTotalLabel:
    def __init__(self):
        self.configured: dict[str, object] = {}

    def configure(self, **kwargs):
        self.configured.update(kwargs)


class _DummyParent:
    def winfo_exists(self):
        return True

    def winfo_x(self):
        return 10

    def winfo_y(self):
        return 5

    def winfo_width(self):
        return 500

    def winfo_height(self):
        return 300


class _DummyWindow:
    def __init__(self):
        self._locked_height = 0
        self.geometry_value = ""
        self.minsize_calls: list[tuple[int, int]] = []
        self.maxsize_calls: list[tuple[int, int]] = []
        self._parent = _DummyParent()

    def update_idletasks(self):
        return None

    def winfo_x(self):
        return 0

    def winfo_y(self):
        return 0

    def winfo_width(self):
        return 520

    def winfo_reqwidth(self):
        return 520

    def winfo_height(self):
        return 500

    def winfo_reqheight(self):
        return 500

    def winfo_screenwidth(self):
        return 800

    def winfo_screenheight(self):
        return 900

    def _content_fit_height(self):
        return 520

    def minsize(self, width, height):
        self.minsize_calls.append((width, height))

    def maxsize(self, width, height):
        self.maxsize_calls.append((width, height))

    def geometry(self, value):
        self.geometry_value = value


class SponsorWindowTests(TestCase):
    def setUp(self):
        _FakeLabel.instances.clear()
        _FakeFrame.instances.clear()

    def test_render_sponsor_list_renders_cards_with_large_font(self):
        window = sponsor_window.SponsorWindow.__new__(sponsor_window.SponsorWindow)
        window._list_container = _DummyContainer()
        window._total_label = _DummyTotalLabel()

        def fake_font(*, size, weight=None):
            return SimpleNamespace(size=size, weight=weight)

        with patch.object(sponsor_window.ctk, "CTkLabel", _FakeLabel), patch.object(
            sponsor_window.ctk, "CTkFrame", _FakeFrame
        ), patch.object(sponsor_window.ctk, "CTkFont", side_effect=fake_font):
            sponsor_window.SponsorWindow._render_sponsor_list(
                window,
                [{"name": "Alice"}, {"name": "Bob"}],
                "en",
            )

        self.assertEqual(len(_FakeFrame.instances), 2)
        self.assertTrue(_FakeLabel.instances)
        sponsor_label = _FakeLabel.instances[0]
        self.assertEqual(sponsor_label.kwargs["font"].size, 20)
        self.assertEqual(sponsor_label.kwargs["text_color"], sponsor_window.GOLD)
        self.assertIn("2", str(window._total_label.configured.get("text", "")))

    def test_show_page_updates_current_page_and_header(self):
        window = sponsor_window.SponsorWindow.__new__(sponsor_window.SponsorWindow)
        window._current_page = "list"
        window._page_visible = False
        window._lang = "zh"
        window._page_list = SimpleNamespace(pack_forget=lambda: None, pack=lambda **_kwargs: None)
        window._page_qr = SimpleNamespace(pack_forget=lambda: None, pack=lambda **_kwargs: None)
        configured = {}
        window._page_header = SimpleNamespace(configure=lambda **kwargs: configured.update(kwargs))
        window._animate_page_switch = lambda: None
        window._sync_locked_geometry = lambda center=False: None

        sponsor_window.SponsorWindow._show_page(window, "qr", animate=False)

        self.assertEqual(window._current_page, "qr")
        self.assertEqual(configured["text"], sponsor_window._t(sponsor_window._QR_PAGE_TITLE, "zh"))

    def test_calculate_window_height_uses_restored_fixed_height(self):
        window = sponsor_window.SponsorWindow.__new__(sponsor_window.SponsorWindow)
        window.winfo_screenheight = lambda: 900

        self.assertEqual(sponsor_window.SponsorWindow._calculate_window_height(window), 520)

    def test_sync_locked_geometry_clamps_popup_into_view(self):
        window = _DummyWindow()

        sponsor_window.SponsorWindow._sync_locked_geometry(window, center=True)

        self.assertEqual(window._locked_height, 520)
        self.assertEqual(window.geometry_value, "520x520+18+18")
        self.assertTrue(window.minsize_calls)
        self.assertTrue(window.maxsize_calls)
