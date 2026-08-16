"""Two virtual climbing boards sharing one physical Bluetooth adapter.

The controller exposes one connectable advertising set at a time.  This
module rotates that set between two stable random addresses.  A connection
consumes the active advertisement but remains alive while the other identity
is advertised, so two phones can ultimately hold two independent board links.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus

from ble.adapter import (DEFAULT_ADAPTER, adapter_path, device_path_prefix,
                         device_signal_match_rule, normalize_adapter)
from ble.advertising import (disable_extended_adv, set_virtual_adv_data,
                             static_random_address)
from ble.gatt import GattProfile, build_application, merge_profiles
from ble.peripheral import BLUEZ_SERVICE, GATT_MANAGER_IFACE

logger = logging.getLogger(__name__)


@dataclass
class VirtualBoard:
    """Protocol and callbacks belonging to one advertised board identity."""

    key: str
    ble_name: str
    profile: GattProfile
    on_data: Callable[[bytes], None]
    on_connect: Callable[[], None] | None = None
    on_disconnect: Callable[[], None] | None = None
    address: bytes = field(init=False)

    def __post_init__(self) -> None:
        self.address = static_random_address(f"boardsim:{self.key}:{self.ble_name}")


class MultiplexedBLEPeripheral:
    """Serve exactly two virtual boards from one BlueZ adapter."""

    ROTATION_SECONDS = 3.0
    POLL_SECONDS = 0.25

    def __init__(self, boards: list[VirtualBoard],
                 on_fatal: Callable[[BaseException], None] | None = None,
                 multi_connect: bool = False,
                 adapter: str = DEFAULT_ADAPTER) -> None:
        if len(boards) != 2:
            raise ValueError("the multiplexed peripheral requires exactly two boards")
        if boards[0].address == boards[1].address:
            raise ValueError("virtual boards require different BLE identities")
        self._boards = boards
        self._profile = merge_profiles([board.profile for board in boards])
        self._adapter = normalize_adapter(adapter)
        self._adapter_path = adapter_path(self._adapter)
        self._device_prefix = device_path_prefix(self._adapter)
        self._on_fatal = on_fatal
        self._multi_connect = multi_connect
        self._running = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bus: MessageBus | None = None
        self._current_slot: int | None = None
        self._advertising_since = 0.0
        self._assignments: dict[str, int] = {}
        self._connected: list[set[str]] = [set(), set()]
        self._force_rotate = False
        self._adv_lock = asyncio.Lock()

    @property
    def adapter(self) -> str:
        return self._adapter

    @property
    def assignments(self) -> dict[str, int]:
        """Snapshot used by diagnostics and hardware-independent tests."""
        return dict(self._assignments)

    def set_multi_connect(self, enabled: bool) -> None:
        self._multi_connect = enabled
        self._force_rotate = True
        logger.info("Virtual-board connection mode → %s",
                    "multi" if enabled else "single")

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True,
            name=f"ble-multiplexer-{self._adapter}")
        self._thread.start()
        logger.info("[%s] Two-board BLE multiplexer started", self._adapter)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("[%s] Two-board BLE multiplexer stopped", self._adapter)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as exc:
            logger.exception("Two-board BLE event loop error")
            if self._on_fatal is not None and self._running:
                self._on_fatal(exc)
        finally:
            self._loop.close()

    def _owns_device_path(self, path: str) -> bool:
        return path.startswith(self._device_prefix)

    def _slot_for_write(self, device: str | None) -> int | None:
        if device and device in self._assignments:
            return self._assignments[device]
        if self._current_slot is None:
            return None
        slot = self._current_slot
        if device:
            self._assign_device(device, slot)
        return slot

    def _handle_gatt_write(self, data: bytes, device: str | None) -> None:
        slot = self._slot_for_write(device)
        if slot is None:
            logger.warning("Dropping GATT write without a virtual-board assignment")
            return
        self._boards[slot].on_data(data)

    def _assign_device(self, device: str, slot: int) -> None:
        previous = self._assignments.get(device)
        self._assignments[device] = slot
        if device not in self._connected[slot]:
            was_empty = not self._connected[slot]
            self._connected[slot].add(device)
            if was_empty and self._boards[slot].on_connect:
                self._boards[slot].on_connect()
        if previous != slot:
            logger.info("%s assigned to virtual board %d '%s'",
                        device, slot + 1, self._boards[slot].ble_name)
        self._force_rotate = True

    async def _connected_paths(self) -> set[str]:
        if self._bus is None:
            return set()
        try:
            reply = await self._bus.call(Message(
                destination=BLUEZ_SERVICE, path="/",
                interface="org.freedesktop.DBus.ObjectManager",
                member="GetManagedObjects"))
            if reply is None or reply.message_type == MessageType.ERROR:
                return set()
            return {
                path for path, ifaces in reply.body[0].items()
                if self._owns_device_path(path)
                and ifaces.get("org.bluez.Device1", {}).get("Connected")
                and ifaces["org.bluez.Device1"]["Connected"].value
            }
        except Exception as exc:
            logger.debug("Could not enumerate connected centrals: %s", exc)
            return set()

    async def _reconcile_connections(self) -> None:
        paths = await self._connected_paths()
        known_connected = self._connected[0] | self._connected[1]
        for device in paths - known_connected:
            if self._current_slot is not None:
                self._assign_device(device, self._current_slot)
        for slot in range(2):
            gone = self._connected[slot] - paths
            if gone:
                self._connected[slot].difference_update(gone)
                if not self._connected[slot] and self._boards[slot].on_disconnect:
                    self._boards[slot].on_disconnect()
                self._force_rotate = True

    def _advertisable_slots(self) -> list[int]:
        return [slot for slot in range(2)
                if self._multi_connect or not self._connected[slot]]

    def _next_slot(self) -> int | None:
        available = self._advertisable_slots()
        if not available:
            return None
        if self._current_slot not in available:
            return available[0]
        current_at = available.index(self._current_slot)
        return available[(current_at + 1) % len(available)]

    async def _program_slot(self, slot: int, adapter_props) -> None:
        board = self._boards[slot]
        async with self._adv_lock:
            try:
                await adapter_props.call_set(
                    "org.bluez.Adapter1", "Alias", Variant("s", board.ble_name))
            except Exception as exc:
                logger.debug("Could not update adapter alias: %s", exc)
            if not set_virtual_adv_data(
                    board.profile.advertised_uuid, board.ble_name,
                    board.address, self._adapter):
                raise RuntimeError(
                    f"controller rejected virtual board {slot + 1} advertising")
            self._current_slot = slot
            self._advertising_since = time.monotonic()
            self._force_rotate = False

    async def _serve(self) -> None:
        if os.geteuid() != 0:
            logger.warning("Two-board BLE mode needs root privileges")
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        introspection = await self._bus.introspect(
            BLUEZ_SERVICE, self._adapter_path)
        adapter = self._bus.get_proxy_object(
            BLUEZ_SERVICE, self._adapter_path, introspection)
        props = adapter.get_interface("org.freedesktop.DBus.Properties")
        await props.call_set("org.bluez.Adapter1", "Powered", Variant("b", True))

        app = build_application(self._profile, self._handle_gatt_write)
        self._bus.export(app.path, app)
        for service in app.services:
            self._bus.export(service.path, service)
            for char in service.characteristics:
                self._bus.export(char.path, char)
        gatt_mgr = adapter.get_interface(GATT_MANAGER_IFACE)
        await gatt_mgr.call_register_application(app.path, {})  # type: ignore

        await props.call_set(
            "org.bluez.Adapter1", "DiscoverableTimeout", Variant("u", 0))
        await props.call_set(
            "org.bluez.Adapter1", "Discoverable", Variant("b", True))
        await asyncio.sleep(1.0)

        def on_properties_changed(msg: Message) -> None:
            if (msg.message_type == MessageType.SIGNAL
                    and msg.member == "PropertiesChanged"
                    and self._owns_device_path(msg.path or "")
                    and msg.body and msg.body[0] == "org.bluez.Device1"
                    and "Connected" in msg.body[1]):
                asyncio.ensure_future(self._reconcile_connections())

        self._bus.add_message_handler(on_properties_changed)
        await self._bus.call(Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="AddMatch", signature="s",
            body=[device_signal_match_rule(self._adapter)]))

        try:
            await self._program_slot(0, props)
            while self._running:
                await asyncio.sleep(self.POLL_SECONDS)
                await self._reconcile_connections()
                due = time.monotonic() - self._advertising_since >= self.ROTATION_SECONDS
                if self._force_rotate or due:
                    next_slot = self._next_slot()
                    if next_slot is None:
                        disable_extended_adv(self._adapter)
                        self._current_slot = None
                        self._force_rotate = False
                    elif next_slot != self._current_slot or self._force_rotate:
                        await self._program_slot(next_slot, props)
        finally:
            try:
                await gatt_mgr.call_unregister_application(app.path)  # type: ignore
            except Exception as exc:
                logger.debug("Could not unregister multiplexed GATT app: %s", exc)
            disable_extended_adv(self._adapter)
            try:
                await props.call_set(
                    "org.bluez.Adapter1", "Discoverable", Variant("b", False))
            except Exception:
                pass
            self._bus.disconnect()
