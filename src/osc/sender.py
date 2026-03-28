from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
import time

from pythonosc import udp_client


MAX_CHATBOX_CHARS = 144
DEFAULT_MIN_SEND_INTERVAL_S = 0.8
DUPLICATE_WINDOW_S = 1.5
SEND_QUEUE_MAXSIZE = 32


@dataclass(frozen=True)
class _QueuedOSCMessage:
    address: str
    arguments: tuple[object, ...]
    rate_limited: bool = False


class VRCOSCSender:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9000,
        min_send_interval_s: float = DEFAULT_MIN_SEND_INTERVAL_S,
    ):
        self._client = udp_client.SimpleUDPClient(host, port)
        self._min_send_interval_s = max(float(min_send_interval_s), 0.0)
        self._queue: queue.Queue[_QueuedOSCMessage | None] = queue.Queue(
            maxsize=SEND_QUEUE_MAXSIZE
        )
        self._state_lock = threading.Lock()
        self._last_enqueued_text = ""
        self._last_enqueued_at = 0.0
        self._last_sent_at = 0.0
        self._avatar_state: dict[str, object] = {}
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

            if payload.rate_limited:
                wait_s = self._min_send_interval_s - (time.monotonic() - self._last_sent_at)
                if wait_s > 0:
                    time.sleep(wait_s)

            self._client.send_message(payload.address, list(payload.arguments))
            with self._state_lock:
                if payload.rate_limited:
                    self._last_sent_at = time.monotonic()

    def _enqueue_payload(self, payload: _QueuedOSCMessage | None) -> None:
        try:
            self._queue.put_nowait(payload)
            return
        except queue.Full:
            pass

        # 队列满时丢掉最旧的一条再重试，保证最新消息能进去
        try:
            self._queue.get_nowait()
        except queue.Empty:
            return

        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            pass

    def send_chatbox(
        self,
        text: str,
        immediate: bool = True,
        *,
        force: bool = False,
    ) -> str:
        safe = self._normalize_text(text)
        if not safe:
            return ""

        now = time.monotonic()
        with self._state_lock:
            if (
                not force
                and (
                safe == self._last_enqueued_text
                and (now - self._last_enqueued_at) < DUPLICATE_WINDOW_S
                )
            ):
                return safe
            self._last_enqueued_text = safe
            self._last_enqueued_at = now

        self._enqueue_payload(
            _QueuedOSCMessage(
                address="/chatbox/input",
                arguments=(safe, immediate, False),
                rate_limited=True,
            )
        )
        return safe

    def clear_chatbox(self):
        self._enqueue_payload(
            _QueuedOSCMessage(
                address="/chatbox/input",
                arguments=("", True, False),
                rate_limited=True,
            )
        )

    def send_avatar_parameter(self, name: str, value: object, *, force: bool = False) -> bool:
        param_name = str(name or "").strip()
        if not param_name:
            return False

        with self._state_lock:
            previous = self._avatar_state.get(param_name)
            if not force and previous == value:
                return False
            self._avatar_state[param_name] = value

        self._enqueue_payload(
            _QueuedOSCMessage(
                address=f"/avatar/parameters/{param_name}",
                arguments=(value,),
                rate_limited=False,
            )
        )
        return True

    def send_avatar_bool(self, name: str, value: bool, *, force: bool = False) -> bool:
        return self.send_avatar_parameter(name, bool(value), force=force)

    def send_avatar_int(self, name: str, value: int, *, force: bool = False) -> bool:
        return self.send_avatar_parameter(name, int(value), force=force)

    def clear_avatar_state(self, names: list[tuple[str, object]] | None = None) -> None:
        defaults = names or []
        for name, value in defaults:
            self.send_avatar_parameter(name, value, force=True)

    def close(self) -> None:
        if self._worker.is_alive():
            self._enqueue_payload(None)
            self._worker.join(timeout=1.0)
