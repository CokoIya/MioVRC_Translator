from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal
from pythonosc import dispatcher, osc_server

from src.osc.sender import DEFAULT_MIN_SEND_INTERVAL_S, VRCOSCSender

logger = logging.getLogger(__name__)

DEFAULT_OSC_RECEIVE_HOST = "127.0.0.1"
DEFAULT_OSC_RECEIVE_PORT = 9001


def _coerce_port(value: object, default: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    return port if 0 < port <= 65535 else default


def _coerce_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    return None


class OscService(QObject):
    """Own OSC sender/listener lifecycles behind a Qt-friendly service API."""

    connected = Signal()
    disconnected = Signal()
    listener_started = Signal(str, int)
    listener_stopped = Signal()
    avatar_parameter_received = Signal(str, object)
    mute_self_changed = Signal(bool)
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._sender: VRCOSCSender | None = None
        self._listener_server: osc_server.ThreadingOSCUDPServer | None = None
        self._listener_thread: threading.Thread | None = None
        self._listener_lock = threading.RLock()
        self._listener_endpoint: tuple[str, int] | None = None
        self._sync_mute_self = True

    @property
    def sender(self) -> VRCOSCSender | None:
        return self._sender

    def connect_sender(
        self,
        host: str = "127.0.0.1",
        port: int = 9000,
        *,
        min_send_interval_s: float = DEFAULT_MIN_SEND_INTERVAL_S,
    ) -> VRCOSCSender:
        self.disconnect_sender()
        host_text = str(host or "").strip() or "127.0.0.1"
        port_number = _coerce_port(port, 9000)
        try:
            self._sender = VRCOSCSender(
                host=host_text,
                port=port_number,
                min_send_interval_s=float(min_send_interval_s),
            )
        except Exception as exc:
            self.error.emit(str(exc))
            raise
        self.connected.emit()
        return self._sender

    def connect(
        self,
        host: str = "127.0.0.1",
        port: int = 9000,
        *,
        min_send_interval_s: float = DEFAULT_MIN_SEND_INTERVAL_S,
    ) -> None:
        self.connect_sender(host, port, min_send_interval_s=min_send_interval_s)

    def disconnect_sender(self) -> None:
        if self._sender is None:
            return
        try:
            self._sender.close()
        finally:
            self._sender = None
            self.disconnected.emit()

    def disconnect(self) -> None:
        self.disconnect_sender()

    def start_listener(
        self,
        host: str = DEFAULT_OSC_RECEIVE_HOST,
        port: int = DEFAULT_OSC_RECEIVE_PORT,
        *,
        sync_mute_self: bool = True,
    ) -> None:
        host_text = str(host or "").strip() or DEFAULT_OSC_RECEIVE_HOST
        port_number = _coerce_port(port, DEFAULT_OSC_RECEIVE_PORT)
        with self._listener_lock:
            if (
                self._listener_server is not None
                and self._listener_thread is not None
                and self._listener_thread.is_alive()
                and self._listener_endpoint == (host_text, port_number)
                and self._sync_mute_self == bool(sync_mute_self)
            ):
                return
            self.stop_listener()
            osc_dispatcher = dispatcher.Dispatcher()
            osc_dispatcher.set_default_handler(self._handle_osc_message)
            try:
                server = osc_server.ThreadingOSCUDPServer(
                    (host_text, port_number),
                    osc_dispatcher,
                )
            except Exception as exc:
                message = str(exc).strip() or exc.__class__.__name__
                logger.warning("Failed to start OSC listener on %s:%s: %s", host_text, port_number, message)
                self.error.emit(message)
                raise
            thread = threading.Thread(
                target=server.serve_forever,
                daemon=True,
                name="osc-listener",
            )
            self._listener_server = server
            self._listener_thread = thread
            self._listener_endpoint = (host_text, port_number)
            self._sync_mute_self = bool(sync_mute_self)
            thread.start()
        self.listener_started.emit(host_text, port_number)
        logger.info("OSC listener started on %s:%s", host_text, port_number)

    def stop_listener(self) -> None:
        with self._listener_lock:
            server = self._listener_server
            thread = self._listener_thread
            if server is None and thread is None:
                return
            self._listener_server = None
            self._listener_thread = None
            self._listener_endpoint = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                logger.debug("Failed to shutdown OSC listener", exc_info=True)
            try:
                server.server_close()
            except Exception:
                logger.debug("Failed to close OSC listener socket", exc_info=True)
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self.listener_stopped.emit()
        logger.info("OSC listener stopped")

    def close(self) -> None:
        self.stop_listener()
        self.disconnect_sender()

    def send_text(self, text: str) -> bool:
        if self._sender is None:
            self.connect_sender()
        try:
            sent = self._sender.send_chatbox(text) if self._sender else ""
        except Exception as exc:
            self.error.emit(str(exc))
            return False
        if not sent:
            message = self._sender.last_error if self._sender else "OSC send failed"
            self.error.emit(message or "OSC send failed")
            return False
        return True

    def send_parameter(self, name: str, value: Any) -> bool:
        if not name:
            return False
        if self._sender is None:
            self.connect_sender()
        try:
            return bool(self._sender and self._sender.send_avatar_parameter(name, value))
        except Exception as exc:
            self.error.emit(str(exc))
            return False

    def _handle_osc_message(self, address: str, *args: object) -> None:
        try:
            self._process_avatar_parameter(address, args)
        except Exception as exc:
            logger.debug("Ignoring malformed OSC message address=%s args=%s: %s", address, args, exc)

    def _process_avatar_parameter(self, address: str, args: tuple[object, ...]) -> None:
        prefix = "/avatar/parameters/"
        if not str(address or "").startswith(prefix):
            return
        name = str(address)[len(prefix):].strip()
        if not name:
            return
        value = args[0] if args else None
        self.avatar_parameter_received.emit(name, value)
        if self._sync_mute_self and name == "MuteSelf":
            muted = _coerce_bool(value)
            if muted is not None:
                self.mute_self_changed.emit(muted)
