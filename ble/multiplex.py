"""Two simultaneous virtual climbing boards on one Bluetooth controller.

Each board gets its own hardware-offloaded Extended Advertising set and
static-random address.  Linux's read-only HCI monitor channel tells us which
advertising handle accepted a connection, so writes arriving through the one
merged BlueZ GATT application can still be routed to the correct board.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus

from ble.adapter import (DEFAULT_ADAPTER, adapter_path, device_path_prefix,
                         device_signal_match_rule, normalize_adapter)
from ble.advertising import (configure_hardware_adv_set,
                             read_supported_adv_sets, remove_adv_set,
                             set_adv_sets_enabled, static_random_address)
from ble.gatt import GattProfile, GattUpdate, build_application, merge_profiles
from ble.hci_monitor import AdvertisingConnection, Disconnection, HciMonitor
from ble.peripheral import BLUEZ_SERVICE, GATT_MANAGER_IFACE

logger = logging.getLogger(__name__)


@dataclass
class VirtualBoard:
    """Protocol and callbacks belonging to one advertised board identity."""

    key: str
    ble_name: str
    profile: GattProfile
    on_data: Callable[[bytes], list[GattUpdate | bytes] | bytes | None]
    on_connect: Callable[[], None] | None = None
    on_disconnect: Callable[[], None] | None = None
    address: bytes = field(init=False)

    def __post_init__(self) -> None:
        self.address = static_random_address(f"boardsim:{self.key}:{self.ble_name}")


@dataclass(frozen=True)
class _PendingConnection:
    slot: int
    connection_handle: int
    peer_address: str | None


class MultiplexedBLEPeripheral:
    """Serve exactly two simultaneous virtual boards from one BlueZ adapter."""

    POLL_SECONDS = 0.25
    ADV_HANDLES = (1, 2)

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
        self._adapter_index = int(self._adapter.removeprefix("hci"))
        self._adapter_path = adapter_path(self._adapter)
        self._device_prefix = device_path_prefix(self._adapter)
        self._on_fatal = on_fatal
        self._multi_connect = multi_connect
        self._running = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bus: MessageBus | None = None
        self._monitor: HciMonitor | None = None
        self._assignments: dict[str, int] = {}
        self._connected: list[set[str]] = [set(), set()]
        self._connection_slots: dict[int, int] = {}
        self._pending: deque[_PendingConnection] = deque()
        self._enabled = [False, False]
        self._sync_needed = False
        self._app = None
        self._slot_read_values = [
            self._initial_read_values(board.profile) for board in boards]
        self._notify_uuid_counts: dict[str, int] = {}
        for board in boards:
            for uuid in self._notify_characteristic_uuids(board.profile):
                self._notify_uuid_counts[uuid] = (
                    self._notify_uuid_counts.get(uuid, 0) + 1)

    @staticmethod
    def _notify_characteristic_uuids(profile: GattProfile) -> set[str]:
        return {
            char.uuid.lower()
            for service in profile.services
            for char in service.characteristics
            if "notify" in char.flags
        }

    @staticmethod
    def _initial_read_values(profile: GattProfile) -> dict[str, bytes]:
        return {
            char.uuid.lower(): char.initial_value
            for service in profile.services
            for char in service.characteristics
            if "read" in char.flags
        }

    @staticmethod
    def _first_uuid(profile: GattProfile, flag: str) -> str | None:
        for service in profile.services:
            for char in service.characteristics:
                if flag in char.flags:
                    return char.uuid
        return None

    @property
    def adapter(self) -> str:
        return self._adapter

    @property
    def assignments(self) -> dict[str, int]:
        """Snapshot used by diagnostics and hardware-independent tests."""
        return dict(self._assignments)

    def set_multi_connect(self, enabled: bool) -> None:
        self._multi_connect = enabled
        self._sync_needed = True
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
        logger.info("[%s] Two-board BLE peripheral started", self._adapter)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("[%s] Two-board BLE peripheral stopped", self._adapter)

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

    @staticmethod
    def _path_address(device: str) -> str | None:
        marker = "/dev_"
        if marker not in device:
            return None
        return device.rsplit(marker, 1)[1].replace("_", ":").upper()

    def _take_pending(self, address: str | None) -> _PendingConnection | None:
        if address:
            address = address.upper()
            for pending in self._pending:
                if pending.peer_address == address:
                    self._pending.remove(pending)
                    return pending
        return self._pending.popleft() if self._pending else None

    def _slot_for_access(self, device: str | None) -> int | None:
        if device and device in self._assignments:
            return self._assignments[device]
        pending = self._take_pending(self._path_address(device)) if device else None
        if pending is None:
            return None
        if device:
            self._assign_device(device, pending.slot)
        return pending.slot

    def _handle_gatt_write(self, data: bytes, device: str | None) -> None:
        slot = self._slot_for_access(device)
        if slot is None:
            logger.warning("Dropping GATT write without an advertising-set assignment")
            return
        replies = self._boards[slot].on_data(data)
        if self._app is None or not replies:
            return
        if isinstance(replies, (bytes, bytearray)):
            replies = [bytes(replies)]
        for reply in replies:
            self._publish_reply(slot, reply)

    def _publish_reply(self, slot: int,
                       reply: GattUpdate | bytes | bytearray) -> None:
        """Publish through this board's characteristics, never a neighbour's."""
        if self._app is None:
            return
        board = self._boards[slot]
        notify_uuid = self._first_uuid(board.profile, "notify")
        read_uuid = self._first_uuid(board.profile, "read")
        if isinstance(reply, GattUpdate):
            if reply.read_value is not None and read_uuid is not None:
                self._slot_read_values[slot][read_uuid.lower()] = reply.read_value
                self._app.set_read_value(read_uuid, reply.read_value)
            if reply.notification is not None and notify_uuid is not None:
                self._notify_slot(notify_uuid, reply.notification)
            return
        value = bytes(reply)
        if (len(value) > 1 and not value[1] & 0x80 and
                read_uuid is not None):
            self._slot_read_values[slot][read_uuid.lower()] = value
            self._app.set_read_value(read_uuid, value)
        if notify_uuid is not None:
            self._notify_slot(notify_uuid, value)

    def _notify_slot(self, uuid: str, value: bytes) -> None:
        """Notify only when this characteristic belongs to one virtual board.

        BlueZ broadcasts a characteristic Value change to every subscribed
        central and offers no destination option. Two Quantum instances share
        fff1, so emitting it would leak one board's roster to the other. Their
        device-scoped fff4 reads remain fully isolated and authoritative.
        """
        if self._notify_uuid_counts.get(uuid.lower(), 0) > 1:
            logger.debug(
                "Suppressing shared %s notification; use device-scoped read",
                uuid)
            return
        self._app.notify(uuid, value)

    def _read_for_device(self, device: str | None, uuid: str,
                         fallback: bytes) -> bytes:
        slot = self._slot_for_access(device)
        if slot is None:
            logger.warning("Reading shared GATT state without a board assignment")
            return fallback
        return self._slot_read_values[slot].get(uuid.lower(), fallback)

    def _configure_read_callbacks(self, app) -> None:
        for service in app.services:
            for characteristic in service.characteristics:
                if "read" in characteristic._flags:
                    characteristic.read_callback = (
                        lambda device, uuid=characteristic._uuid,
                        fallback=bytes(characteristic._value):
                        self._read_for_device(device, uuid, fallback))

    def _assign_device(self, device: str, slot: int) -> None:
        previous = self._assignments.get(device)
        if previous is not None and previous != slot:
            self._connected[previous].discard(device)
        self._assignments[device] = slot
        if device not in self._connected[slot]:
            was_empty = not self._connected[slot]
            self._connected[slot].add(device)
            if was_empty and self._boards[slot].on_connect:
                self._boards[slot].on_connect()
        if previous != slot:
            logger.info("%s assigned to board %d '%s'", device, slot + 1,
                        self._boards[slot].ble_name)
        self._sync_needed = True

    def _handle_hci_event(self, event: object) -> None:
        if isinstance(event, AdvertisingConnection):
            try:
                slot = self.ADV_HANDLES.index(event.advertising_handle)
            except ValueError:
                return
            self._connection_slots[event.connection_handle] = slot
            self._pending.append(_PendingConnection(
                slot, event.connection_handle, event.peer_address))
            # A connectable advertising set terminates when it accepts a link.
            self._enabled[slot] = False
            self._sync_needed = True
            logger.info("Connection 0x%04x accepted by board %d '%s'",
                        event.connection_handle, slot + 1,
                        self._boards[slot].ble_name)
        elif isinstance(event, Disconnection):
            slot = self._connection_slots.pop(event.connection_handle, None)
            self._pending = deque(
                item for item in self._pending
                if item.connection_handle != event.connection_handle)
            if slot is not None:
                self._sync_needed = True
                logger.debug("Connection 0x%04x for board %d ended (0x%02x)",
                             event.connection_handle, slot + 1, event.reason)

    async def _connected_devices(self) -> dict[str, str | None]:
        if self._bus is None:
            return {}
        try:
            reply = await self._bus.call(Message(
                destination=BLUEZ_SERVICE, path="/",
                interface="org.freedesktop.DBus.ObjectManager",
                member="GetManagedObjects"))
            if reply is None or reply.message_type == MessageType.ERROR:
                return {}
            devices: dict[str, str | None] = {}
            for path, ifaces in reply.body[0].items():
                properties = ifaces.get("org.bluez.Device1", {})
                connected = properties.get("Connected")
                if not self._owns_device_path(path) or not connected or not connected.value:
                    continue
                address = properties.get("Address")
                devices[path] = address.value.upper() if address else None
            return devices
        except Exception as exc:
            logger.debug("Could not enumerate connected centrals: %s", exc)
            return {}

    async def _reconcile_connections(self) -> None:
        devices = await self._connected_devices()
        known_connected = self._connected[0] | self._connected[1]
        for device in devices.keys() - known_connected:
            pending = self._take_pending(devices[device])
            if pending is not None:
                self._assign_device(device, pending.slot)
        paths = set(devices)
        for slot in range(2):
            gone = self._connected[slot] - paths
            if not gone:
                continue
            self._connected[slot].difference_update(gone)
            for device in gone:
                self._assignments.pop(device, None)
            if not self._connected[slot] and self._boards[slot].on_disconnect:
                self._boards[slot].on_disconnect()
            self._sync_needed = True

    def _desired_enabled(self, slot: int) -> bool:
        if self._multi_connect:
            return True
        pending = any(item.slot == slot for item in self._pending)
        return not self._connected[slot] and not pending

    async def _sync_advertising(self) -> None:
        for slot, handle in enumerate(self.ADV_HANDLES):
            desired = self._desired_enabled(slot)
            if desired == self._enabled[slot]:
                continue
            if not set_adv_sets_enabled([handle], desired, self._adapter):
                action = "enable" if desired else "disable"
                raise RuntimeError(
                    f"controller rejected {action} for board {slot + 1}")
            self._enabled[slot] = desired
        self._sync_needed = False

    async def _serve(self) -> None:
        if os.geteuid() != 0:
            logger.warning("Two-board BLE mode needs root privileges")
        supported = read_supported_adv_sets(self._adapter)
        if supported is not None and supported < 3:
            raise RuntimeError(
                f"{self._adapter} exposes only {supported} advertising sets; "
                "two-board mode needs handles 1 and 2")

        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        introspection = await self._bus.introspect(
            BLUEZ_SERVICE, self._adapter_path)
        adapter = self._bus.get_proxy_object(
            BLUEZ_SERVICE, self._adapter_path, introspection)
        props = adapter.get_interface("org.freedesktop.DBus.Properties")
        await props.call_set("org.bluez.Adapter1", "Powered", Variant("b", True))
        pairable_before = await props.call_get("org.bluez.Adapter1", "Pairable")
        discoverable_before = await props.call_get(
            "org.bluez.Adapter1", "Discoverable")
        await props.call_set("org.bluez.Adapter1", "Pairable", Variant("b", False))
        await props.call_set(
            "org.bluez.Adapter1", "Discoverable", Variant("b", False))

        app = build_application(self._profile, self._handle_gatt_write)
        self._app = app
        self._configure_read_callbacks(app)
        gatt_mgr = adapter.get_interface(GATT_MANAGER_IFACE)
        registered = False
        monitor_task: asyncio.Task | None = None
        configured: list[int] = []
        try:
            self._bus.export(app.path, app)
            for service in app.services:
                self._bus.export(service.path, service)
                for char in service.characteristics:
                    self._bus.export(char.path, char)
            await gatt_mgr.call_register_application(app.path, {})  # type: ignore
            registered = True

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

            self._monitor = HciMonitor(
                self._adapter_index, self._handle_hci_event)
            self._monitor.open()
            monitor_task = asyncio.create_task(self._monitor.run())

            for slot, (board, handle) in enumerate(
                    zip(self._boards, self.ADV_HANDLES)):
                if not configure_hardware_adv_set(
                        board.profile.advertised_uuid, board.ble_name,
                        board.address, handle, self._adapter):
                    raise RuntimeError(
                        f"controller rejected board {slot + 1} advertising set")
                configured.append(handle)
            if not set_adv_sets_enabled(
                    list(self.ADV_HANDLES), True, self._adapter):
                raise RuntimeError("controller rejected simultaneous advertising")
            self._enabled = [True, True]
            logger.info("[%s] Both virtual boards are advertising simultaneously",
                        self._adapter)

            while self._running:
                await asyncio.sleep(self.POLL_SECONDS)
                if monitor_task.done():
                    error = monitor_task.exception()
                    raise RuntimeError("Linux HCI monitor stopped") from error
                await self._reconcile_connections()
                if self._sync_needed:
                    await self._sync_advertising()
        finally:
            if monitor_task is not None:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.debug("HCI monitor stopped: %s", exc)
            if self._monitor is not None:
                self._monitor.close()
            if configured:
                set_adv_sets_enabled(configured, False, self._adapter)
            for handle in configured:
                remove_adv_set(handle, self._adapter)
            if registered:
                try:
                    await gatt_mgr.call_unregister_application(  # type: ignore
                        app.path)
                except Exception as exc:
                    logger.debug(
                        "Could not unregister multiplexed GATT app: %s", exc)
            try:
                await props.call_set(
                    "org.bluez.Adapter1", "Pairable", pairable_before)
                await props.call_set(
                    "org.bluez.Adapter1", "Discoverable", discoverable_before)
            except Exception:
                pass
            self._bus.disconnect()
            self._app = None
