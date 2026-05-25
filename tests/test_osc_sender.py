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
    sender._worker = None
    sender._last_error = ""
    sender._ensure_worker_running = lambda: None
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
