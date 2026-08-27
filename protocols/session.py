"""Protocol-family sessions: one object wiring board -> decoder -> state.

This is the seam between the shared BLE peripheral and the two protocol
families. A session owns everything protocol-specific about one running
simulator:

- the BLE identity (advertised name) and GATT shape (:class:`GattProfile`),
- the protocol decoder fed from GATT writes,
- the board state the decoder updates,
- renderer construction (GUI or headless) for its rendering model.

main.py only ever talks to the :class:`Session` interface, so adding a
third protocol family means adding a session class here and a registry
entry in boards.py — the CLI and BLE layers stay untouched.
"""

from __future__ import annotations

import logging

import config
from ble.gatt import CharacteristicSpec, GattProfile, ServiceSpec
from board_state import AuroraBoardState, MoonBoardState, QuantumBoardState
from boards import PROTOCOL_AURORA, PROTOCOL_MOONBOARD, PROTOCOL_QUANTUM, Board

logger = logging.getLogger(__name__)

# Aurora boards expose an empty discovery service (the UUID the official
# apps historically filter on) + the Nordic UART Service whose RX
# characteristic receives the climb packets.
AURORA_GATT_PROFILE = GattProfile(
    description="discovery + UART service",
    advertised_uuid=config.AURORA_ADVERTISING_SERVICE_UUID,
    services=(
        ServiceSpec(uuid=config.AURORA_ADVERTISING_SERVICE_UUID),
        ServiceSpec(
            uuid=config.UART_SERVICE_UUID,
            characteristics=(
                CharacteristicSpec(
                    uuid=config.RX_CHARACTERISTIC_UUID,
                    flags=("write", "write-without-response"),
                    receives_writes=True,
                ),
            ),
        ),
    ),
)

# A MoonBoard exposes ONLY the Nordic UART Service and advertises that
# service's UUID itself. TX is a notify-only stub a real board pushes
# on; the apps only ever write, so nothing is sent.
MOONBOARD_GATT_PROFILE = GattProfile(
    description="Nordic UART Service",
    advertised_uuid=config.UART_SERVICE_UUID,
    services=(
        ServiceSpec(
            uuid=config.UART_SERVICE_UUID,
            characteristics=(
                CharacteristicSpec(
                    uuid=config.RX_CHARACTERISTIC_UUID,
                    flags=("write", "write-without-response"),
                    receives_writes=True,
                ),
                CharacteristicSpec(
                    uuid=config.TX_CHARACTERISTIC_UUID,
                    flags=("notify",),
                ),
            ),
        ),
    ),
)

def quantum_config_payload(variant, local_name: str) -> bytes:
    """Build the 41-byte fff5 identity record parsed by eWalls 2.0.14."""
    type_byte = {"big": 0, "small": 1, "xsmall": 2, "belay": 3,
                 "medium": 4}[variant.catalog_type]
    identity = local_name.rsplit("_", 1)[-1]
    mac = bytes.fromhex(identity) if len(identity) == 12 else b"\0" * 6
    payload = bytearray(41)
    payload[:24] = local_name.encode("ascii")[:24].ljust(24, b"\0")
    payload[24:30] = mac
    # Controller protocol/firmware/auto-off values are not hardware-captured.
    # Keep them explicitly unknown instead of confusing app version 2.0.14
    # with controller firmware identity.
    payload[30] = 0
    payload[31:34] = bytes((0, 0, 0))
    payload[34] = type_byte
    payload[35:37] = variant.columns.to_bytes(2, "big")
    payload[37:39] = variant.rows.to_bytes(2, "big")
    payload[39:41] = (0).to_bytes(2, "big")
    return bytes(payload)


QUANTUM_GATT_PROFILE = GattProfile(
    description="Quantum ffe0 service (fff1/fff2/fff4/fff5)",
    advertised_uuid=config.QUANTUM_SERVICE_UUID,
    services=(
        ServiceSpec(
            uuid=config.QUANTUM_SERVICE_UUID,
            characteristics=(
                CharacteristicSpec(
                    uuid=config.QUANTUM_WRITE_UUID,
                    flags=("write-without-response",),
                    receives_writes=True,
                ),
                CharacteristicSpec(
                    uuid=config.QUANTUM_NOTIFY_UUID,
                    flags=("notify",),
                ),
                CharacteristicSpec(
                    uuid=config.QUANTUM_STATE_UUID,
                    flags=("read",),
                    initial_value=bytes((1, 0x47, 0, 0)),
                ),
                CharacteristicSpec(
                    uuid=config.QUANTUM_CONFIG_UUID,
                    flags=("read",),
                    initial_value=bytes(41),
                ),
            ),
        ),
    ),
)


class Session:
    """Common shape of one running simulator's protocol wiring."""

    ble_name: str
    gatt_profile: GattProfile
    window_title: str

    def feed(self, data: bytes) -> None:
        """Forward raw bytes from a GATT write to the protocol decoder."""
        raise NotImplementedError

    def connection_lost(self) -> None:
        """Reset per-link transport state; controller state stays intact."""

    def create_renderer(self, headless: bool = True):
        """Build the headless renderer (stdout grid) and wire it to the state."""
        raise NotImplementedError

    def create_panel(self, parent, selection, on_switch,
                     multi_connect=False, on_connections=None,
                     instance_count=1, on_instances=None,
                     board_height=None):
        """Build the Tk GUI panel into ``parent`` and wire it to the state.

        Must be called from the main thread (Tkinter requirement). The
        controller (main._run_gui) owns a single persistent root + main loop;
        on a board-bar pick it tears the panel down and rebuilds it for the
        new selection. Returns an object with update_holds/update_status.

        ``board_height`` overrides the height the panel opens with (--window
        -height); the panel stays resizable regardless.
        """
        raise NotImplementedError


class AuroraSession(Session):
    """Kilter + the five Aurora-family boards: binary packets, LED map."""

    def __init__(self, board: Board, variant, size_id: int,
                 api_level: int, serial: str) -> None:
        # Imported here so MoonBoard-only runs never touch sqlite assets.
        from board_geometry import BoardGeometry
        from protocols.aurora_decoder import ProtocolDecoder
        from role_colors import RoleColorResolver

        self.board = board
        self.variant = variant
        self.api_level = api_level
        self.geometry = BoardGeometry(board, variant, size_id)
        self.resolver = RoleColorResolver(self.geometry.roles, api_level=api_level)
        self.state = AuroraBoardState()
        self._decoder = ProtocolDecoder(
            api_level=api_level, on_message=self.state.update)

        self.ble_name = config.aurora_ble_name(
            board.display_name, serial, api_level)
        self.gatt_profile = AURORA_GATT_PROFILE
        self.window_title = (
            f"{board.display_name} Simulator — "
            f"{variant.display_name} / {self.geometry.size.name}"
        )

    def feed(self, data: bytes) -> None:
        self._decoder.feed(data)

    def create_renderer(self, headless: bool = True):
        from render.aurora_headless import HeadlessRenderer
        renderer = HeadlessRenderer(self.geometry, self.resolver)
        self.state.register_callback(renderer.update_holds)
        return renderer

    def create_panel(self, parent, selection, on_switch,
                     multi_connect=False, on_connections=None,
                     instance_count=1, on_instances=None,
                     board_height=None):
        # Imported lazily so headless boxes without Tk still run.
        from render.aurora_gui import BoardGUI
        panel = BoardGUI(parent, self.geometry, self.ble_name, self.resolver,
                         selection=selection, on_switch=on_switch,
                         multi_connect=multi_connect,
                         on_connections=on_connections,
                         instance_count=instance_count,
                         on_instances=on_instances,
                         board_height=board_height)
        self.state.register_callback(panel.update_holds)
        return panel


class MoonSession(Session):
    """MoonBoard: ASCII frames, photo/coordinate-map rendering."""

    def __init__(self, board: Board, variant) -> None:
        from protocols.moonboard import MoonBoardProtocol

        self.board = board
        self.variant = variant
        self.state = MoonBoardState()
        # Holder semantics: the GUI variant picker can swap the decoder at
        # runtime; the decoder also holds parser state, so a clean swap is
        # simpler than mutating in place.
        self._decoder = MoonBoardProtocol(
            on_message=self.state.update, grid_rows=variant.grid_rows)

        self.ble_name = config.MOONBOARD_BLE_NAME
        self.gatt_profile = MOONBOARD_GATT_PROFILE
        self.window_title = f"MoonBoard Simulator — {variant.display_name}"

    def feed(self, data: bytes) -> None:
        self._decoder.feed(data)

    def switch_variant(self, new_variant) -> None:
        """Swap the active MoonBoard variant (GUI picker callback)."""
        from protocols.moonboard import MoonBoardProtocol

        logger.info(
            "Variant switch: %s -> %s (grid_rows %d -> %d)",
            self.variant.display_name, new_variant.display_name,
            self._decoder.grid_rows, new_variant.grid_rows,
        )
        self.variant = new_variant
        self._decoder = MoonBoardProtocol(
            on_message=self.state.update, grid_rows=new_variant.grid_rows)
        self.state.clear()

    def create_renderer(self, headless: bool = True):
        from render.moon_headless import MoonHeadlessRenderer
        renderer = MoonHeadlessRenderer(self.variant.grid_rows)
        self.state.register_callback(renderer.update_holds)
        return renderer

    def create_panel(self, parent, selection, on_switch,
                     multi_connect=False, on_connections=None,
                     instance_count=1, on_instances=None,
                     board_height=None):
        # Imported lazily so headless boxes without Tk still run.
        from render.moon_gui import MoonBoardGUI
        panel = MoonBoardGUI(parent, self.board, self.variant,
                             selection=selection, on_switch=on_switch,
                             multi_connect=multi_connect,
                             on_connections=on_connections,
                             instance_count=instance_count,
                             on_instances=on_instances,
                             board_height=board_height)
        self.state.register_callback(panel.update_holds)
        return panel


class QuantumSession(Session):
    """Quantum Board using the response behaviour captured from a real XL.

    Valid commands update the rendered LEDs, while fff4 remains the last
    controller-published snapshot (initially empty) and fff1 stays silent.
    Protocol fault injection can still publish Modbus exception notifications.
    """

    def __init__(self, board: Board, variant) -> None:
        from protocols.quantum import QuantumProtocol
        from quantum_geometry import QuantumGeometry

        self.board = board
        self.variant = variant
        self.geometry = QuantumGeometry(variant)
        addresses = tuple(d.address16 for d in self.geometry.diodes)
        self.state = QuantumBoardState(addresses)
        self.events = []
        self._decoder = QuantumProtocol(self.state.apply, self.events.append)
        self.ble_name = config.quantum_ble_name(variant.key)
        config_value = quantum_config_payload(variant, self.ble_name)
        service = QUANTUM_GATT_PROFILE.services[0]
        self.gatt_profile = GattProfile(
            QUANTUM_GATT_PROFILE.description,
            QUANTUM_GATT_PROFILE.advertised_uuid,
            (ServiceSpec(service.uuid, tuple(
                CharacteristicSpec(
                    char.uuid, char.flags, char.receives_writes,
                    config_value if char.uuid == config.QUANTUM_CONFIG_UUID
                    else char.initial_value)
                for char in service.characteristics)),),
        )
        self.window_title = f"Quantum Board Simulator — {variant.display_name}"

    def feed(self, data: bytes) -> list[bytes]:
        return self._decoder.feed(data)

    def connection_lost(self) -> None:
        """Drop partial transport data but preserve controller LED state."""
        self._decoder.reset_transport()

    disconnect = connection_lost
    reconnect = connection_lost

    def create_renderer(self, headless: bool = True):
        from render.quantum_headless import QuantumHeadlessRenderer
        renderer = QuantumHeadlessRenderer(self.geometry)
        self.state.register_callback(renderer.update_holds)
        return renderer

    def create_panel(self, parent, selection, on_switch,
                     multi_connect=False, on_connections=None,
                     instance_count=1, on_instances=None,
                     board_height=None):
        from render.quantum_gui import QuantumBoardGUI
        panel = QuantumBoardGUI(
            parent, self.geometry, self.ble_name,
            selection=selection, on_switch=on_switch,
            multi_connect=multi_connect, on_connections=on_connections,
            instance_count=instance_count, on_instances=on_instances,
            board_height=board_height)
        self.state.register_callback(panel.update_holds)
        return panel


def create_session(board: Board, variant, size_id: int | None,
                   api_level: int | None, serial: str | None) -> Session:
    """Build the right session for a board's protocol family.

    Aurora-only options (--size, --api-level, --serial) raise on a
    MoonBoard rather than being silently ignored.
    """
    if board.protocol == PROTOCOL_AURORA:
        if size_id is None:
            size_id = variant.default_size_id
        return AuroraSession(
            board, variant, size_id,
            api_level if api_level is not None else config.API_LEVEL,
            serial if serial is not None else config.BOARD_SERIAL,
        )
    if board.protocol == PROTOCOL_MOONBOARD:
        rejected = [name for name, value in (
            ("--size", size_id), ("--api-level", api_level),
            ("--serial", serial)) if value is not None]
        if rejected:
            raise ValueError(
                f"{', '.join(rejected)} only applies to Aurora-protocol "
                "boards, not the MoonBoard"
            )
        return MoonSession(board, variant)
    if board.protocol == PROTOCOL_QUANTUM:
        rejected = [name for name, value in (
            ("--size", size_id), ("--api-level", api_level),
            ("--serial", serial)) if value is not None]
        if rejected:
            raise ValueError(
                f"{', '.join(rejected)} only applies to Aurora-protocol "
                "boards, not Quantum"
            )
        return QuantumSession(board, variant)
    raise ValueError(f"unknown protocol family '{board.protocol}'")
