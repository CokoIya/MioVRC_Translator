"""Long lines go out as several chatbox pages; the typing bubble; clearing."""

from __future__ import annotations

import queue
import threading

from src.osc.sender import (
    CHATBOX_PAGE_SECONDS,
    MAX_CHATBOX_CHARS,
    MAX_CHATBOX_PAGES,
    VRCOSCSender,
    split_chatbox_pages,
)


class _Client:
    def __init__(self):
        self.sent: list = []

    def send_message(self, address, arguments):
        self.sent.append((address, list(arguments)))


def _sender() -> VRCOSCSender:
    sender = VRCOSCSender.__new__(VRCOSCSender)
    sender._min_send_interval_s = 0.0
    sender._queue = queue.Queue(maxsize=32)
    sender._state_lock = threading.Lock()
    sender._last_sent_at = 0.0
    sender._avatar_state = {}
    sender._chatbox_generation = 0
    sender._worker = None
    sender._last_error = ""
    sender._closed = False
    sender._typing = False
    sender._notify_sound = False
    sender._posted_since_clear = False
    sender._client = _Client()
    sender._ensure_worker_running = lambda: True
    return sender


def _queued(sender):
    items = []
    while not sender._queue.empty():
        items.append(sender._queue.get_nowait())
    return items


class TestPages:
    def test_a_short_line_is_one_page(self):
        assert split_chatbox_pages("你好") == ["你好"]
        assert split_chatbox_pages("   ") == []

    def test_a_long_line_breaks_at_a_sentence_end(self):
        first = "这是第一句话，" * 12 + "结束。"
        text = first + "第二句也很长，" * 12

        pages = split_chatbox_pages(text)

        assert pages[0] == first
        assert all(len(page) <= MAX_CHATBOX_CHARS for page in pages)
        assert "".join(pages) == text.rstrip()

    def test_spaced_text_breaks_between_words(self):
        text = " ".join(["word"] * 60)

        pages = split_chatbox_pages(text)

        assert len(pages) >= 2
        assert all(not page.startswith(" ") and not page.endswith(" ") for page in pages)
        assert all("wor " not in page for page in pages)

    def test_text_without_breaks_is_cut_at_the_limit(self):
        pages = split_chatbox_pages("あ" * (MAX_CHATBOX_CHARS + 10))

        assert [len(page) for page in pages] == [MAX_CHATBOX_CHARS, 10]

    def test_too_much_text_ends_with_an_ellipsis(self):
        pages = split_chatbox_pages("字" * (MAX_CHATBOX_CHARS * (MAX_CHATBOX_PAGES + 2)))

        assert len(pages) == MAX_CHATBOX_PAGES
        assert pages[-1].endswith("…")
        assert len(pages[-1]) <= MAX_CHATBOX_CHARS


class TestSending:
    def test_later_pages_wait_to_be_read(self):
        sender = _sender()
        seen: list = []

        text = "句子很长，" * 40
        assert sender.send_chatbox(text, completion_callback=seen.append)

        items = _queued(sender)
        assert len(items) >= 2
        assert all(item.address == "/chatbox/input" for item in items)
        assert items[0].completion_callback is not None
        assert all(item.completion_callback is None for item in items[1:])
        assert all(item.min_interval_s >= CHATBOX_PAGE_SECONDS for item in items[1:])

    def test_nothing_is_cut_off_any_more(self):
        sender = _sender()
        text = "a" * 200

        assert sender.send_chatbox(text) == text

        joined = "".join(item.arguments[0] for item in _queued(sender))
        assert joined == text

    def test_the_notification_sound_follows_the_setting(self):
        sender = _sender()
        sender.set_notify_sound(True)

        sender.send_chatbox("hi")

        assert _queued(sender)[0].arguments == ("hi", True, True)

    def test_the_typing_bubble_is_sent_on_change_only(self):
        sender = _sender()

        assert sender.set_typing(True)
        assert not sender.set_typing(True)
        assert sender.set_typing(False)

        assert sender._client.sent == [("/chatbox/typing", [True]), ("/chatbox/typing", [False])]

    def test_a_posted_message_ends_the_typing_bubble(self):
        sender = _sender()
        sender.set_typing(True)

        sender.send_chatbox("done")

        assert sender._typing is False
        # So the next "typing" is sent again.
        assert sender.set_typing(True)

    def test_clearing_only_takes_down_what_mio_posted(self):
        sender = _sender()

        assert not sender.clear_chatbox_if_posted()
        assert sender._queue.empty()

        sender._posted_since_clear = True
        assert sender.clear_chatbox_if_posted()
        assert _queued(sender)[0].arguments == ("", True, False)
