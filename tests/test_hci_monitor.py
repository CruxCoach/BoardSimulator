"""Linux HCI monitor parsing used by simultaneous two-board mode."""

import struct

from ble.hci_monitor import (AdvertisingConnection, Disconnection,
                             HciEventParser)


def monitor_packet(data: bytes, adapter: int = 0) -> bytes:
    return struct.pack("<HHH", 3, adapter, len(data)) + data


def enhanced_connection(handle: int = 0x0040) -> bytes:
    # Peer address is little-endian on the HCI wire.
    parameters = bytes([
        0x0a, 0x00, handle & 0xff, handle >> 8, 0x01, 0x01,
        0x66, 0x55, 0x44, 0x33, 0x22, 0x11,
        *([0x00] * 18),
    ])
    return monitor_packet(bytes([0x3e, len(parameters)]) + parameters)


def advertising_terminated(handle: int = 0x0040, adv_handle: int = 2) -> bytes:
    parameters = bytes([
        0x12, 0x00, adv_handle, handle & 0xff, handle >> 8, 0x00,
    ])
    return monitor_packet(bytes([0x3e, len(parameters)]) + parameters)


def test_joins_connection_and_advertising_handle_in_either_order() -> None:
    parser = HciEventParser()
    assert parser.feed(enhanced_connection(), 0) == []
    assert parser.feed(advertising_terminated(), 0) == [
        AdvertisingConnection(2, 0x0040, "11:22:33:44:55:66")]

    parser = HciEventParser()
    assert parser.feed(advertising_terminated(), 0) == []
    assert parser.feed(enhanced_connection(), 0) == [
        AdvertisingConnection(2, 0x0040, "11:22:33:44:55:66")]


def test_filters_other_adapters_and_parses_disconnect() -> None:
    parser = HciEventParser()
    assert parser.feed(enhanced_connection(), 1) == []

    parameters = bytes([0x00, 0x40, 0x00, 0x13])
    packet = monitor_packet(bytes([0x05, len(parameters)]) + parameters)
    assert parser.feed(packet, 0) == [Disconnection(0x0040, 0x13)]
