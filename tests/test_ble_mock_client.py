#!/usr/bin/env python3
"""BLE mock client that connects to a running simulator and sends a climb.

Usage: python tests/test_ble_mock_client.py [board-key]
       (board-key: kilter | tension | grasshopper | decoy | soill |
        touchstone | moonboard, default kilter; must match the running
        simulator's --board)

Requires the simulator (main.py) to be running first. Uses bleak as a
BLE central to scan (matching exactly like CruxCoach's BoardBleScanner),
connect and write a climb to the UART RX characteristic:

- Aurora-protocol boards: three holds in the board's own role colours,
  encoded via the BoardPacketEncoder port.
- MoonBoard: an ASCII climb frame as CruxCoach's MoonBoardFrameEncoder
  emits it.

Note: this is a manual integration helper, not a pytest test — it needs
a live BLE adapter and a running simulator. pytest collects nothing here
because there are no test_* functions.
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from boards import PROTOCOL_AURORA, board_for  # noqa: E402

UART_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
RX_CHARACTERISTIC_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

# A sample MoonBoard climb frame. l#<token><serialPos>,...# — S=start,
# P=hand, E=end/finish. This is the exact shape CruxCoach's
# MoonBoardFrameEncoder emits.
MOON_SAMPLE_FRAME = b"l#S0,P1,P40,P77,E197#"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def build_aurora_chunks(board_key: str) -> list[bytes]:
    """Encode a small sample climb in the board's own role colours.

    Picks the first three placements of the default layout/size and encodes
    them start/middle/finish — exactly what CruxCoach's send path produces.
    """
    from board_geometry import BoardGeometry
    from protocols.aurora_encoder import encode_climb, hex_to_color_byte

    board = board_for(board_key)
    variant = board.variant_for(None)
    geometry = BoardGeometry(board, variant, variant.default_size_id)
    led_map = geometry.placement_led_map()
    role_ids = sorted(geometry.roles)  # [start, middle, finish, foot]

    placements = sorted(led_map)[:3]
    holds = [
        (led_map[placement], hex_to_color_byte(geometry.roles[role_id].led_color))
        for placement, role_id in zip(placements, role_ids[:3])
    ]
    logger.info("Sample climb: %s", [(p, geometry.roles[r].name)
                                     for p, r in zip(placements, role_ids[:3])])
    return encode_climb(holds, api_level=3)


def build_chunks(board_key: str) -> list[bytes]:
    """The BLE writes for one sample climb, per protocol family."""
    board = board_for(board_key)
    if board.protocol == PROTOCOL_AURORA:
        return build_aurora_chunks(board_key)
    # MoonBoard: one ASCII frame, split to the 20-byte BLE chunk size.
    return [MOON_SAMPLE_FRAME[i:i + 20]
            for i in range(0, len(MOON_SAMPLE_FRAME), 20)]


def cruxcoach_brand_prefix_match(name: str, prefix: str) -> bool:
    """CruxCoach's auroraBrandFromName normalisation: lowercase, strip
    spaces/hyphens, match the brand prefix."""
    normalised = name.lower().replace(" ", "").replace("-", "")
    return normalised.startswith(prefix)


async def main() -> None:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError:
        logger.error("bleak not installed — run: pip install bleak")
        sys.exit(1)

    board_key = sys.argv[1] if len(sys.argv) > 1 else "kilter"
    board = board_for(board_key)
    chunks = build_chunks(board_key)

    logger.info("Scanning for a fake %s...", board.display_name)

    def _match(device, adv) -> bool:
        name = (device.name or adv.local_name or "")
        if board.protocol == PROTOCOL_AURORA:
            # Aurora names are 'Name#serial@apiLevel'; brand from the name.
            if "@" not in name and "#" not in name:
                return False
            return cruxcoach_brand_prefix_match(name, board.cruxcoach_prefix)
        # MoonBoard: bare "MoonBoard…" name prefix (CruxCoach
        # isMoonBoardName accepts both capitalisations).
        return name.startswith(("MoonBoard", "Moonboard"))

    device = await BleakScanner.find_device_by_filter(_match, timeout=10.0)
    if device is None:
        logger.error("No fake %s found. Is main.py --board %s running?",
                     board.display_name, board_key)
        sys.exit(1)

    logger.info("Found device: %s (%s)", device.name, device.address)

    async with BleakClient(device) as client:
        logger.info("Connected to %s", device.name)

        logger.info("Sending %d BLE chunks...", len(chunks))
        for chunk_idx, chunk in enumerate(chunks):
            logger.debug("Chunk %d: %s", chunk_idx, chunk.hex(" "))
            await client.write_gatt_char(
                RX_CHARACTERISTIC_UUID, chunk, response=False
            )
            await asyncio.sleep(0.05)

        logger.info("Climb sent — check the simulator window / log.")
        await asyncio.sleep(3.0)

    logger.info("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())
