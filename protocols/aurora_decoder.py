"""Aurora Board protocol decoder for API Level 2 and 3.

The wire protocol is identical across the whole Aurora-Climbing ecosystem
(Kilter, Tension, Grasshopper, Decoy, So iLL, Touchstone) — consolidated from the
sibling Kilter/Aurora simulator projects and cross-checked against the decompiled
brand apps' BluetoothServiceKt (prepBytesV2/prepBytesV3/wrapBytes).

Handles chunk reassembly from 20-byte BLE writes, checksum validation,
and hold data decoding (LED position + RGB color per hold).
"""

import logging
from typing import Callable

logger = logging.getLogger(__name__)

# Packet framing bytes
START_MARKER: int = 0x01
SEPARATOR: int = 0x02
END_MARKER: int = 0x03

# Position codes: indicates where a packet sits in a multi-packet message
# API Level 2
POS_FIRST_V2: int = 78   # 'N'
POS_LAST_V2: int = 79    # 'O'
POS_MIDDLE_V2: int = 77  # 'M'
POS_ONLY_V2: int = 80    # 'P'

# API Level 3
POS_FIRST_V3: int = 82   # 'R'
POS_LAST_V3: int = 83    # 'S'
POS_MIDDLE_V3: int = 81  # 'Q'
POS_ONLY_V3: int = 84    # 'T'


def checksum(data: list[int]) -> int:
    """Calculate the checksum over a list of byte values.

    Sum all bytes (masked to 8 bits), then bitwise NOT masked to 8 bits.
    """
    s = 0
    for byte in data:
        s = (s + byte) & 0xFF
    return (~s) & 0xFF


def _decode_color_v2(color_byte: int) -> tuple[int, int, int]:
    """Decode 2-bit-per-channel color from API Level 2.

    Byte layout: bits [7:6]=Red, [5:4]=Green, [3:2]=Blue, [1:0]=position high bits.
    Each 2-bit value (0-3) maps to 0/85/170/255 via value/3*255.
    """
    blue = int(((color_byte & 0b00001100) >> 2) / 3.0 * 255.0)
    green = int(((color_byte & 0b00110000) >> 4) / 3.0 * 255.0)
    red = int(((color_byte & 0b11000000) >> 6) / 3.0 * 255.0)
    return (red, green, blue)


def _decode_color_v3(color_byte: int) -> tuple[int, int, int]:
    """Decode mixed-precision color from API Level 3.

    Byte layout: bits [7:5]=Red (3-bit), [4:2]=Green (3-bit), [1:0]=Blue (2-bit).
    3-bit values (0-7) -> value/7*255, 2-bit values (0-3) -> value/3*255.
    """
    blue = int(((color_byte & 0b00000011) >> 0) / 3.0 * 255.0)
    green = int(((color_byte & 0b00011100) >> 2) / 7.0 * 255.0)
    red = int(((color_byte & 0b11100000) >> 5) / 7.0 * 255.0)
    return (red, green, blue)


def _decode_holds_v2(data: list[int]) -> list[tuple[int, int, int, int]]:
    """Decode hold data from API Level 2 (2 bytes per hold).

    Returns list of (position, r, g, b) tuples.
    """
    holds: list[tuple[int, int, int, int]] = []
    for i in range(0, len(data), 2):
        if i + 1 >= len(data):
            break
        position = data[i] + ((data[i + 1] & 0b11) << 8)
        r, g, b = _decode_color_v2(data[i + 1])
        holds.append((position, r, g, b))
    return holds


def _decode_holds_v3(data: list[int]) -> list[tuple[int, int, int, int]]:
    """Decode hold data from API Level 3 (3 bytes per hold).

    Returns list of (position, r, g, b) tuples.
    """
    holds: list[tuple[int, int, int, int]] = []
    for i in range(0, len(data), 3):
        if i + 2 >= len(data):
            break
        position = (data[i + 1] << 8) + data[i]
        r, g, b = _decode_color_v3(data[i + 2])
        holds.append((position, r, g, b))
    return holds


class ProtocolDecoder:
    """Reassembles BLE chunks into complete Aurora Board messages and decodes holds."""

    def __init__(self, api_level: int, on_message: Callable[[list[tuple[int, int, int, int]]], None] | None = None):
        """Initialize the decoder.

        Args:
            api_level: Protocol API level (2 or 3).
            on_message: Callback invoked with decoded holds when a full message is received.
        """
        self.api_level: int = api_level
        self.on_message = on_message

        # Current packet being assembled
        self._packet: list[int] = []
        self._packet_length: int = -1

        # Accumulated holds across packets in the current message
        self._placements: list[tuple[int, int, int, int]] = []
        self._all_received: bool = False

    def feed(self, data: bytes | bytearray) -> None:
        """Feed raw bytes from a BLE write into the decoder.

        Bytes are processed one at a time to handle arbitrary chunk boundaries.
        """
        for byte in data:
            self._process_byte(byte)

    def _process_byte(self, byte: int) -> None:
        """Process a single incoming byte."""
        # If previous message was complete, reset for new message
        if self._all_received:
            self._all_received = False
            self._placements.clear()

        # First byte of a packet must be START_MARKER; skip otherwise
        if len(self._packet) == 0 and byte != START_MARKER:
            return

        self._packet.append(byte)

        # Second byte is the data length
        if len(self._packet) == 2:
            self._packet_length = byte + 5

        # Check if packet is complete
        elif self._packet_length > 0 and len(self._packet) == self._packet_length:
            if self._verify_and_parse():
                self._all_received = self._is_last_packet()
                if self._all_received:
                    logger.info("Complete message: %d holds decoded", len(self._placements))
                    if self.on_message:
                        self.on_message(list(self._placements))
            else:
                logger.warning("Packet verification failed, discarding message")
                self._placements.clear()

            self._packet.clear()
            self._packet_length = -1

    def _verify_and_parse(self) -> bool:
        """Verify checksum and parse hold data from the current packet.

        Returns True if the packet is valid, False otherwise.
        """
        pkt = self._packet
        pkt_len = self._packet_length

        # Basic structure check
        if pkt_len < 6:
            logger.debug("Packet too short: %d bytes", pkt_len)
            return False
        if pkt[0] != START_MARKER or pkt[3] != SEPARATOR or pkt[pkt_len - 1] != END_MARKER:
            logger.debug("Invalid packet framing")
            return False

        # Checksum validation: covers bytes [4 .. pkt_len-2]
        data_region = pkt[4:pkt_len - 1]
        expected = checksum(data_region)
        actual = pkt[2]
        if expected != actual:
            logger.debug("Checksum mismatch: expected %02x, got %02x", expected, actual)
            return False

        # Packet ordering validation
        is_first = self._is_first_packet()
        if len(self._placements) == 0 and not is_first:
            logger.debug("Expected first packet but got continuation")
            return False
        if len(self._placements) > 0 and is_first:
            logger.debug("Got first packet but already have placements")
            return False

        # Decode hold data (bytes after position code, before end marker)
        hold_data = pkt[5:pkt_len - 1]
        if self.api_level < 3:
            holds = _decode_holds_v2(hold_data)
        else:
            holds = _decode_holds_v3(hold_data)

        self._placements.extend(holds)
        logger.debug("Parsed %d holds from packet (total: %d)", len(holds), len(self._placements))
        return True

    def _is_first_packet(self) -> bool:
        """Check if the current packet is the first (or only) in a message."""
        pos_code = self._packet[4]
        if self.api_level < 3:
            return pos_code in (POS_ONLY_V2, POS_FIRST_V2)
        else:
            return pos_code in (POS_ONLY_V3, POS_FIRST_V3)

    def _is_last_packet(self) -> bool:
        """Check if the current packet is the last (or only) in a message."""
        pos_code = self._packet[4]
        if self.api_level < 3:
            return pos_code in (POS_ONLY_V2, POS_LAST_V2)
        else:
            return pos_code in (POS_ONLY_V3, POS_LAST_V3)
