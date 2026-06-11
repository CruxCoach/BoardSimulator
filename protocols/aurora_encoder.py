"""Aurora Board packet encoder + climb-frames parsing (reference port).

Faithful Python port of CruxCoach 0.2.0's Kotlin encoding logic:

- `BoardPacketEncoder.kt`: hold list -> BLE-ready packet chunks, API level 3
  (3 bytes/hold, RGB332 colour) and API level 2 (2 bytes/hold, 10-bit
  position, 18 W power-budget brightness scaling — the BoardSesh aurora.ts
  spec, identical to the decompiled official apps' prepBytesV2).
- `BoardClimbParser.kt`: the delta frames string format
  ("p{placementId}r{roleId}…") used by the Aurora-era catalogues.

The simulator itself only *decodes*; this module exists so the tests can
round-trip exactly what CruxCoach (or an official brand app) would send,
and so the mock BLE client can drive a running simulator.
"""

from __future__ import annotations

import math
import re

from protocols.aurora_decoder import checksum

BLE_CHUNK_SIZE = 20

# Max payload bytes per protocol packet (255 max length byte - 1 type byte).
_MESSAGE_BODY_MAX = 255
_MAX_HOLDS_PER_PACKET_V3 = 84   # 254 / 3 bytes per hold
_MAX_HOLDS_PER_PACKET_V2 = 127  # 254 / 2 bytes per LED

# API Level 3 packet types
API3_FIRST = 0x52   # 'R'
API3_MIDDLE = 0x51  # 'Q'
API3_LAST = 0x53    # 'S'
API3_ONLY = 0x54    # 'T'

# API Level 2 packet types
API2_FIRST = 0x4E   # 'N'
API2_MIDDLE = 0x4D  # 'M'
API2_LAST = 0x4F    # 'O'
API2_ONLY = 0x50    # 'P'

# ── Colour encoding (API level 3, RGB332) ───────────────────────────────────


def encode_color(r: int, g: int, b: int) -> int:
    """8-bit RGB -> RGB332 board colour byte (r3<<5 | g3<<2 | b2)."""
    r3 = min(max(r // 32, 0), 7)
    g3 = min(max(g // 32, 0), 7)
    b2 = min(max(b // 64, 0), 3)
    return (r3 << 5) | (g3 << 2) | b2


def hex_to_color_byte(hex_color: str | None) -> int | None:
    """Parse 'RRGGBB' / '#RRGGBB' (placement_roles.led_color) to RGB332.

    Returns None for absent/malformed values (caller falls back to white,
    like the official apps do).
    """
    if hex_color is None:
        return None
    h = hex_color.strip().removeprefix("#")
    if len(h) < 6:
        return None
    try:
        return encode_color(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return None


# ── API level 2 power-budget scaling ────────────────────────────────────────

_V2_POWER_SCALES = (1.0, 0.8, 0.6, 0.4, 0.2, 0.1, 0.05)
_V2_MAX_BOARD_POWER = 18.0
V2_MAX_POSITION = 1023  # 10-bit position field


def rgb332_to_rgb888(color: int) -> tuple[int, int, int]:
    """RGB332 colour byte -> 8-bit channels via bit replication.

    Palette colours round-trip exactly: encode_color(0,255,0)=0x1C -> (0,255,0).
    """
    r3 = (color >> 5) & 0x07
    g3 = (color >> 2) & 0x07
    b2 = color & 0x03
    r8 = (r3 << 5) | (r3 << 2) | (r3 >> 1)
    g8 = (g3 << 5) | (g3 << 2) | (g3 >> 1)
    b8 = (b2 << 6) | (b2 << 4) | (b2 << 2) | b2
    return (r8, g8, b8)


def scaled_color_v2(value_8bit: int, scale: float) -> int:
    """floor(value * scale) >> 6 -> 0..3 (BoardSesh scaledColorV2)."""
    return int(math.floor(value_8bit * scale)) >> 6


def compute_v2_scale(colors: list[int], leds_per_hold: int) -> float:
    """Largest brightness scale fitting the 18 W v2 power budget.

    `colors` are RGB332 bytes; tries each scale until
    leds_per_hold * sum((r+g+b)/30) <= 18, else 0.0 (all LEDs off).
    """
    for scale in _V2_POWER_SCALES:
        total_power = 0.0
        for color in colors:
            r8, g8, b8 = rgb332_to_rgb888(color)
            total_power += (
                scaled_color_v2(r8, scale)
                + scaled_color_v2(g8, scale)
                + scaled_color_v2(b8, scale)
            ) / 30.0
        if leds_per_hold * total_power <= _V2_MAX_BOARD_POWER:
            return scale
    return 0.0


def encode_position_and_color_v2(position: int, color: int,
                                 scale: float) -> tuple[int, int] | None:
    """One v2 LED as 2 bytes: (posLo, (r2<<6)|(g2<<4)|(b2<<2)|posHi).

    None when the position exceeds the 10-bit field (caller skips the hold).
    """
    if position > V2_MAX_POSITION:
        return None
    pos_lo = position & 0xFF
    pos_hi = (position >> 8) & 0x03
    r8, g8, b8 = rgb332_to_rgb888(color)
    color_byte = (
        (scaled_color_v2(r8, scale) << 6)
        | (scaled_color_v2(g8, scale) << 4)
        | (scaled_color_v2(b8, scale) << 2)
        | pos_hi
    )
    return (pos_lo, color_byte)


# ── Packet building ─────────────────────────────────────────────────────────


def _build_packet(payload: list[int]) -> bytes:
    """[0x01][len][checksum][0x02] + payload + [0x03]."""
    return bytes([0x01, len(payload) & 0xFF, checksum(payload), 0x02]
                 + payload + [0x03])


def _chunk(packet: bytes) -> list[bytes]:
    """Split a packet into BLE-MTU-sized (20-byte) write chunks."""
    return [packet[i:i + BLE_CHUNK_SIZE]
            for i in range(0, len(packet), BLE_CHUNK_SIZE)]


def _typed_packets(chunks: list[list[int]], first: int, middle: int,
                   last: int, only: int) -> list[bytes]:
    """Wrap payload chunks into packets with FIRST/MIDDLE/LAST/ONLY types."""
    packets: list[bytes] = []
    for i, body in enumerate(chunks):
        if len(chunks) == 1:
            ptype = only
        elif i == 0:
            ptype = first
        elif i == len(chunks) - 1:
            ptype = last
        else:
            ptype = middle
        packets.append(_build_packet([ptype] + body))
    return packets


def encode_climb_v3(holds: list[tuple[int, int]]) -> list[bytes]:
    """Encode (led_position, rgb332_color) pairs as API level 3 BLE chunks."""
    hold_bytes: list[int] = []
    for pos, color in holds:
        hold_bytes += [pos & 0xFF, (pos >> 8) & 0xFF, color & 0xFF]

    chunk_size = _MAX_HOLDS_PER_PACKET_V3 * 3
    bodies = [hold_bytes[i:i + chunk_size]
              for i in range(0, len(hold_bytes), chunk_size)] or [[]]
    packets = _typed_packets(bodies, API3_FIRST, API3_MIDDLE, API3_LAST, API3_ONLY)
    return [chunk for packet in packets for chunk in _chunk(packet)]


def encode_climb_v2(holds: list[tuple[int, int]],
                    leds_per_hold: int = 1) -> list[bytes]:
    """Encode (led_position, rgb332_color) pairs as API level 2 BLE chunks.

    Applies the 18 W power-budget brightness scale once across the whole
    send; positions beyond the 10-bit field are skipped.
    """
    if not holds:
        return _chunk(_build_packet([API2_ONLY]))

    scale = compute_v2_scale([color for _, color in holds], leds_per_hold)
    led_bytes: list[int] = []
    for pos, color in holds:
        encoded = encode_position_and_color_v2(pos, color, scale)
        if encoded is None:
            continue
        led_bytes += list(encoded)
    if not led_bytes:
        return _chunk(_build_packet([API2_ONLY]))

    chunk_size = _MAX_HOLDS_PER_PACKET_V2 * 2
    bodies = [led_bytes[i:i + chunk_size]
              for i in range(0, len(led_bytes), chunk_size)]
    packets = _typed_packets(bodies, API2_FIRST, API2_MIDDLE, API2_LAST, API2_ONLY)
    return [chunk for packet in packets for chunk in _chunk(packet)]


def encode_climb(holds: list[tuple[int, int]], api_level: int = 3,
                 leds_per_hold: int = 1) -> list[bytes]:
    """Encode holds for the given API level (see encode_climb_v2/_v3)."""
    if api_level < 3:
        return encode_climb_v2(holds, leds_per_hold)
    return encode_climb_v3(holds)


# ── Climb frames strings (BoardClimbParser.kt, delta format) ────────────────

_DELTA_PATTERN = re.compile(r"p(\d+)r(\d+)")


def parse_frames(frames: str) -> list[tuple[int, int]]:
    """Parse a delta frames string ("p{placementId}r{roleId}…") into
    (placement_id, role_id) tuples. Role ids are kept brand-native
    (Aurora-family boards use 1-4 / Tension Board 2 uses 5-8)."""
    return [(int(p), int(r)) for p, r in _DELTA_PATTERN.findall(frames or "")]


def encode_frames(holds: list[tuple[int, int]]) -> str:
    """Encode (placement_id, role_id) tuples back to a delta frames string."""
    return "".join(f"p{placement}r{role}" for placement, role in holds)
