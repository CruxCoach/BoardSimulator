"""ATT/link log from the Linux HCI monitor channel (read-only sniffer).

BlueZ answers much of what a central does on its own: the MTU exchange,
service discovery, reads of Generic Access, and every request for an
attribute the simulator does not offer. None of that reaches the D-Bus GATT
objects, so a protocol test against an unknown app would miss it. The
monitor channel (``ble.hci_monitor``) duplicates all HCI traffic without
competing with bluetoothd; this module turns it into log lines:

- link events: LE connection (peer address, role, interval), connection
  update, data length, PHY, encryption change, disconnection with reason;
- SMP pairing / security requests from the peer;
- every ATT PDU with its direction, decoded: MTU exchange, discovery with
  what the simulator answered (building a handle -> UUID map), reads, writes
  (request / command / prepare / execute), subscriptions (CCCD writes),
  notifications, indications and error responses.

The parser is pure (bytes in, ``(level, message)`` lines out) and unit
tested with synthetic packets; :class:`AttMonitor` is the async runner the
peripheral starts. Needs root (CAP_NET_RAW), like the rest of live BLE.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
from dataclasses import dataclass, field
from typing import Callable, Iterable

from ble.hci_monitor import (AF_BLUETOOTH, BTPROTO_HCI, HCI_EVENT_LE_META,
                             _bind_monitor_channel)

logger = logging.getLogger("ble.att")

MON_EVENT = 3
MON_ACL_TX = 4   # host -> controller: the simulator sends
MON_ACL_RX = 5   # controller -> host: the peer sent
CID_ATT = 0x0004
CID_SMP = 0x0006

DEBUG, INFO, WARNING = logging.DEBUG, logging.INFO, logging.WARNING

UUID16_NAMES = {
    0x1800: "Generic Access", 0x1801: "Generic Attribute",
    0x180A: "Device Information", 0x2800: "Primary Service",
    0x2801: "Secondary Service", 0x2802: "Include", 0x2803: "Characteristic",
    0x2900: "Extended Properties", 0x2901: "User Description",
    0x2902: "CCCD", 0x2903: "SCCD", 0x2904: "Presentation Format",
    0x2A00: "Device Name", 0x2A01: "Appearance", 0x2A04: "PPCP",
    0x2A05: "Service Changed", 0x2AA6: "Central Address Resolution",
    0x2B29: "Client Supported Features", 0x2B2A: "Database Hash",
    0x2B3A: "Server Supported Features",
}
ATT_ERRORS = {
    0x01: "Invalid Handle", 0x02: "Read Not Permitted",
    0x03: "Write Not Permitted", 0x04: "Invalid PDU",
    0x05: "Insufficient Authentication", 0x06: "Request Not Supported",
    0x07: "Invalid Offset", 0x08: "Insufficient Authorization",
    0x09: "Prepare Queue Full", 0x0A: "Attribute Not Found",
    0x0B: "Attribute Not Long", 0x0C: "Insufficient Key Size",
    0x0D: "Invalid Attribute Value Length", 0x0E: "Unlikely Error",
    0x0F: "Insufficient Encryption", 0x10: "Unsupported Group Type",
    0x11: "Insufficient Resources", 0x12: "Database Out Of Sync",
    0x13: "Value Not Allowed",
}
DISCONNECT_REASONS = {
    0x05: "Authentication Failure", 0x08: "Connection Timeout",
    0x13: "Remote User Terminated", 0x14: "Remote Low Resources",
    0x15: "Remote Power Off", 0x16: "Terminated by Local Host",
    0x1A: "Unsupported Remote Feature", 0x22: "LL Response Timeout",
    0x28: "Instant Passed", 0x3B: "Unacceptable Connection Parameters",
    0x3D: "MIC Failure", 0x3E: "Failed to be Established",
}
OPCODES = {
    0x01: "ERROR", 0x02: "MTU REQ", 0x03: "MTU RSP", 0x04: "FIND INFO REQ",
    0x05: "FIND INFO RSP", 0x06: "FIND BY TYPE REQ", 0x07: "FIND BY TYPE RSP",
    0x08: "READ BY TYPE REQ", 0x09: "READ BY TYPE RSP", 0x0A: "READ REQ",
    0x0B: "READ RSP", 0x0C: "READ BLOB REQ", 0x0D: "READ BLOB RSP",
    0x0E: "READ MULTI REQ", 0x0F: "READ MULTI RSP",
    0x10: "READ BY GROUP REQ", 0x11: "READ BY GROUP RSP", 0x12: "WRITE REQ",
    0x13: "WRITE RSP", 0x16: "PREPARE WRITE REQ", 0x17: "PREPARE WRITE RSP",
    0x18: "EXECUTE WRITE REQ", 0x19: "EXECUTE WRITE RSP",
    0x1B: "NOTIFY", 0x1D: "INDICATE", 0x1E: "CONFIRM",
    0x20: "READ MULTI VAR REQ", 0x21: "READ MULTI VAR RSP",
    0x23: "MULTI NOTIFY", 0x52: "WRITE CMD", 0xD2: "SIGNED WRITE CMD",
}
DISCOVERY_REQUESTS = {0x04, 0x06, 0x08, 0x10}
SMP_CODES = {
    0x01: "pairing request", 0x02: "pairing response",
    0x03: "pairing confirm", 0x04: "pairing random",
    0x05: "pairing failed", 0x0B: "security request",
}

Line = tuple[int, str]


def format_uuid(raw: bytes) -> str:
    """16-bit UUIDs as ``0x2902 (CCCD)``, 128-bit as the usual string."""
    if len(raw) == 2:
        value = int.from_bytes(raw, "little")
        name = UUID16_NAMES.get(value)
        return f"0x{value:04x}" + (f" ({name})" if name else "")
    if len(raw) == 16:
        h = raw[::-1].hex()
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
    return raw.hex() or "?"


def _address(raw: bytes) -> str:
    return ":".join(f"{octet:02X}" for octet in reversed(raw))


def _value(raw: bytes, limit: int = 40) -> str:
    text = raw[:limit].decode("ascii", errors="replace")
    shown = text if text.isprintable() and raw else ""
    hexed = raw[:limit].hex() + ("…" if len(raw) > limit else "")
    return f"{len(raw)} B {hexed or '(empty)'}" + (f" '{shown}'" if shown else "")


@dataclass
class _Link:
    peer: str = "?"
    # Partial L2CAP frames per direction (True = from the peer).
    partial: dict[bool, bytearray] = field(default_factory=dict)
    expected: dict[bool, int] = field(default_factory=dict)
    # Last request per direction, to read its response.
    request: dict[bool, tuple[int, bytes]] = field(default_factory=dict)
    client_mtu: int | None = None


class AttLogParser:
    """Monitor packets of one controller -> log lines.

    ``quiet_uuids`` are characteristics whose writes the session logs
    already (the protocol RX line); their ATT writes go to DEBUG.
    """

    def __init__(self, adapter_index: int,
                 quiet_uuids: Iterable[str] = ()) -> None:
        self.adapter_index = adapter_index
        self.quiet_uuids = {u.lower() for u in quiet_uuids}
        self._links: dict[int, _Link] = {}
        # Our own GATT database as the simulator announced it in discovery.
        self.handles: dict[int, str] = {}
        self._char_values: dict[int, str] = {}

    # -- entry point ------------------------------------------------------

    def feed(self, packet: bytes) -> list[Line]:
        if len(packet) < 6:
            return []
        opcode, index, length = struct.unpack_from("<HHH", packet)
        if index != self.adapter_index:
            return []
        data = packet[6:6 + length]
        if opcode == MON_EVENT:
            return self._event(data)
        if opcode in (MON_ACL_TX, MON_ACL_RX):
            return self._acl(data, from_peer=opcode == MON_ACL_RX)
        return []

    # -- HCI events -------------------------------------------------------

    def _link(self, handle: int) -> _Link:
        return self._links.setdefault(handle, _Link())

    def _event(self, data: bytes) -> list[Line]:
        if len(data) < 2 or data[1] + 2 > len(data):
            return []
        code = data[0]
        if code == 0x05 and len(data) >= 6:
            handle = int.from_bytes(data[3:5], "little") & 0x0FFF
            if data[2]:
                return []
            link = self._links.pop(handle, _Link())
            reason = data[5]
            return [(INFO, f"LINK {link.peer} disconnected (handle 0x{handle:03x}, "
                     f"reason 0x{reason:02x} "
                     f"{DISCONNECT_REASONS.get(reason, 'unknown')})")]
        if code in (0x08, 0x59) and len(data) >= 6:
            handle = int.from_bytes(data[3:5], "little") & 0x0FFF
            state = "on" if data[5] else "off"
            status = "" if not data[2] else f" (status 0x{data[2]:02x})"
            return [(INFO, f"LINK {self._link(handle).peer} encryption {state}{status}")]
        if code != HCI_EVENT_LE_META or len(data) < 3:
            return []
        sub = data[2]
        if sub in (0x01, 0x0A, 0x29) and len(data) >= 14:
            if data[3]:
                return [(INFO, f"LINK connection attempt failed (status 0x{data[3]:02x})")]
            handle = int.from_bytes(data[4:6], "little") & 0x0FFF
            role = "peripheral" if data[6] == 1 else "central"
            peer = _address(data[8:14])
            timing = 14 if sub == 0x01 else 26
            link = _Link(peer=peer)
            self._links[handle] = link
            line = (f"LINK {peer} connected (handle 0x{handle:03x}, "
                    f"we are {role}, address type {data[7]}")
            if len(data) >= timing + 6:
                interval, latency, timeout = struct.unpack_from("<HHH", data, timing)
                line += (f", interval {interval * 1.25:g} ms, latency {latency}, "
                         f"timeout {timeout * 10} ms")
            return [(INFO, line + ")")]
        if sub == 0x03 and len(data) >= 12:
            handle = int.from_bytes(data[4:6], "little") & 0x0FFF
            interval, latency, timeout = struct.unpack_from("<HHH", data, 6)
            return [(INFO, f"LINK {self._link(handle).peer} connection update: "
                     f"interval {interval * 1.25:g} ms, latency {latency}, "
                     f"timeout {timeout * 10} ms (status 0x{data[3]:02x})")]
        if sub == 0x07 and len(data) >= 13:
            handle = int.from_bytes(data[3:5], "little") & 0x0FFF
            tx, _tx_time, rx, _rx_time = struct.unpack_from("<HHHH", data, 5)
            return [(INFO, f"LINK {self._link(handle).peer} data length: "
                     f"tx {tx} B, rx {rx} B")]
        if sub == 0x0C and len(data) >= 8:
            handle = int.from_bytes(data[4:6], "little") & 0x0FFF
            return [(INFO, f"LINK {self._link(handle).peer} PHY tx {data[6]} "
                     f"rx {data[7]} (1 = 1M, 2 = 2M, 3 = coded)")]
        return []

    # -- ACL / L2CAP --------------------------------------------------------

    def _acl(self, data: bytes, from_peer: bool) -> list[Line]:
        if len(data) < 4:
            return []
        header, length = struct.unpack_from("<HH", data)
        handle, boundary = header & 0x0FFF, (header >> 12) & 0x3
        payload = data[4:4 + length]
        link = self._link(handle)
        if boundary == 0x1:
            buffer = link.partial.get(from_peer)
            if buffer is None:
                return []
            buffer.extend(payload)
        else:
            if len(payload) < 4:
                return []
            link.partial[from_peer] = bytearray(payload)
            link.expected[from_peer] = 4 + int.from_bytes(payload[:2], "little")
        buffer = link.partial[from_peer]
        if len(buffer) < link.expected[from_peer]:
            return []
        frame = bytes(buffer[:link.expected[from_peer]])
        del link.partial[from_peer]
        cid = int.from_bytes(frame[2:4], "little")
        if cid == CID_ATT and len(frame) > 4:
            return self._att(link, frame[4:], from_peer)
        if cid == CID_SMP and len(frame) > 4 and from_peer:
            name = SMP_CODES.get(frame[4], f"code 0x{frame[4]:02x}")
            return [(INFO, f"SMP {link.peer}: peer sent {name}")]
        return []

    # -- ATT ----------------------------------------------------------------

    def _describe(self, handle: int) -> str:
        name = self.handles.get(handle)
        return f"0x{handle:04x}" + (f" ({name})" if name else "")

    def _char_for_cccd(self, handle: int) -> str | None:
        below = [h for h in self._char_values if h < handle]
        return self._char_values[max(below)] if below else None

    def _quiet(self, handle: int) -> bool:
        uuid = self._char_values.get(handle, "")
        return uuid.split(" ")[0] in self.quiet_uuids

    def _att(self, link: _Link, pdu: bytes, from_peer: bool) -> list[Line]:
        op = pdu[0]
        body = pdu[1:]
        who = f"ATT {link.peer} {'peer→sim' if from_peer else 'sim→peer'}"
        name = OPCODES.get(op, f"opcode 0x{op:02x}")
        # Requests from the peer address our GATT server; requests we send
        # (BlueZ browsing the phone's server) are context only.
        server_side = from_peer == (op not in (0x03, 0x05, 0x07, 0x09, 0x0B,
                                              0x0D, 0x0F, 0x11, 0x13, 0x17,
                                              0x19, 0x21, 0x01, 0x1B, 0x1D,
                                              0x23))
        level = INFO if server_side else DEBUG
        if op & 0x01 == 0 and op not in (0x52, 0xD2, 0x1E):
            link.request[from_peer] = (op, body)
        if op == 0x02 and len(body) >= 2:
            mtu = int.from_bytes(body[:2], "little")
            link.client_mtu = mtu if from_peer else link.client_mtu
            return [(level, f"{who} MTU request {mtu}")]
        if op == 0x03 and len(body) >= 2:
            mtu = int.from_bytes(body[:2], "little")
            request = link.request.pop(not from_peer, (None, b""))
            client = (int.from_bytes(request[1][:2], "little")
                      if request[0] == 0x02 and len(request[1]) >= 2 else None)
            effective = f", ATT MTU now {min(client, mtu)}" if client else ""
            return [(level if not from_peer else DEBUG,
                     f"{who} MTU response {mtu}{effective}")]
        if op in (0x10, 0x08, 0x04, 0x06) and len(body) >= 4:
            start, end = struct.unpack_from("<HH", body)
            what = {0x10: "discover services", 0x08: "read by type",
                    0x04: "find information", 0x06: "find by type value"}[op]
            extra = f" {format_uuid(body[4:])}" if op in (0x10, 0x08) else ""
            if op == 0x06 and len(body) >= 6:
                extra = f" {format_uuid(body[4:6])} = {format_uuid(body[6:])}"
            return [(level, f"{who} {what} 0x{start:04x}..0x{end:04x}{extra}")]
        if op in (0x11, 0x09, 0x05, 0x07):
            return self._discovery_response(link, op, body, from_peer, who)
        if op in (0x0A, 0x0C) and len(body) >= 2:
            handle = int.from_bytes(body[:2], "little")
            offset = (f" offset {int.from_bytes(body[2:4], 'little')}"
                      if op == 0x0C and len(body) >= 4 else "")
            return [(level, f"{who} READ {self._describe(handle) if from_peer else f'0x{handle:04x}'}{offset}")]
        if op in (0x0B, 0x0D, 0x0F, 0x21):
            request = link.request.pop(not from_peer, (None, b""))
            target = ""
            if request[0] in (0x0A, 0x0C) and len(request[1]) >= 2:
                handle = int.from_bytes(request[1][:2], "little")
                target = f" of {self._describe(handle) if not from_peer else f'0x{handle:04x}'}"
            return [(INFO if not from_peer else DEBUG,
                     f"{who} {name}{target}: {_value(body)}")]
        if op in (0x12, 0x52, 0xD2, 0x16) and len(body) >= 2:
            return self._write(link, op, body, from_peer, who, level, name)
        if op == 0x18 and body:
            flags = "execute" if body[0] else "cancel"
            return [(level, f"{who} EXECUTE WRITE ({flags})")]
        if op in (0x1B, 0x1D) and len(body) >= 2:
            handle = int.from_bytes(body[:2], "little")
            target = self._describe(handle) if not from_peer else f"0x{handle:04x}"
            return [(INFO if not from_peer else DEBUG,
                     f"{who} {name} {target}: {_value(body[2:])}")]
        if op == 0x01 and len(body) >= 4:
            failed_op, handle, code = body[0], int.from_bytes(body[1:3], "little"), body[3]
            failed = OPCODES.get(failed_op, f"0x{failed_op:02x}")
            routine = code == 0x0A and failed_op in DISCOVERY_REQUESTS
            link.request.pop(not from_peer, None)
            target = self._describe(handle) if not from_peer else f"0x{handle:04x}"
            line = (f"{who} ERROR for {failed} {target}: 0x{code:02x} "
                    f"{ATT_ERRORS.get(code, 'application error')}")
            if routine or from_peer:
                return [(DEBUG, line)]
            return [(WARNING, line)]
        if op in (0x13, 0x17, 0x19, 0x1E):
            link.request.pop(not from_peer, None)
            return [(DEBUG, f"{who} {name}")]
        return [(level, f"{who} {name} {_value(body)}")]

    def _discovery_response(self, link: _Link, op: int, body: bytes,
                            from_peer: bool, who: str) -> list[Line]:
        request = link.request.pop(not from_peer, (None, b""))
        entries: list[str] = []
        if op == 0x11 and body:
            size = body[0]
            for i in range(1, len(body) - size + 1, size):
                handle, end = struct.unpack_from("<HH", body, i)
                uuid = format_uuid(body[i + 4:i + size])
                entries.append(f"0x{handle:04x}..0x{end:04x} {uuid}")
                if not from_peer:
                    self.handles[handle] = f"service {uuid}"
        elif op == 0x09 and body:
            size = body[0]
            declaration = (request[0] == 0x08 and len(request[1]) == 6
                           and request[1][4:6] == b"\x03\x28")
            for i in range(1, len(body) - size + 1, size):
                handle = int.from_bytes(body[i:i + 2], "little")
                value = body[i + 2:i + size]
                if declaration and len(value) >= 5:
                    props = value[0]
                    value_handle = int.from_bytes(value[1:3], "little")
                    uuid = format_uuid(value[3:])
                    entries.append(f"0x{value_handle:04x} {uuid} props 0x{props:02x}")
                    if not from_peer:
                        self.handles[value_handle] = uuid
                        self._char_values[value_handle] = uuid.lower()
                else:
                    entries.append(f"0x{handle:04x} {_value(value)}")
        elif op == 0x05 and body:
            width = 2 if body[0] == 1 else 16
            for i in range(1, len(body) - width - 1, width + 2):
                handle = int.from_bytes(body[i:i + 2], "little")
                uuid = format_uuid(body[i + 2:i + 2 + width])
                entries.append(f"0x{handle:04x} {uuid}")
                if not from_peer:
                    self.handles.setdefault(handle, uuid)
        elif op == 0x07:
            for i in range(0, len(body) - 3, 4):
                handle, end = struct.unpack_from("<HH", body, i)
                entries.append(f"0x{handle:04x}..0x{end:04x}")
        level = INFO if not from_peer else DEBUG
        return [(level, f"{who} {OPCODES[op]}: " + ("; ".join(entries) or "(none)"))]

    def _write(self, link: _Link, op: int, body: bytes, from_peer: bool,
               who: str, level: int, name: str) -> list[Line]:
        handle = int.from_bytes(body[:2], "little")
        value = body[2:]
        if op == 0x16:
            offset = int.from_bytes(value[:2], "little")
            return [(level, f"{who} PREPARE WRITE {self._describe(handle)} "
                     f"offset {offset}: {_value(value[2:])}")]
        if op == 0xD2:
            value = value[:-12]
        uuid = self.handles.get(handle, "")
        if from_peer and "0x2902" in uuid and len(value) >= 2:
            flags = int.from_bytes(value[:2], "little")
            what = {0: "UNSUBSCRIBE", 1: "SUBSCRIBE notify",
                    2: "SUBSCRIBE indicate", 3: "SUBSCRIBE notify+indicate"}.get(
                        flags, f"CCCD 0x{flags:04x}")
            char = self._char_for_cccd(handle) or "unknown characteristic"
            return [(INFO, f"{who} {what} on {char} (CCCD 0x{handle:04x})")]
        if from_peer and self._quiet(handle):
            level = DEBUG
        target = self._describe(handle) if from_peer else f"0x{handle:04x}"
        return [(level, f"{who} {name} {target}: {_value(value, 24)}")]


class AttMonitor:
    """Async reader: monitor socket -> :class:`AttLogParser` -> logger."""

    def __init__(self, adapter_index: int, quiet_uuids: Iterable[str] = (),
                 emit: Callable[[int, str], None] | None = None) -> None:
        self._parser = AttLogParser(adapter_index, quiet_uuids)
        self._emit = emit or (lambda level, line: logger.log(level, "%s", line))
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
        try:
            while True:
                packet = await loop.sock_recv(self._socket, 4096)
                if not packet:
                    return
                for level, line in self._parser.feed(packet):
                    self._emit(level, line)
        finally:
            self.close()
