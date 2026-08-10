from __future__ import annotations

import unittest

from research_system.adapters.vendors.baostock_socket_safety import (
    BaoStockTransportError,
    _receive_response,
    install_baostock_socket_safety,
)


class _ClosedPeerSocket:
    def __init__(self) -> None:
        self.recv_calls = 0

    def recv(self, _size: int) -> bytes:
        self.recv_calls += 1
        return b""


class BaoStockSocketSafetyTests(unittest.TestCase):
    def test_peer_eof_raises_after_one_recv_instead_of_spinning(self) -> None:
        sock = _ClosedPeerSocket()

        with self.assertRaisesRegex(BaoStockTransportError, "closed"):
            _receive_response(sock)

        self.assertEqual(sock.recv_calls, 1)

    def test_install_is_idempotent_and_updates_timeout(self) -> None:
        self.assertTrue(install_baostock_socket_safety(timeout_seconds=3))
        self.assertTrue(install_baostock_socket_safety(timeout_seconds=7))

        from baostock.util import socketutil

        self.assertEqual(
            socketutil._cognitrade_socket_safety["timeout_seconds"],
            7,
        )


if __name__ == "__main__":
    unittest.main()
