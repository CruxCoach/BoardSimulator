"""Round-trip tests: encoder (CruxCoach BoardPacketEncoder port) -> decoder.

Fixture values are ported from CruxCoach 0.2.0's Kotlin sources
(BoardPacketEncoder.kt / BoardClimbParser.kt) and the decompiled official
brand apps (BluetoothServiceKt.prepBytesV3 / prepBytesV2), so a green run
means the simulator decodes exactly what a real client sends.
"""

import protocols.aurora_encoder as encoder
from protocols.aurora_encoder import (
    compute_v2_scale,
    encode_climb,
    encode_color,
    encode_frames,
    encode_position_and_color_v2,
    hex_to_color_byte,
    parse_frames,
    rgb332_to_rgb888,
    scaled_color_v2,
)
from protocols.aurora_decoder import ProtocolDecoder


def decode_all(chunks: list[bytes], api_level: int) -> list[list[tuple[int, int, int, int]]]:
    """Feed encoder output through the decoder, return all decoded messages."""
    received: list[list[tuple[int, int, int, int]]] = []
    decoder = ProtocolDecoder(api_level=api_level, on_message=received.append)
    for chunk in chunks:
        decoder.feed(chunk)
    return received


# --- Colour byte encoding (BoardPacketEncoder.encodeColor fixtures) ---

class TestEncodeColor:
    def test_kilter_palette_constants(self) -> None:
        # COLOR_START/HAND/FINISH/FOOT constants from BoardPacketEncoder.kt
        assert encode_color(0x00, 0xFF, 0x00) == 0x1C  # green
        assert encode_color(0x00, 0xFF, 0xFF) == 0x1F  # cyan
        assert encode_color(0xFF, 0x00, 0xFF) == 0xE3  # magenta
        assert encode_color(0xFF, 0xA5, 0x00) == 0xF4  # orange

    def test_aurora_family_role_colors(self) -> None:
        # The five boards' placement_roles.led_color values, all of which
        # must survive the RGB332 quantisation distinctly.
        assert hex_to_color_byte("00FF00") == 0x1C  # start (green)
        assert hex_to_color_byte("0000FF") == 0x03  # middle (blue)
        assert hex_to_color_byte("FF0000") == 0xE0  # finish (red)
        assert hex_to_color_byte("FF00FF") == 0xE3  # foot / So iLL middle (magenta)
        assert hex_to_color_byte("FFFFFF") == 0xFF  # So iLL finish (white)
        assert hex_to_color_byte("00FFFF") == 0x1F  # So iLL foot (cyan)

    def test_hex_parsing_variants(self) -> None:
        assert hex_to_color_byte("#00FF00") == 0x1C
        assert hex_to_color_byte(None) is None
        assert hex_to_color_byte("xyz") is None
        assert hex_to_color_byte("12345") is None

    def test_rgb332_bit_replication_roundtrip(self) -> None:
        # Palette colours round-trip exactly through RGB332 (Kotlin
        # rgb332ToRgb888 doc example).
        assert rgb332_to_rgb888(0x1C) == (0, 255, 0)
        assert rgb332_to_rgb888(0xFF) == (255, 255, 255)
        assert rgb332_to_rgb888(0x00) == (0, 0, 0)


# --- API level 3 packets ---

class TestEncodeV3:
    def test_single_hold_packet_bytes(self) -> None:
        # One hold, LED 261, white: payload = [T, 0x05, 0x01, 0xFF]
        chunks = encode_climb([(261, 0xFF)], api_level=3)
        assert len(chunks) == 1
        packet = chunks[0]
        assert packet[0] == 0x01           # start marker
        assert packet[1] == 4              # payload length
        assert packet[3] == 0x02           # separator
        assert packet[4] == 0x54           # 'T' = only packet
        assert packet[5:8] == bytes([0x05, 0x01, 0xFF])
        assert packet[-1] == 0x03          # end marker

    def test_roundtrip_small_climb(self) -> None:
        holds = [(1, encode_color(0, 255, 0)),    # start, green
                 (143, encode_color(0, 0, 255)),  # middle, blue
                 (577, encode_color(255, 0, 0))]  # finish, red
        messages = decode_all(encode_climb(holds, api_level=3), api_level=3)
        assert len(messages) == 1
        assert [(pos, (r, g, b)) for pos, r, g, b in messages[0]] == [
            (1, (0, 255, 0)),
            (143, (0, 0, 255)),
            (577, (255, 0, 0)),
        ]

    def test_roundtrip_multi_packet(self) -> None:
        """>84 holds split into R/S packets; decoder reassembles them."""
        holds = [(pos, 0x1C) for pos in range(100)]
        chunks = encode_climb(holds, api_level=3)
        messages = decode_all(chunks, api_level=3)
        assert len(messages) == 1
        assert [h[0] for h in messages[0]] == list(range(100))

    def test_roundtrip_empty_climb_clears(self) -> None:
        messages = decode_all(encode_climb([], api_level=3), api_level=3)
        assert messages == [[]]

    def test_chunks_respect_ble_mtu(self) -> None:
        holds = [(pos, 0xFF) for pos in range(200)]
        for chunk in encode_climb(holds, api_level=3):
            assert len(chunk) <= 20


# --- API level 2 packets (power-budget scaling) ---

class TestEncodeV2:
    def test_scaled_color(self) -> None:
        # BoardSesh scaledColorV2: floor(value * scale) >> 6
        assert scaled_color_v2(255, 1.0) == 3
        assert scaled_color_v2(255, 0.4) == 1
        assert scaled_color_v2(0, 1.0) == 0

    def test_scale_full_for_small_climb(self) -> None:
        # A handful of holds is far below 18 W -> full brightness.
        assert compute_v2_scale([0x1C, 0xE0, 0x03], leds_per_hold=1) == 1.0

    def test_scale_drops_for_huge_climb(self) -> None:
        # 600 white LEDs: 600 * 9/30 = 180 W >> 18 W -> scale must drop.
        scale = compute_v2_scale([0xFF] * 600, leds_per_hold=1)
        assert scale < 1.0

    def test_kilter_two_leds_halve_the_budget(self) -> None:
        # Same colours need a lower scale at ledsPerHold=2 than at 1.
        colors = [0xFF] * 70
        assert compute_v2_scale(colors, 2) <= compute_v2_scale(colors, 1)

    def test_position_beyond_10_bits_skipped(self) -> None:
        assert encode_position_and_color_v2(1024, 0xFF, 1.0) is None

    def test_roundtrip_small_climb(self) -> None:
        holds = [(1, encode_color(0, 255, 0)), (322, encode_color(255, 0, 0))]
        messages = decode_all(encode_climb(holds, api_level=2), api_level=2)
        assert len(messages) == 1
        decoded = messages[0]
        assert [h[0] for h in decoded] == [1, 322]
        # Full scale: green stays green, red stays red (2-bit channels).
        assert decoded[0][1:] == (0, 255, 0)
        assert decoded[1][1:] == (255, 0, 0)

    def test_roundtrip_empty_climb_clears(self) -> None:
        messages = decode_all(encode_climb([], api_level=2), api_level=2)
        assert messages == [[]]


# --- Frames strings (BoardClimbParser.kt fixtures) ---

class TestFrames:
    def test_parse_delta_format(self) -> None:
        # Example from BoardClimbParser's docs: "p1091r15p1096r15p1163r12"
        assert parse_frames("p1091r15p1096r15p1163r12") == [
            (1091, 15), (1096, 15), (1163, 12),
        ]

    def test_aurora_family_role_ids_kept_native(self) -> None:
        # Aurora-family boards use board-local role ids 1-4 (TB2: 5-8);
        # they must NOT be normalised to Kilter's 12-15.
        assert parse_frames("p100r1p200r2p300r3p400r4") == [
            (100, 1), (200, 2), (300, 3), (400, 4),
        ]
        assert parse_frames("p10r5p20r6p30r7p40r8") == [
            (10, 5), (20, 6), (30, 7), (40, 8),
        ]

    def test_empty_and_blank(self) -> None:
        assert parse_frames("") == []
        assert parse_frames(None) == []

    def test_encode_roundtrip(self) -> None:
        holds = [(1091, 1), (1096, 2), (1163, 3)]
        assert parse_frames(encode_frames(holds)) == holds
