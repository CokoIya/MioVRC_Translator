import queue
import threading

from src.osc.sender import _QueuedOSCMessage, VRCOSCSender


def _sender_without_worker(maxsize: int = 8) -> VRCOSCSender:
    sender = VRCOSCSender.__new__(VRCOSCSender)
    sender._min_send_interval_s = 0.0
    sender._queue = queue.Queue(maxsize=maxsize)
    sender._state_lock = threading.Lock()
    sender._last_sent_at = 0.0
    sender._avatar_state = {}
    sender._chatbox_generation = 0
    sender._worker = None
    sender._last_error = ""
    sender._closed = False
    sender._ensure_worker_running = lambda: True
    return sender


def test_chatbox_duplicate_text_is_still_queued():
    sender = _sender_without_worker()

    assert sender.send_chatbox("hello") == "hello"
    assert sender.send_chatbox("hello") == "hello"

    first = sender._queue.get_nowait()
    second = sender._queue.get_nowait()
    assert first.address == "/chatbox/input"
    assert second.address == "/chatbox/input"
    assert first.arguments == ("hello", True, False)
    assert second.arguments == ("hello", True, False)


def test_chatbox_pacing_scales_with_text_length():
    sender = _sender_without_worker()
    sender._min_send_interval_s = 0.8

    assert sender.send_chatbox("hi") == "hi"
    assert sender.send_chatbox("x" * 120) == "x" * 120

    short = sender._queue.get_nowait()
    long = sender._queue.get_nowait()
    assert short.min_interval_s == 0.8
    assert long.min_interval_s is not None
    assert long.min_interval_s > short.min_interval_s


def test_chatbox_send_reports_failure_when_enqueue_fails():
    sender = _sender_without_worker()
    sender._enqueue_payload = lambda _payload: False

    assert sender.send_chatbox("hello") == ""


def test_queue_full_evicts_oldest_message_for_new_chatbox_payload():
    sender = _sender_without_worker(maxsize=1)
    sender._queue.put_nowait(
        _QueuedOSCMessage("/avatar/parameters/MioSpeaking", (True,), rate_limited=False)
    )

    assert sender.send_chatbox("new") == "new"
    payload = sender._queue.get_nowait()
    assert payload.address == "/chatbox/input"
    assert payload.arguments == ("new", True, False)


def test_full_chatbox_queue_drops_newest_without_reordering_earlier_sentences():
    sender = _sender_without_worker(maxsize=2)
    assert sender.send_chatbox("first") == "first"
    assert sender.send_chatbox("second") == "second"

    assert sender.send_chatbox("third") == ""
    first = sender._queue.get_nowait()
    second = sender._queue.get_nowait()
    assert first.arguments[0] == "first"
    assert second.arguments[0] == "second"


def test_avatar_update_never_evicts_a_queued_chatbox_sentence():
    sender = _sender_without_worker(maxsize=1)
    assert sender.send_chatbox("first") == "first"

    assert sender.send_avatar_bool("MioSpeaking", True) is False
    payload = sender._queue.get_nowait()
    assert payload.address == "/chatbox/input"
    assert payload.arguments[0] == "first"


def test_session_rollover_invalidates_pending_chatbox_but_keeps_avatar_updates():
    sender = _sender_without_worker(maxsize=3)
    assert sender.send_chatbox("old") == "old"
    assert sender.send_avatar_bool("MioSpeaking", True) is True

    assert sender.clear_pending_chatbox() == 1
    avatar = sender._queue.get_nowait()
    assert avatar.address == "/avatar/parameters/MioSpeaking"

    assert sender.send_chatbox("new") == "new"
    chatbox = sender._queue.get_nowait()
    assert chatbox.arguments[0] == "new"
    assert chatbox.generation == 1


def test_avatar_state_is_not_cached_when_enqueue_fails():
    sender = _sender_without_worker()
    sender._enqueue_payload = lambda _payload: False

    assert sender.send_avatar_bool("MioSpeaking", True) is False
    assert "MioSpeaking" not in sender._avatar_state


def test_sender_sanitizes_invalid_endpoint(monkeypatch):
    captured = {}

    class FakeUDPClient:
        def __init__(self, host, port):
            captured["host"] = host
            captured["port"] = port

        def send_message(self, _address, _arguments):
            pass

    monkeypatch.setattr("src.osc.sender.udp_client.SimpleUDPClient", FakeUDPClient)

    sender = VRCOSCSender(host="", port="bad", min_send_interval_s=0.0)
    try:
        assert captured == {"host": "127.0.0.1", "port": 9000}
    finally:
        sender.close()


def test_closed_sender_rejects_late_chatbox_and_avatar_work():
    sender = _sender_without_worker()
    sender._closed = True

    assert sender.send_chatbox("late") == ""
    assert sender.send_avatar_bool("MioSpeaking", True) is False
    assert sender._queue.empty()
