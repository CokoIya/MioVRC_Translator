"""OSC経由でVRChatにメッセージを送信する  """

from pythonosc import udp_client


MAX_CHATBOX_CHARS = 144


class VRCOSCSender:
    def __init__(self, host: str = "127.0.0.1", port: int = 9000):
        self._client = udp_client.SimpleUDPClient(host, port)

    def send_chatbox(self, text: str, immediate: bool = True) -> str:
        """
        /chatbox/input 経由でVRChatのチャットボックスにテキストを送信する  
        144文字を超える場合は自動的に省略記号で切り詰める  
        実際に送信したテキストを返す  
        """
        if len(text) > MAX_CHATBOX_CHARS:
            text = text[: MAX_CHATBOX_CHARS - 1] + "…"
        self._client.send_message("/chatbox/input", [text, immediate, False])
        return text

    def clear_chatbox(self):
        self._client.send_message("/chatbox/input", ["", True, False])
