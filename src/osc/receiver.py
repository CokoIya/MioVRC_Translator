"""VRChatのOSC出力を受信し、他プレイヤーのチャットボックスメッセージを取得する。"""

import threading
from typing import Callable, Optional, Set

from pythonosc import dispatcher, osc_server


class VRCOSCReceiver:
    """
    VRC OSC出力ポート（デフォルト9001）をリッスンする。
    受信した /chatbox/input メッセージのテキストを `on_message` に渡す。
    自アプリが送信したメッセージはフィルタリングして除外する。
    """

    def __init__(
        self,
        on_message: Callable[[str], None],
        port: int = 9001,
        own_messages: Optional[Set[str]] = None,
    ):
        self.on_message = on_message
        self.port = port
        self._own_messages: Set[str] = own_messages if own_messages is not None else set()
        self._server: Optional[osc_server.ThreadingOSCUDPServer] = None
        self._thread: Optional[threading.Thread] = None

    def _handle_chatbox(self, address, *args):
        if not args:
            return
        text = str(args[0]).strip()
        if not text:
            return
        # 自アプリが送信したメッセージをフィルタリング
        if text in self._own_messages:
            return
        try:
            self.on_message(text)
        except Exception as e:
            print(f"[OSCReceiver] on_message error: {e}")

    def start(self):
        if self._server:
            return
        d = dispatcher.Dispatcher()
        d.map("/chatbox/input", self._handle_chatbox)

        self._server = osc_server.ThreadingOSCUDPServer(("0.0.0.0", self.port), d)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None

    def register_own_message(self, text: str):
        """送信済みメッセージを記録して受信フィルタリングに使用する。"""
        self._own_messages.add(text)
        # セットが際限なく増大しないよう制限する
        if len(self._own_messages) > 200:
            self._own_messages.pop()
