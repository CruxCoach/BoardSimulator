#!/usr/bin/env python3
"""BLE mock client that connects to a running simulator and sends a climb.

Usage: python tests/test_ble_mock_client.py [board-key] [--kilter-built]
       python tests/test_ble_mock_client.py shinywall [--layout st1]
              [--name NAME] [--command]
       (board-key: kilter | tension | grasshopper | decoy | soill |
        touchstone | moonboard | quantum | shinywall, default kilter; must
        match the running simulator's --board. --kilter-built matches a
        simulator started with --controller kilter-built: bare name, API
        level 2, and it writes like the Kilter app — with response, 100 ms
        apart. Shiny Wall: --layout must match the simulator's variant,
        --name picks one exact advertised name, --command writes without
        response; the route goes out like the official app sends it: '-',
        then '+' and one digit per hold and '.', each message in
        max(MTU - 5, 18)-byte writes, 100 ms before the first, 50 ms apart)

Requires the simulator (main.py) to be running first. Uses bleak as a
BLE central to scan (matching exactly like CruxCoach's BoardBleScanner),
connect and write a climb to the UART RX characteristic:

- Aurora-protocol boards: three holds in the board's own role colours,
  encoded via the BoardPacketEncoder port.
- MoonBoard: an ASCII climb frame as CruxCoach's MoonBoardFrameEncoder
  emits it.
- Shiny Wall: '-' and a full state vector (start/hand/foot/top), the
  characteristic looked up across all services.

Note: this is a manual integration helper, not a pytest test — it needs
a live BLE adapter and a running simulator. pytest collects nothing here
because there are no test_* functions.
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from boards import (PROTOCOL_AURORA, PROTOCOL_MOONBOARD,  # noqa: E402
                    PROTOCOL_QUANTUM, PROTOCOL_SHINYWALL, board_for)

UART_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
RX_CHARACTERISTIC_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
QUANTUM_WRITE_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
QUANTUM_NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
QUANTUM_STATE_UUID = "0000fff4-0000-1000-8000-00805f9b34fb"
QUANTUM_CONFIG_UUID = "0000fff5-0000-1000-8000-00805f9b34fb"

# A sample MoonBoard climb frame. l#<token><serialPos>,...# — S=start,
# P=hand, E=end/finish. This is the exact shape CruxCoach's
# MoonBoardFrameEncoder emits.
MOON_SAMPLE_FRAME = b"l#S0,P1,P40,P77,E197#"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def build_aurora_chunks(board_key: str, api_level: int = 3) -> list[bytes]:
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
    return encode_climb(holds, api_level=api_level)


def build_chunks(board_key: str, kilter_built: bool = False) -> list[bytes]:
    """The BLE writes for one sample climb, per protocol family."""
    board = board_for(board_key)
    if board.protocol == PROTOCOL_AURORA:
        return build_aurora_chunks(board_key, api_level=2 if kilter_built else 3)
    if board.protocol == PROTOCOL_QUANTUM:
        from protocols.quantum import (Command, EWALLS_ROUTE_DURATION_SECONDS,
                                       encode_chunks)
        return encode_chunks(
            Command.ACTIVATE_WALL,
            route_id="00112233-4455-6677-8899-aabbccddeeff",
            user_id="ffeeddcc-bbaa-9988-7766-554433221100", color="#00aaff",
            duration=EWALLS_ROUTE_DURATION_SECONDS,
            diodes=[1003, 1004, 1005])
    # MoonBoard: one ASCII frame, split to the 20-byte BLE chunk size.
    return [MOON_SAMPLE_FRAME[i:i + 20]
            for i in range(0, len(MOON_SAMPLE_FRAME), 20)]


def shiny_sample_route(hold_count: int) -> dict[int, int]:
    """Two starts, two hands, a foot and a top spread over the wall."""
    from protocols.shinywall import CODE_FOOT, CODE_HAND, CODE_START, CODE_TOP

    return {0: CODE_START, 1: CODE_START, hold_count // 4: CODE_HAND,
            hold_count // 2: CODE_HAND, hold_count // 8: CODE_FOOT,
            hold_count - 11: CODE_TOP}


def shiny_name_matches(name: str, wanted: str | None) -> bool:
    """CruxCoach's ShinyWallName rules, or one exact name."""
    import re

    if wanted:
        return name == wanted
    match = re.match(r"^ShinyWall_([A-Z0-9]+)_(\S+)$", name)
    if match:
        return match.group(1).startswith("ST")
    return re.match(r"^Shiny Wall (ST\d)\b", name) is not None


def _option(name: str) -> str | None:
    argv = sys.argv[1:]
    if name in argv and argv.index(name) + 1 < len(argv):
        return argv[argv.index(name) + 1]
    return None


async def send_shiny(client, board, layout: str | None,
                     with_response: bool) -> None:
    """Find the write characteristic in any service and send one route."""
    import config
    from protocols.shinywall import (APP_CHUNK_GAP_MS, APP_FIRST_CHUNK_DELAY_MS,
                                     chunks, encode_vector, state_vector)

    variant = board.variant_for(layout)
    char = next((c for service in client.services
                 for c in service.characteristics
                 if c.uuid.lower() == config.SHINYWALL_WRITE_UUID), None)
    if char is None:
        raise RuntimeError("write characteristic beb5483e-… not found")
    mtu = getattr(client, "mtu_size", 23) or 23
    route = shiny_sample_route(variant.hold_count)
    messages = [b"-", encode_vector(state_vector(route, variant.hold_count))]
    logger.info("Shiny Wall %s: characteristic in service %s, properties %s, "
                "MTU %d -> writes of up to %d B, %s", variant.key,
                char.service_uuid, char.properties, mtu, max(mtu - 5, 18),
                "with response" if with_response else "without response")
    logger.info("Route: %s", sorted(route.items()))
    for message in messages:
        await asyncio.sleep(APP_FIRST_CHUNK_DELAY_MS / 1000)
        for index, chunk in enumerate(chunks(message, mtu)):
            if index:
                await asyncio.sleep(APP_CHUNK_GAP_MS / 1000)
            await client.write_gatt_char(char, chunk, response=with_response)


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

    valued = {_option("--layout"), _option("--name")} - {None}
    args = [arg for arg in sys.argv[1:]
            if not arg.startswith("--") and arg not in valued]
    kilter_built = "--kilter-built" in sys.argv[1:]
    board_key = args[0] if args else "kilter"
    board = board_for(board_key)
    shiny_name = _option("--name")
    chunks = ([] if board.protocol == PROTOCOL_SHINYWALL
              else build_chunks(board_key, kilter_built))

    logger.info("Scanning for a fake %s...", board.display_name)

    def _match(device, adv) -> bool:
        name = (device.name or adv.local_name or "")
        if board.protocol == PROTOCOL_AURORA:
            # Classic boxes: 'Name#serial@apiLevel'. Kilter-built boxes: the
            # bare product name. Brand from the name either way.
            suffixed = "@" in name or "#" in name
            if suffixed == kilter_built:
                return False
            return cruxcoach_brand_prefix_match(name, board.cruxcoach_prefix)
        if board.protocol == PROTOCOL_QUANTUM:
            return name.startswith(("QB_", "QBB_"))
        if board.protocol == PROTOCOL_SHINYWALL:
            return shiny_name_matches(name, shiny_name)
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

        if board.protocol == PROTOCOL_SHINYWALL:
            await send_shiny(client, board, _option("--layout"),
                             with_response="--command" not in sys.argv[1:])
            logger.info("Route sent — check the simulator log (STATE line).")
            await asyncio.sleep(2.0)
            logger.info("Disconnected.")
            return

        if board.protocol == PROTOCOL_QUANTUM:
            identity = bytes(await client.read_gatt_char(QUANTUM_CONFIG_UUID))
            state = bytes(await client.read_gatt_char(QUANTUM_STATE_UUID))
            if len(identity) != 41:
                raise RuntimeError(f"invalid fff5 identity length: {len(identity)}")
            logger.info("fff5 board type=%d, dimensions=%dx%d, state=%s",
                        identity[34], int.from_bytes(identity[35:37], "big"),
                        int.from_bytes(identity[37:39], "big"), state.hex())
            notifications = []
            await client.start_notify(
                QUANTUM_NOTIFY_UUID,
                lambda _sender, value: notifications.append(bytes(value)))

        logger.info("Sending %d BLE chunks...", len(chunks))
        for chunk_idx, chunk in enumerate(chunks):
            logger.debug("Chunk %d: %s", chunk_idx, chunk.hex(" "))
            characteristic = (QUANTUM_WRITE_UUID
                              if board.protocol == PROTOCOL_QUANTUM
                              else RX_CHARACTERISTIC_UUID)
            # CruxCoach currently uses a write request for Quantum. Exercise
            # that transport choice without treating it as a hardware claim.
            # A Kilter-built box takes write requests only, 100 ms apart.
            await client.write_gatt_char(
                characteristic, chunk,
                response=(board.protocol == PROTOCOL_QUANTUM or kilter_built))
            await asyncio.sleep(0.1 if kilter_built else 0.05)

        logger.info("Climb sent — check the simulator window / log.")
        await asyncio.sleep(3.0)
        if board.protocol == PROTOCOL_QUANTUM:
            state = bytes(await client.read_gatt_char(QUANTUM_STATE_UUID))
            route = bytes.fromhex("00112233445566778899aabbccddeeff")
            if state[1:4] != b"\x47\x01\x00" or route not in state:
                raise RuntimeError(f"unexpected fff4 route state: {state.hex()}")
            if not notifications or notifications[-1][1:4] != b"\x41\x01\x00":
                seen = notifications[-1].hex() if notifications else "none"
                raise RuntimeError(f"missing fff1 activation event: {seen}")
            logger.info("Stateful Quantum response matched: fff1 event + fff4 roster")

    logger.info("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())
