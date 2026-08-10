from __future__ import annotations

import socket
import zlib
from importlib import import_module
from threading import RLock
from typing import Any

DEFAULT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_MESSAGE_TERMINATOR = b"<![CDATA[]]>\n"
_PATCH_LOCK = RLock()
_IO_LOCK = RLock()


class BaoStockTransportError(ConnectionError):
    """BaoStock's socket protocol ended before a complete response arrived."""


def _close_socket(sock: Any) -> None:
    try:
        sock.close()
    except (AttributeError, OSError):
        pass


def _receive_response(sock: Any) -> bytes:
    response = bytearray()
    while not response.endswith(_MESSAGE_TERMINATOR):
        chunk = sock.recv(8192)
        if not chunk:
            # baostock 0.9.3 omits this EOF check and repeatedly appends b""
            # forever after the peer closes a connection.
            raise BaoStockTransportError(
                "baostock connection closed before the response terminator"
            )
        response.extend(chunk)
        if len(response) > _MAX_RESPONSE_BYTES:
            raise BaoStockTransportError("baostock response exceeded the safety limit")
    return bytes(response)


def _decode_response(response: bytes, constants: Any) -> str:
    header_length = int(constants.MESSAGE_HEADER_LENGTH)
    if len(response) < header_length:
        raise BaoStockTransportError("baostock returned an incomplete response header")
    header = response[:header_length].decode()
    header_parts = header.split(constants.MESSAGE_SPLIT)
    if len(header_parts) < 3:
        raise BaoStockTransportError("baostock returned an invalid response header")
    if header_parts[1] not in constants.COMPRESSED_MESSAGE_TYPE_TUPLE:
        return response.decode()
    body_length = int(header_parts[2])
    body = response[header_length : header_length + body_length]
    return header + zlib.decompress(body).decode()


def install_baostock_socket_safety(
    *,
    timeout_seconds: float = DEFAULT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS,
) -> bool:
    """Install bounded, EOF-aware transport functions into baostock.

    UZI's provider and Cognitrade's direct adapters continue to call the
    original ``baostock`` API.  This only repairs the third-party socket
    transport boundary, whose upstream ``recv`` loop otherwise spins forever
    when the server closes a connection without its proprietary terminator.
    """

    timeout = float(timeout_seconds)
    if timeout <= 0:
        raise ValueError("baostock socket timeout must be positive")
    try:
        socketutil = import_module("baostock.util.socketutil")
        context = import_module("baostock.common.context")
        constants = import_module("baostock.common.contants")
    except ImportError:
        return False

    with _PATCH_LOCK:
        existing = getattr(socketutil, "_cognitrade_socket_safety", None)
        if isinstance(existing, dict):
            existing["timeout_seconds"] = timeout
            return True

        state = {"timeout_seconds": timeout}
        original_connect = socketutil.SocketUtil.connect
        original_send_msg = socketutil.send_msg

        def safe_connect(_self) -> None:
            with _IO_LOCK:
                previous = getattr(context, "default_socket", None)
                if previous is not None:
                    _close_socket(previous)
                current = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                current.settimeout(float(state["timeout_seconds"]))
                try:
                    current.connect(
                        (constants.BAOSTOCK_SERVER_IP, constants.BAOSTOCK_SERVER_PORT)
                    )
                except OSError as exc:
                    _close_socket(current)
                    context.default_socket = None
                    raise BaoStockTransportError("baostock connection failed") from exc
                context.default_socket = current

        def safe_send_msg(message: str) -> str:
            with _IO_LOCK:
                current = getattr(context, "default_socket", None)
                if current is None:
                    raise BaoStockTransportError("baostock is not connected")
                try:
                    current.settimeout(float(state["timeout_seconds"]))
                    current.sendall((str(message) + "\n").encode("utf-8"))
                    response = _receive_response(current)
                    return _decode_response(response, constants)
                except (BaoStockTransportError, OSError, UnicodeError, ValueError, zlib.error):
                    if getattr(context, "default_socket", None) is current:
                        context.default_socket = None
                    _close_socket(current)
                    raise

        socketutil.SocketUtil.connect = safe_connect
        socketutil.send_msg = safe_send_msg
        socketutil._cognitrade_socket_safety = state
        socketutil._cognitrade_original_connect = original_connect
        socketutil._cognitrade_original_send_msg = original_send_msg
        return True


__all__ = [
    "DEFAULT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS",
    "BaoStockTransportError",
    "install_baostock_socket_safety",
]
