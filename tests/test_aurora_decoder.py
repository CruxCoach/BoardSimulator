"""Tests for the Aurora Board protocol decoder (shared across all brands)."""

from protocols.aurora_decoder import (
    ProtocolDecoder,
    checksum,
    _decode_color_v2,
    _decode_color_v3,
    _decode_holds_v2,
    _decode_holds_v3,
    POS_ONLY_V2,
    POS_ONLY_V3,
    POS_FIRST_V3,
    POS_LAST_V3,
    START_MARKER,
    SEPARATOR,
    END_MARKER,
)


def _build_packet(pos_code: int, hold_data: list[int]) -> bytes:
    """Build a valid packet from position code and raw hold data bytes."""
    data_region = [pos_code] + hold_data
    cs = checksum(data_region)
    packet = [START_MARKER, len(data_region), cs, SEPARATOR] + data_region + [END_MARKER]
    return bytes(packet)


# --- Checksum ---

class TestChecksum:
    def test_empty(self) -> None:
        assert checksum([]) == 0xFF

    def test_known_value(self) -> None:
        # ~(1+2+3) & 0xFF = ~6 & 0xFF = 0xF9
        assert checksum([1, 2, 3]) == 0xF9

    def test_wraps_to_8_bits(self) -> None:
        assert checksum([0xFF, 0xFF]) == (~0xFE) & 0xFF

    def test_single_byte(self) -> None:
        # sum=0x42, ~0x42 & 0xFF = 0xBD
        assert checksum([0x42]) == 0xBD

    def test_overflow(self) -> None:
        # 0x80 + 0x80 = 0x100, masked to 0x00 -> ~0x00 & 0xFF = 0xFF
        assert checksum([0x80, 0x80]) == 0xFF

    def test_all_ones(self) -> None:
        # 0xFF -> sum=0xFF, ~0xFF & 0xFF = 0x00
        assert checksum([0xFF]) == 0x00

    def test_known_sequence(self) -> None:
        # Sum: 1+2+3+4+5 = 15 = 0x0F, ~0x0F & 0xFF = 0xF0
        assert checksum([1, 2, 3, 4, 5]) == 0xF0

    def test_roundtrip(self) -> None:
        """Adding the checksum byte to the data should produce checksum 0."""
        data = [10, 20, 30, 40]
        cs = checksum(data)
        assert checksum(data + [cs]) == 0x00


# --- Color decoding ---

class TestColorV2:
    def test_zero(self) -> None:
        assert _decode_color_v2(0b00000000) == (0, 0, 0)

    def test_max(self) -> None:
        assert _decode_color_v2(0b11111100) == (255, 255, 255)

    def test_channels(self) -> None:
        assert _decode_color_v2(0b11000000) == (255, 0, 0)
        assert _decode_color_v2(0b00110000) == (0, 255, 0)
        assert _decode_color_v2(0b00001100) == (0, 0, 255)


class TestColorV3:
    def test_zero(self) -> None:
        assert _decode_color_v3(0b00000000) == (0, 0, 0)

    def test_max(self) -> None:
        assert _decode_color_v3(0b11111111) == (255, 255, 255)

    def test_channels(self) -> None:
        assert _decode_color_v3(0b11100000) == (255, 0, 0)
        assert _decode_color_v3(0b00011100) == (0, 255, 0)
        assert _decode_color_v3(0b00000011) == (0, 0, 255)


# --- Hold decoding ---

class TestDecodeHoldsV2:
    def test_single_hold(self) -> None:
        # Position = 0x42 + (0b01 << 8) = 322
        # Color byte 0b11_01_00_01: red=3, green=1, blue=0, pos_high=01
        holds = _decode_holds_v2([0x42, 0b11010001])
        assert holds == [(0x42 + (0b01 << 8), 255, 85, 0)]

    def test_trailing_byte_ignored(self) -> None:
        holds = _decode_holds_v2([0x42, 0b11010001, 0x99])
        assert len(holds) == 1


class TestDecodeHoldsV3:
    def test_single_hold(self) -> None:
        holds = _decode_holds_v3([0x05, 0x01, 0xFF])
        assert holds == [(261, 255, 255, 255)]

    def test_position_16_bit(self) -> None:
        holds = _decode_holds_v3([0x34, 0x12, 0x00])
        assert holds[0][0] == 0x1234


# --- Full decoder ---

class TestProtocolDecoder:
    def test_single_packet_v3(self) -> None:
        received: list[list[tuple[int, int, int, int]]] = []
        decoder = ProtocolDecoder(api_level=3, on_message=received.append)

        decoder.feed(_build_packet(POS_ONLY_V3, [0x05, 0x01, 0xFF]))

        assert received == [[(261, 255, 255, 255)]]

    def test_single_packet_v2(self) -> None:
        received: list[list[tuple[int, int, int, int]]] = []
        decoder = ProtocolDecoder(api_level=2, on_message=received.append)

        decoder.feed(_build_packet(POS_ONLY_V2, [0x42, 0b11010001]))

        assert len(received) == 1
        assert received[0][0][0] == 0x42 + (0b01 << 8)

    def test_multi_packet_v3(self) -> None:
        """Two-packet message should accumulate holds before the callback."""
        received: list[list[tuple[int, int, int, int]]] = []
        decoder = ProtocolDecoder(api_level=3, on_message=received.append)

        decoder.feed(_build_packet(POS_FIRST_V3, [0x01, 0x00, 0b11100000]))
        assert received == []  # not yet complete

        decoder.feed(_build_packet(POS_LAST_V3, [0x02, 0x00, 0b00011100]))
        assert len(received) == 1
        assert len(received[0]) == 2

    def test_empty_message_clears_board(self) -> None:
        """An ONLY packet with no holds (CruxCoach encodeClear) fires the
        callback with an empty list so the board clears."""
        received: list[list[tuple[int, int, int, int]]] = []
        decoder = ProtocolDecoder(api_level=3, on_message=received.append)

        decoder.feed(_build_packet(POS_ONLY_V3, []))

        assert received == [[]]

    def test_bad_checksum_discards(self) -> None:
        received: list[list[tuple[int, int, int, int]]] = []
        decoder = ProtocolDecoder(api_level=3, on_message=received.append)

        packet = bytearray(_build_packet(POS_ONLY_V3, [0x01, 0x00, 0xFF]))
        packet[2] ^= 0x01  # corrupt checksum
        decoder.feed(bytes(packet))

        assert received == []

    def test_chunked_delivery(self) -> None:
        """Data delivered byte-by-byte should still reassemble correctly."""
        received: list[list[tuple[int, int, int, int]]] = []
        decoder = ProtocolDecoder(api_level=3, on_message=received.append)

        for byte in _build_packet(POS_ONLY_V3, [0x05, 0x01, 0xFF]):
            decoder.feed(bytes([byte]))

        assert len(received) == 1

    def test_multiple_messages(self) -> None:
        """After one complete message, a new one should start fresh."""
        received: list[list[tuple[int, int, int, int]]] = []
        decoder = ProtocolDecoder(api_level=3, on_message=received.append)

        pkt1 = _build_packet(POS_ONLY_V3, [0x01, 0x00, 0xFF])
        pkt2 = _build_packet(POS_ONLY_V3, [0x02, 0x00, 0b11100000])
        decoder.feed(pkt1 + pkt2)

        assert len(received) == 2
        assert received[0][0][0] == 1
        assert received[1][0][0] == 2

    def test_garbage_before_start_marker_skipped(self) -> None:
        received: list[list[tuple[int, int, int, int]]] = []
        decoder = ProtocolDecoder(api_level=3, on_message=received.append)

        decoder.feed(b"\x42\x99" + _build_packet(POS_ONLY_V3, [0x05, 0x01, 0xFF]))

        assert len(received) == 1
