"""VRChat のチャットボックス OSC を受信するヘルパー。"""

import threading
from typing import Callable, Optional, Set

from pythonosc import dispatcher, osc_server


class VRCOSCReceiver:
    def __init__(
        self,
        on_message: Callable[[str], None],
        port: int = 9001,
        own_messages: Optional[Set[str]] = None,
        on_own_message: Optional[Callable[[str], None]] = None,
    ):
        self.on_message = on_message
        self.port = port
        self._own_messages: Set[str] = own_messages if own_messages is not None else set()
        self._on_own_message = on_own_message
        self._server: Optional[osc_server.ThreadingOSCUDPServer] = None
        self._thread: Optional[threading.Thread] = None

    def _handle_chatbox(self, address, *args):
        del address
        if not args:
            return

        text = str(args[0]).strip()
        if not text:
            return

        if text in self._own_messages:
            if self._on_own_message:
                try:
                    self._on_own_message(text)
                except Exception:
                    pass
            return

        try:
            self.on_message(text)
        except Exception as exc:
            print(f"[OSCReceiver] on_message error: {exc}")

    def start(self):
        if self._server:
            return

        osc_dispatcher = dispatcher.Dispatcher()
        osc_dispatcher.map("/chatbox/input", self._handle_chatbox)

        self._server = osc_server.ThreadingOSCUDPServer(
            ("0.0.0.0", self.port),
            osc_dispatcher,
        )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def register_own_message(self, text: str):
        self._own_messages.add(text)
        if len(self._own_messages) > 200:
            self._own_messages.pop()
