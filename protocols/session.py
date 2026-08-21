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
from board_state import AuroraBoardState, MoonBoardState
from boards import PROTOCOL_AURORA, PROTOCOL_MOONBOARD, Board

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


class Session:
    """Common shape of one running simulator's protocol wiring."""

    ble_name: str
    gatt_profile: GattProfile
    window_title: str

    def feed(self, data: bytes) -> None:
        """Forward raw bytes from a GATT write to the protocol decoder."""
        raise NotImplementedError

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
    raise ValueError(f"unknown protocol family '{board.protocol}'")
