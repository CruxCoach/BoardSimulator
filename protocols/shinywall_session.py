"""Shiny Wall session: GATT shape, write-by-write logging, LED state.

Split from ``protocols.session`` (which builds it through
``create_session``) to keep both modules small. The decoder itself is
``protocols.shinywall``; this module adds what a live link needs: the
write context from BlueZ (type, MTU, peer), per-central decoders, the
simulated controller limits (MTU, processing delay) and the log lines a
protocol test reads.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field

import config
from ble.gatt import (CharacteristicSpec, GattProfile, GattUpdate, ServiceSpec,
                      WriteInfo, current_write)
from board_state import ShinyWallBoardState
from boards import Board
from protocols.session import Session
from protocols.shinywall import ACK, UNDERSTOOD, ShinyWallOptions

logger = logging.getLogger(__name__)


def shinywall_gatt_profile(options: ShinyWallOptions) -> GattProfile:
    """One service with the ESP32 write characteristic.

    The official app finds the characteristic in any service, so the
    service UUID (``--service-uuid``) and whether it is advertised
    (``--no-advertised-uuid``) are test knobs, not part of the contract.
    """
    characteristics = [CharacteristicSpec(
        config.SHINYWALL_WRITE_UUID, options.props, receives_writes=True)]
    description = (f"Shiny Wall service {options.service_uuid}, write "
                   f"characteristic {config.SHINYWALL_WRITE_UUID} "
                   f"[{', '.join(options.props)}]")
    if options.notify_uuid:
        characteristics.append(CharacteristicSpec(options.notify_uuid,
                                                  ("notify",)))
        description += f" + notify characteristic {options.notify_uuid}"
    return GattProfile(
        description=description,
        advertised_uuid=options.service_uuid if options.advertise_uuid else "",
        services=(ServiceSpec(options.service_uuid, tuple(characteristics)),),
    )


@dataclass
class _ShinyLink:
    """Per-central transport state: its decoder and write statistics."""

    decoder: object
    writes: int = 0
    last_at: float | None = None
    message: list[tuple[str, int]] = field(default_factory=list)


class ShinyWallSession(Session):
    """Shiny Wall: ASCII state vectors, logged write by write.

    Each central gets its own decoder (a partial message from one never
    mixes with another's); every completed message replaces the shared
    wall state. ``clock``/``sleep`` are injectable for tests.
    """

    WARN_LEVEL_EVENTS = {"garbage", "abort", "length", "invalid",
                         "unknown-code", "reject", "overflow", "command",
                         "legacy"}
    MESSAGE_END = {"clear", "state", "reject", "single", "hold", "disco",
                   "game", "foot", "command", "legacy", "abort", "overflow"}

    def __init__(self, board: Board, variant,
                 options: ShinyWallOptions | None = None,
                 clock=time.monotonic, sleep=time.sleep) -> None:
        from shinywall_geometry import ShinyWallGeometry

        self.board = board
        self.variant = variant
        self.options = (options or ShinyWallOptions()).validated()
        self.geometry = ShinyWallGeometry(
            variant, self.options.hold_count, self.options.geometry_path)
        self.hold_count = self.geometry.hold_count
        self.state = ShinyWallBoardState()
        self.events: deque = deque(maxlen=2000)
        self._links: dict[str | None, _ShinyLink] = {}
        # Decoded states wait until their events are logged, so the log
        # reads "STATE …" before the renderers report the new wall.
        self._pending: list[dict[int, int]] = []
        self._clock = clock
        self._sleep = sleep
        self._mtu_warned = False
        self.dropped = 0
        self.ble_name = (self.options.name or config.shinywall_ble_name(
            variant.name_code, self.options.identity))
        self.gatt_profile = shinywall_gatt_profile(self.options)
        self._ack_uuid = self.options.notify_uuid or config.SHINYWALL_WRITE_UUID
        self.window_title = (f"Shiny Wall Simulator — {variant.display_name} "
                             f"({self.hold_count} holds)")
        logger.info("Shiny Wall %s: %d holds, vector length %s, codes %s, "
                    "LEDs on disconnect: %s, positions: %s",
                    variant.key, self.hold_count, self.options.length_mode,
                    self.options.codes_mode, self.options.on_disconnect,
                    self.geometry.source)

    def _link(self, peer: str | None) -> _ShinyLink:
        from protocols.shinywall import ShinyWallProtocol

        link = self._links.get(peer)
        if link is None:
            link = _ShinyLink(ShinyWallProtocol(
                self.hold_count, self._queue_state,
                self.options.length_mode, self.options.codes_mode,
                current=self._wall))
            self._links[peer] = link
        return link

    def feed(self, data: bytes) -> list[GattUpdate] | None:
        info = current_write() or WriteInfo()
        link = self._link(info.peer)
        now = self._clock()
        gap = None if link.last_at is None else (now - link.last_at) * 1000.0
        link.last_at = now
        link.writes += 1
        write_type = info.write_type or "type ?"
        context = [f"#{link.writes}", write_type]
        if info.mtu:
            context.append(f"mtu {info.mtu}")
        if info.offset:
            context.append(f"offset {info.offset}")
        if gap is not None:
            context.append(f"+{gap:.0f} ms")
        printable = all(0x20 <= b < 0x7F for b in data)
        text = data.decode("ascii", errors="replace")
        logger.info("RX %d B (%s) from %s: %s", len(data), ", ".join(context),
                    info.address or "a central",
                    f"'{text}'" if printable else f"hex {data.hex()}")
        if self.options.mtu:
            limit = self.options.mtu - 3
            if info.mtu and info.mtu != self.options.mtu and not self._mtu_warned:
                self._mtu_warned = True
                logger.warning(
                    "BlueZ negotiated ATT MTU %d, the simulated controller has "
                    "%d; set ExchangeMTU = %d in /etc/bluetooth/main.conf "
                    "[GATT] so the app sees the same MTU", info.mtu,
                    self.options.mtu, self.options.mtu)
            if len(data) > limit:
                self.dropped += 1
                logger.warning(
                    "DROP: %d-byte write exceeds the simulated ATT MTU %d "
                    "(at most %d B per write)", len(data), self.options.mtu,
                    limit)
                return None
        if self.options.delay_ms:
            self._sleep(self.options.delay_ms / 1000.0)
        this_write = (write_type, len(data))
        link.message.append(this_write)
        events = link.decoder.feed(data)
        for event in events:
            self._log_event(event, link)
        self._apply_pending()
        # Statistics cover the writes of one message; a write can end one
        # message and start the next.
        if any(event.kind in self.MESSAGE_END for event in events):
            link.message = [this_write] if link.decoder.in_message else []
        elif not link.decoder.in_message:
            link.message = []
        if not self.options.ack:
            return None
        acks = [GattUpdate(notification=ACK, notify_uuid=self._ack_uuid)
                for event in events if event.kind in UNDERSTOOD]
        if acks:
            logger.info("ACK: notifying 'OK' x%d on %s (reaches subscribed "
                        "centrals only)", len(acks), self._ack_uuid)
        return acks or None

    def _wall(self) -> dict[int, int]:
        """The wall as the next message sees it (shared by all centrals)."""
        return self._pending[-1] if self._pending else self.state.get_holds()

    def _queue_state(self, state: dict[int, int]) -> None:
        self._pending.append(state)

    def _apply_pending(self) -> None:
        pending, self._pending = self._pending, []
        for state in pending:
            self.state.update(state)

    def _log_event(self, event, link: _ShinyLink | None = None) -> None:
        self.events.append(event)
        if event.kind == "state" and link is not None:
            types: dict[str, int] = {}
            for write_type, _size in link.message:
                types[write_type] = types.get(write_type, 0) + 1
            sizes = [size for _type, size in link.message]
            mix = ", ".join(f"{t}x{n}" for t, n in types.items())
            logger.info("STATE %s — from %d write(s) (%s), sizes %s",
                        event.detail, len(sizes), mix, sizes)
        else:
            level = (logging.WARNING if event.kind in self.WARN_LEVEL_EVENTS
                     else logging.INFO)
            logger.log(level, "%s %s", event.kind.upper(), event.detail)
        if event.kind in ("state", "hold"):
            ghosts = sorted(set(event.indices) & self.geometry.absent)
            if ghosts:
                logger.warning("ABSENT lit index(es) %s carry no hold on this "
                               "wall per the geometry file (should be 0)", ghosts)

    def connection_lost(self) -> None:
        """The last central left: drop partial messages, keep or clear LEDs."""
        for link in self._links.values():
            for event in link.decoder.reset():
                self._log_event(event)
        self._links.clear()
        if self.options.on_disconnect == "clear":
            logger.info("Link ended: wall cleared (--shiny-on-disconnect clear)")
            self.state.clear()
        else:
            logger.info("Link ended: %d hold(s) stay lit (--shiny-on-disconnect "
                        "keep)", len(self.state.get_holds()))

    disconnect = connection_lost

    def create_renderer(self, headless: bool = True):
        from render.shinywall_headless import ShinyWallHeadlessRenderer
        renderer = ShinyWallHeadlessRenderer(self.geometry)
        self.state.register_callback(renderer.update_holds)
        return renderer

    def create_panel(self, parent, selection, on_switch,
                     multi_connect=False, on_connections=None,
                     instance_count=1, on_instances=None,
                     board_height=None):
        from render.shinywall_gui import ShinyWallGUI
        panel = ShinyWallGUI(
            parent, self.geometry, self.ble_name,
            selection=selection, on_switch=on_switch,
            multi_connect=multi_connect, on_connections=on_connections,
            instance_count=instance_count, on_instances=on_instances,
            board_height=board_height)
        self.state.register_callback(panel.update_holds)
        return panel
