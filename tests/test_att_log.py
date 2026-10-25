"""ATT/link log parser, fed with synthetic HCI monitor packets.

The packets follow the Linux monitor format (opcode, index, length) with
HCI ACL data (handle + boundary flags, L2CAP basic header, ATT PDU) and
HCI events, as the kernel duplicates them on the monitor channel.
"""

import logging
import struct

from ble.att_log import AttLogParser, format_uuid

ADDRESS = bytes.fromhex("ffeeddccbbaa")  # AA:BB:CC:DD:EE:FF, LSB first
HANDLE = 0x0040
SERVICE = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
WRITE_CHAR = "beb5483e-36e1-4688-b7f5-ea07361b26a8"


def uuid128(text: str) -> bytes:
    return bytes.fromhex(text.replace("-", ""))[::-1]


def mon(opcode: int, data: bytes, index: int = 0) -> bytes:
    return struct.pack("<HHH", opcode, index, len(data)) + data


def acl(pdu: bytes, cid: int = 4, boundary: int = 0b10) -> bytes:
    l2cap = struct.pack("<HH", len(pdu), cid) + pdu
    return struct.pack("<HH", HANDLE | boundary << 12, len(l2cap)) + l2cap


def rx(pdu: bytes, cid: int = 4) -> bytes:   # the peer sent it
    return mon(5, acl(pdu, cid))


def tx(pdu: bytes) -> bytes:                 # the simulator sent it
    return mon(4, acl(pdu, boundary=0b00))


def connected() -> bytes:
    params = (bytes([0x01, 0x00]) + struct.pack("<H", HANDLE) + bytes([1, 1])
              + ADDRESS + struct.pack("<HHH", 24, 0, 500) + bytes([0]))
    return mon(3, bytes([0x3E, len(params)]) + params)


def lines(parser: AttLogParser, *packets: bytes) -> list[tuple[int, str]]:
    out = []
    for packet in packets:
        out.extend(parser.feed(packet))
    return out


def discovered(parser: AttLogParser) -> list[tuple[int, str]]:
    """Connect, exchange MTU and discover one service like Android does."""
    group = struct.pack("<HH", 0x0001, 0x0005) + uuid128(SERVICE)
    decl = (struct.pack("<H", 0x0002) + bytes([0x0E]) + struct.pack("<H", 0x0003)
            + uuid128(WRITE_CHAR))
    return lines(
        parser, connected(),
        rx(bytes([0x02]) + struct.pack("<H", 517)),
        tx(bytes([0x03]) + struct.pack("<H", 517)),
        rx(bytes([0x10]) + struct.pack("<HHH", 1, 0xFFFF, 0x2800)),
        tx(bytes([0x11, len(group)]) + group),
        rx(bytes([0x08]) + struct.pack("<HHH", 1, 5, 0x2803)),
        tx(bytes([0x09, len(decl)]) + decl),
        rx(bytes([0x04]) + struct.pack("<HH", 4, 5)),
        tx(bytes([0x05, 0x01]) + struct.pack("<HH", 0x0004, 0x2902)),
    )


def test_connection_mtu_and_discovery_are_logged() -> None:
    parser = AttLogParser(0, quiet_uuids=[WRITE_CHAR])
    text = [line for _level, line in discovered(parser)]
    assert text[0] == ("LINK AA:BB:CC:DD:EE:FF connected (handle 0x040, we are "
                       "peripheral, address type 1, interval 30 ms, latency 0, "
                       "timeout 5000 ms)")
    assert "ATT AA:BB:CC:DD:EE:FF peer→sim MTU request 517" in text
    assert "ATT AA:BB:CC:DD:EE:FF sim→peer MTU response 517, ATT MTU now 517" in text
    assert any("discover services 0x0001..0xffff 0x2800 (Primary Service)" in t
               for t in text)
    assert any(f"READ BY GROUP RSP: 0x0001..0x0005 {SERVICE}" in t for t in text)
    assert any(f"0x0003 {WRITE_CHAR} props 0x0e" in t for t in text)
    assert parser.handles[0x0003] == WRITE_CHAR
    assert parser.handles[0x0004] == "0x2902 (CCCD)"


def test_subscription_read_and_quiet_write() -> None:
    parser = AttLogParser(0, quiet_uuids=[WRITE_CHAR])
    discovered(parser)
    out = lines(
        parser,
        rx(bytes([0x12]) + struct.pack("<H", 0x0004) + b"\x01\x00"),
        rx(bytes([0x0A]) + struct.pack("<H", 0x0003)),
        tx(bytes([0x0B]) + b"hello"),
        rx(bytes([0x52]) + struct.pack("<H", 0x0003) + b"-"),
    )
    assert out[0] == (logging.INFO, "ATT AA:BB:CC:DD:EE:FF peer→sim SUBSCRIBE "
                      f"notify on {WRITE_CHAR} (CCCD 0x0004)")
    assert out[1] == (logging.INFO, "ATT AA:BB:CC:DD:EE:FF peer→sim READ "
                      f"0x0003 ({WRITE_CHAR})")
    assert out[2][1].endswith(f"READ RSP of 0x0003 ({WRITE_CHAR}): 5 B 68656c6c6f 'hello'")
    # The session logs protocol writes already; their ATT line is DEBUG.
    assert out[3][0] == logging.DEBUG and "WRITE CMD" in out[3][1]


def test_requests_for_missing_attributes_are_warnings() -> None:
    parser = AttLogParser(0)
    discovered(parser)
    out = lines(parser,
                rx(bytes([0x0A]) + struct.pack("<H", 0x0009)),
                tx(bytes([0x01, 0x0A]) + struct.pack("<H", 0x0009) + b"\x01"))
    assert out[-1] == (logging.WARNING, "ATT AA:BB:CC:DD:EE:FF sim→peer ERROR for "
                       "READ REQ 0x0009: 0x01 Invalid Handle")


def test_end_of_discovery_error_is_routine() -> None:
    parser = AttLogParser(0)
    out = lines(parser, connected(),
                rx(bytes([0x10]) + struct.pack("<HHH", 6, 0xFFFF, 0x2800)),
                tx(bytes([0x01, 0x10]) + struct.pack("<H", 6) + b"\x0a"))
    assert out[-1][0] == logging.DEBUG and "Attribute Not Found" in out[-1][1]


def test_fragmented_acl_is_reassembled() -> None:
    parser = AttLogParser(0)
    discovered(parser)
    pdu = bytes([0x12]) + struct.pack("<H", 0x0003) + b".2+4+6+8+0+0+0+0+0+0."
    l2cap = struct.pack("<HH", len(pdu), 4) + pdu
    first = struct.pack("<HH", HANDLE | 0b10 << 12, 10) + l2cap[:10]
    rest = struct.pack("<HH", HANDLE | 0b01 << 12, len(l2cap) - 10) + l2cap[10:]
    assert parser.feed(mon(5, first)) == []
    ((level, line),) = parser.feed(mon(5, rest))
    assert level == logging.INFO
    assert f"WRITE REQ 0x0003 ({WRITE_CHAR}): 21 B" in line


def test_prepare_execute_and_notification() -> None:
    parser = AttLogParser(0)
    discovered(parser)
    out = lines(parser,
                rx(bytes([0x16]) + struct.pack("<HH", 0x0003, 0) + b".2+4"),
                rx(bytes([0x18, 0x01])),
                tx(bytes([0x1B]) + struct.pack("<H", 0x0003) + b"ok"))
    assert "PREPARE WRITE 0x0003" in out[0][1] and "offset 0" in out[0][1]
    assert out[1][1].endswith("EXECUTE WRITE (execute)")
    assert out[2][1].endswith(f"NOTIFY 0x0003 ({WRITE_CHAR}): 2 B 6f6b 'ok'")


def test_disconnect_reason_smp_and_other_adapters() -> None:
    parser = AttLogParser(0)
    discovered(parser)
    smp = rx(bytes([0x01, 0x03, 0x00, 0x01, 0x10, 0x07, 0x07]), cid=6)
    params = bytes([0x00]) + struct.pack("<H", HANDLE) + bytes([0x13])
    out = lines(parser, smp, mon(3, bytes([0x05, len(params)]) + params),
                mon(5, acl(b"\x02\x05\x02"), index=1))
    assert out == [
        (logging.INFO, "SMP AA:BB:CC:DD:EE:FF: peer sent pairing request"),
        (logging.INFO, "LINK AA:BB:CC:DD:EE:FF disconnected (handle 0x040, "
                       "reason 0x13 Remote User Terminated)"),
    ]


def test_bluez_browsing_the_phone_is_debug() -> None:
    parser = AttLogParser(0)
    out = lines(parser, connected(),
                tx(bytes([0x10]) + struct.pack("<HHH", 1, 0xFFFF, 0x2800)),
                rx(bytes([0x11, 6]) + struct.pack("<HHH", 1, 7, 0x1800)))
    assert [level for level, _ in out[1:]] == [logging.DEBUG, logging.DEBUG]
    assert parser.handles == {}  # the phone's database is not ours


def test_uuid_formatting() -> None:
    assert format_uuid(b"\x02\x29") == "0x2902 (CCCD)"
    assert format_uuid(uuid128(SERVICE)) == SERVICE


def test_garbage_packets_are_ignored() -> None:
    parser = AttLogParser(0)
    assert parser.feed(b"\x00") == []
    assert parser.feed(mon(5, b"\x40")) == []
    assert parser.feed(mon(3, b"\x3e")) == []
    assert parser.feed(mon(5, struct.pack("<HH", HANDLE | 0b01 << 12, 2) + b"xx")) == []
