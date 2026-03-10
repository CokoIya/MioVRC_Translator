from __future__ import annotations

import queue
import threading
import time

from pythonosc import udp_client


MAX_CHATBOX_CHARS = 144
DEFAULT_MIN_SEND_INTERVAL_S = 0.8
DUPLICATE_WINDOW_S = 1.5
SEND_QUEUE_MAXSIZE = 32


class VRCOSCSender:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9000,
        min_send_interval_s: float = DEFAULT_MIN_SEND_INTERVAL_S,
    ):
        self._client = udp_client.SimpleUDPClient(host, port)
        self._min_send_interval_s = max(float(min_send_interval_s), 0.0)
        self._queue: queue.Queue[tuple[str, bool] | None] = queue.Queue(
            maxsize=SEND_QUEUE_MAXSIZE
        )
        self._state_lock = threading.Lock()
        self._last_enqueued_text = ""
        self._last_enqueued_at = 0.0
        self._last_sent_at = 0.0
        self._worker = threading.Thread(target=self._send_loop, daemon=True)
        self._worker.start()

    @staticmethod
    def _normalize_text(text: str) -> str:
        safe = str(text or "").strip()
        if len(safe) > MAX_CHATBOX_CHARS:
            safe = safe[: MAX_CHATBOX_CHARS - 3] + "..."
        return safe

    def _send_loop(self) -> None:
        while True:
            payload = self._queue.get()
            if payload is None:
                return

            text, immediate = payload
            wait_s = self._min_send_interval_s - (time.monotonic() - self._last_sent_at)
            if wait_s > 0:
                time.sleep(wait_s)

            self._client.send_message("/chatbox/input", [text, immediate, False])
            with self._state_lock:
                self._last_sent_at = time.monotonic()

    def _enqueue_payload(self, payload: tuple[str, bool] | None) -> None:
        try:
            self._queue.put_nowait(payload)
            return
        except queue.Full:
            pass

        try:
            self._queue.get_nowait()
        except queue.Empty:
            return

        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            pass

    def send_chatbox(self, text: str, immediate: bool = True) -> str:
        safe = self._normalize_text(text)
        if not safe:
            return ""

        now = time.monotonic()
        with self._state_lock:
            if (
                safe == self._last_enqueued_text
                and (now - self._last_enqueued_at) < DUPLICATE_WINDOW_S
            ):
                return safe
            self._last_enqueued_text = safe
            self._last_enqueued_at = now

        self._enqueue_payload((safe, immediate))
        return safe

    def clear_chatbox(self):
        self._enqueue_payload(("", True))

    def close(self) -> None:
        if self._worker.is_alive():
            self._enqueue_payload(None)
            self._worker.join(timeout=1.0)
