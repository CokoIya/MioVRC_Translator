from src.core.output_dispatcher import OutputMessage
from src.core.overlay_service import OverlayService


class _Backend:
    def __init__(self):
        self.messages = []
        self.revealed = 0
        self.hidden = 0
        self.listen_status = []

    def show_message(self, message):
        self.messages.append(message)
        return True

    def set_listen_status(self, listening: bool):
        self.listen_status.append(listening)

    def reveal(self):
        self.revealed += 1

    def hide(self):
        self.hidden += 1


def test_overlay_service_only_routes_messages_when_enabled():
    backend = _Backend()
    service = OverlayService(backend)
    message = OutputMessage(source="listen", display_text="hello", chatbox_text="[Listen] hello")

    assert service.show_message(message) is False
    assert backend.messages == []

    service.set_enabled(True)

    assert backend.revealed == 1
    assert service.show_message(message) is True
    assert backend.messages == [message]

    service.set_enabled(False)

    assert backend.hidden == 1


def test_overlay_service_forwards_listen_status():
    backend = _Backend()
    service = OverlayService(backend)

    service.set_listen_status(True)
    service.set_listen_status(False)

    assert backend.listen_status == [True, False]
