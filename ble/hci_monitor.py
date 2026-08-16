"""Minimal parser for Linux's non-invasive HCI monitor channel.

Only three events are needed by the board multiplexer: Enhanced Connection
Complete, Advertising Set Terminated, and Disconnection Complete.  The Linux
monitor socket duplicates traffic already consumed by the kernel/BlueZ, so it
does not compete with bluetoothd for ownership of the controller.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import socket
import struct
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

# Linux UAPI values from include/uapi/linux/socket.h and
# include/net/bluetooth/bluetooth.h.  Some Python distributions (notably
# Conda builds) omit the symbolic constants even though the running Linux
# kernel supports the socket family.
AF_BLUETOOTH = getattr(socket, "AF_BLUETOOTH", 31)
BTPROTO_HCI = getattr(socket, "BTPROTO_HCI", 1)
HCI_DEV_NONE = 0xffff
HCI_CHANNEL_MONITOR = 2
HCI_MON_EVENT_PKT = 3
HCI_EVENT_LE_META = 0x3e
HCI_EVENT_DISCONNECTION_COMPLETE = 0x05
LE_ENHANCED_CONNECTION_COMPLETE = 0x0a
LE_CONNECTION_COMPLETE = 0x01
LE_ADVERTISING_SET_TERMINATED = 0x12


class _SockaddrHci(ctypes.Structure):
    """Linux ``struct sockaddr_hci`` (Python exposes channels only in 3.14+)."""

    _fields_ = [
        ("family", ctypes.c_ushort),
        ("device", ctypes.c_ushort),
        ("channel", ctypes.c_ushort),
    ]


def _bind_monitor_channel(sock: socket.socket) -> None:
    """Bind an HCI monitor socket on every supported Python (3.10+)."""
    address = _SockaddrHci(
        AF_BLUETOOTH, HCI_DEV_NONE, HCI_CHANNEL_MONITOR)
    libc = ctypes.CDLL(None, use_errno=True)
    bind = libc.bind
    bind.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    bind.restype = ctypes.c_int
    if bind(sock.fileno(), ctypes.byref(address), ctypes.sizeof(address)) < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


@dataclass(frozen=True)
class AdvertisingConnection:
    advertising_handle: int
    connection_handle: int
    peer_address: str | None


@dataclass(frozen=True)
class Disconnection:
    connection_handle: int
    reason: int


class HciEventParser:
    """Join connection and advertising-termination events by HCI handle."""

    def __init__(self) -> None:
        self._peers: dict[int, str] = {}
        self._advertisements: dict[int, int] = {}

    @staticmethod
    def _address(raw: bytes) -> str:
        return ":".join(f"{octet:02X}" for octet in reversed(raw))

    def feed(self, packet: bytes, adapter_index: int) -> list[object]:
        if len(packet) < 8:
            return []
        opcode, index, length = struct.unpack_from("<HHH", packet)
        if opcode != HCI_MON_EVENT_PKT or index != adapter_index:
            return []
        data = packet[6:6 + length]
        if len(data) < 2 or len(data) != length:
            return []
        event_code, parameter_length = data[0], data[1]
        if parameter_length + 2 > len(data):
            return []
        if event_code == HCI_EVENT_DISCONNECTION_COMPLETE and len(data) >= 6:
            if data[2] != 0:
                return []
            handle = int.from_bytes(data[3:5], "little") & 0x0fff
            self._peers.pop(handle, None)
            self._advertisements.pop(handle, None)
            return [Disconnection(handle, data[5])]
        if event_code != HCI_EVENT_LE_META or len(data) < 3:
            return []
        subevent = data[2]
        if subevent in (LE_CONNECTION_COMPLETE,
                        LE_ENHANCED_CONNECTION_COMPLETE) and len(data) >= 14:
            if data[3] != 0:
                return []
            handle = int.from_bytes(data[4:6], "little") & 0x0fff
            self._peers[handle] = self._address(data[8:14])
            return self._complete(handle)
        if subevent == LE_ADVERTISING_SET_TERMINATED and len(data) >= 8:
            if data[3] != 0:
                return []
            adv_handle = data[4]
            handle = int.from_bytes(data[5:7], "little") & 0x0fff
            self._advertisements[handle] = adv_handle
            return self._complete(handle)
        return []

    def _complete(self, handle: int) -> list[AdvertisingConnection]:
        if handle not in self._advertisements or handle not in self._peers:
            return []
        return [AdvertisingConnection(
            self._advertisements.pop(handle), handle,
            self._peers.pop(handle))]


class HciMonitor:
    """Async Linux monitor-socket reader for one hciN controller."""

    def __init__(self, adapter_index: int,
                 callback: Callable[[object], None]) -> None:
        self._adapter_index = adapter_index
        self._callback = callback
        self._parser = HciEventParser()
        self._socket: socket.socket | None = None

    def open(self) -> None:
        sock = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
        try:
            _bind_monitor_channel(sock)
            sock.setblocking(False)
            self._socket = sock
        except BaseException:
            sock.close()
            raise

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    async def run(self) -> None:
        if self._socket is None:
            self.open()
        assert self._socket is not None
        loop = asyncio.get_running_loop()
        while True:
            packet = await loop.sock_recv(self._socket, 4096)
            if not packet:
                return
            for event in self._parser.feed(packet, self._adapter_index):
                self._callback(event)
