"""BLE peripheral that emulates a climbing board over BlueZ.

GATT services via the BlueZ D-Bus API (dbus_fast), shaped by the
protocol family's :class:`~ble.gatt.GattProfile`. Advertising via
Adapter.Discoverable + Extended Advertising HCI commands to inject the
family's service UUID into the advertising data; the board identity
travels in the advertised name.

Fail-fast: :func:`preflight_check` verifies that BlueZ (``org.bluez``)
is reachable and a powered-capable adapter exists BEFORE the peripheral
thread starts — a box without Bluetooth aborts with a clear error
instead of hanging silently (a known wart of the predecessor
simulators). ``--list`` and the test suite never touch this module's
runtime path.

Must be run as root (sudo venv/bin/python main.py).
"""

import asyncio
import logging
import os
import subprocess
import threading
from typing import Callable

from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Message, MessageType, Variant

from ble.advertising import set_extended_adv_data
from ble.gatt import GattProfile, build_application

logger = logging.getLogger(__name__)

# BlueZ D-Bus constants
BLUEZ_SERVICE = "org.bluez"
ADAPTER_PATH = "/org/bluez/hci0"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"


class BlueZUnavailableError(RuntimeError):
    """BlueZ or a Bluetooth adapter is missing — live BLE cannot work."""


async def _async_preflight() -> None:
    """Verify org.bluez is on the system bus and hci0 exists."""
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    except Exception as exc:
        raise BlueZUnavailableError(
            "cannot connect to the system D-Bus — is this a Linux box "
            f"with D-Bus running? ({exc})"
        ) from exc
    try:
        reply = await bus.call(Message(
            destination=BLUEZ_SERVICE,
            path="/",
            interface="org.freedesktop.DBus.ObjectManager",
            member="GetManagedObjects",
        ))
        if reply is None or reply.message_type == MessageType.ERROR:
            error = reply.error_name if reply else "no reply"
            raise BlueZUnavailableError(
                f"BlueZ (org.bluez) is not available on the system bus "
                f"({error}) — install/start the bluetooth service"
            )
        managed = reply.body[0]
        adapters = [path for path, ifaces in managed.items()
                    if "org.bluez.Adapter1" in ifaces]
        if not adapters:
            raise BlueZUnavailableError(
                "BlueZ is running but no Bluetooth adapter was found "
                "(expected org.bluez.Adapter1 under /org/bluez) — this "
                "machine has no usable Bluetooth hardware"
            )
        if ADAPTER_PATH not in adapters:
            raise BlueZUnavailableError(
                f"no adapter at {ADAPTER_PATH} (found: {adapters}) — "
                "the simulator currently drives hci0 only"
            )
    finally:
        bus.disconnect()


def preflight_check() -> None:
    """Raise :class:`BlueZUnavailableError` unless live BLE can work.

    Called synchronously from main BEFORE the peripheral thread starts,
    so a machine without BlueZ/adapter aborts immediately with a clear
    message instead of advertising into the void.
    """
    asyncio.run(_async_preflight())


class BLEPeripheral:
    """BLE peripheral emulating one climbing board.

    GATT shape and advertised service UUID come from the protocol
    family's :class:`GattProfile`; the advertised name carries the
    board identity.
    """

    def __init__(self, ble_name: str, profile: GattProfile,
                 on_data: Callable[[bytes], None],
                 on_connect: Callable[[], None] | None = None,
                 on_disconnect: Callable[[], None] | None = None,
                 on_fatal: Callable[[BaseException], None] | None = None) -> None:
        self._ble_name = ble_name
        self._profile = profile
        self._on_data = on_data
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_fatal = on_fatal
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._bus: MessageBus | None = None
        self._connected = False
        # Fallback connection tracking for devices that D-Bus doesn't track
        # (e.g., Android 9 with random resolvable BLE addresses).
        self._gatt_active = False

    def _handle_gatt_write(self, data: bytes) -> None:
        """Wrapper around the user's on_data callback with connection tracking.

        When BlueZ doesn't create a D-Bus Device1 object for a connected device
        (happens with Android 9's random resolvable addresses), the D-Bus
        PropertiesChanged signal never fires. But GATT WriteValue IS called,
        so we use it to detect these "phantom" connections. A periodic poll
        of `hcitool con` then detects when the device disconnects.
        """
        if not self._connected and not self._gatt_active:
            self._gatt_active = True
            logger.info("GATT write from untracked device — enabling HCI poll fallback")
        self._on_data(data)

    async def _check_hci_connections(self) -> bool:
        """Check if there are active LE connections via hcitool."""
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["hcitool", "con"], capture_output=True, text=True, timeout=3
                )
            )
            return "< LE" in result.stdout
        except Exception as e:
            logger.debug("hcitool con check failed: %s", e)
            return False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ble-peripheral")
        self._thread.start()
        logger.info("BLE peripheral thread started")

    def stop(self) -> None:
        # Cooperative shutdown: _serve()'s poll loop checks self._running and
        # returns on its own, so run_until_complete() finishes normally and
        # the bus/loop close cleanly. A forced loop.stop() here interrupted
        # run_until_complete mid-await and raised "Event loop stopped before
        # Future completed" — harmless, but logged as a scary traceback on
        # every board switch.
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("BLE peripheral stopped")

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as exc:
            logger.exception("BLE peripheral event loop error")
            # Fail fast: surface the error to the host instead of leaving
            # a dead peripheral thread behind a live-looking GUI.
            if self._on_fatal is not None and self._running:
                self._on_fatal(exc)
        finally:
            self._loop.close()

    async def _serve(self) -> None:
        board_name = self._ble_name
        logger.info("Starting BLE server as '%s' (%s)",
                    board_name, self._profile.description)

        if os.geteuid() != 0:
            logger.warning(
                "Not running as root! Run with: sudo venv/bin/python main.py")

        # -- Connect to system D-Bus -----------------------------------
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        # -- Configure adapter -----------------------------------------
        introspection = await self._bus.introspect(BLUEZ_SERVICE, ADAPTER_PATH)
        adapter = self._bus.get_proxy_object(BLUEZ_SERVICE, ADAPTER_PATH, introspection)
        adapter_props = adapter.get_interface("org.freedesktop.DBus.Properties")

        try:
            await adapter_props.call_set(
                "org.bluez.Adapter1", "Powered", Variant("b", True))
            logger.info("Adapter powered on")
        except Exception as e:
            logger.warning("Could not power on adapter: %s", e)

        try:
            await adapter_props.call_set(
                "org.bluez.Adapter1", "Alias", Variant("s", board_name))
            logger.info("Adapter alias set to '%s'", board_name)
        except Exception as e:
            logger.warning("Could not set Adapter.Alias: %s", e)

        # -- Register GATT application ---------------------------------
        app = build_application(self._profile, self._handle_gatt_write)

        # Export all GATT objects on D-Bus
        self._bus.export(app.path, app)
        for svc in app.services:
            self._bus.export(svc.path, svc)
            for char in svc.characteristics:
                self._bus.export(char.path, char)

        gatt_mgr = adapter.get_interface(GATT_MANAGER_IFACE)
        await gatt_mgr.call_register_application(app.path, {})  # type: ignore
        logger.info("GATT application registered (%s)", self._profile.description)

        # -- Start advertising -----------------------------------------
        # Step 1: Set Discoverable (starts legacy advertising on handle 0x00)
        try:
            await adapter_props.call_set(
                "org.bluez.Adapter1", "DiscoverableTimeout", Variant("u", 0))
            await adapter_props.call_set(
                "org.bluez.Adapter1", "Discoverable", Variant("b", True))
            logger.info("Adapter.Discoverable set")
        except Exception as e:
            logger.warning("Could not set Discoverable: %s", e)

        # Step 2: Wait for BlueZ to finish programming the controller
        await asyncio.sleep(1.0)

        # Step 3: Overwrite handle 0x00's advertising data via Extended
        # Advertising HCI commands so the advertised service UUID is the
        # family's discovery UUID (Aurora: the 4488B571 discovery service;
        # MoonBoard: the Nordic UART Service itself).
        uuid = self._profile.advertised_uuid
        if set_extended_adv_data(uuid, board_name):
            logger.info("Service UUID injected into advertising data!")
        else:
            logger.warning(
                "Could not inject UUID. Device visible as '%s' "
                "but apps that filter on the UUID may not discover it.",
                board_name)

        logger.info("Advertising UUID: %s", uuid)
        logger.info("BLE peripheral ready — waiting for connections")

        # -- Monitor connection state via D-Bus signals ----------------
        def _on_properties_changed(msg: Message) -> None:
            if msg.message_type != MessageType.SIGNAL:
                return
            if msg.member != "PropertiesChanged":
                return
            path = msg.path or ""
            if not path.startswith(ADAPTER_PATH + "/dev_"):
                return

            args = msg.body
            if not args or len(args) < 2:
                return
            iface = args[0]
            changed = args[1]
            if iface != "org.bluez.Device1":
                return
            if "Connected" not in changed:
                return

            connected = changed["Connected"].value
            if connected and not self._connected:
                self._connected = True
                self._gatt_active = False  # D-Bus is tracking — disable fallback
                logger.info("Device connected: %s", path)
                if self._on_connect:
                    self._on_connect()
            elif not connected and self._connected:
                self._connected = False
                self._gatt_active = False
                logger.info("Device disconnected: %s", path)
                if self._on_disconnect:
                    self._on_disconnect()
                # Restart advertising so the next client can connect
                asyncio.ensure_future(self._restart_advertising(
                    adapter_props, uuid, board_name))

        self._bus.add_message_handler(_on_properties_changed)

        # Subscribe to PropertiesChanged signals from BlueZ
        await self._bus.call(Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="AddMatch",
            signature="s",
            body=[
                "type='signal',"
                "sender='org.bluez',"
                "interface='org.freedesktop.DBus.Properties',"
                "member='PropertiesChanged',"
                f"path_namespace='{ADAPTER_PATH}'"
            ],
        ))
        logger.info("D-Bus signal monitoring active (connect/disconnect)")

        # -- Keep running ----------------------------------------------
        poll_ticks = 0
        while self._running:
            await asyncio.sleep(0.5)
            poll_ticks += 1

            # Every 2s: check for disconnects that D-Bus missed
            if poll_ticks >= 4:
                poll_ticks = 0
                if self._gatt_active and not self._connected:
                    has_conn = await self._check_hci_connections()
                    if not has_conn:
                        self._gatt_active = False
                        logger.info("Fallback: disconnect detected via HCI poll "
                                    "(D-Bus missed this device)")
                        if self._on_disconnect:
                            self._on_disconnect()
                        asyncio.ensure_future(self._restart_advertising(
                            adapter_props, uuid, board_name))

    async def _restart_advertising(self, adapter_props, uuid: str, name: str) -> None:
        """Restart advertising after a disconnect so new clients can connect."""
        logger.info("Restarting advertising after disconnect...")
        await asyncio.sleep(1.0)  # Give BlueZ time to clean up

        # Re-enable Discoverable (BlueZ may have turned it off)
        try:
            await adapter_props.call_set(
                "org.bluez.Adapter1", "Discoverable", Variant("b", True))
            logger.info("Adapter.Discoverable re-enabled")
        except Exception as e:
            logger.warning("Could not re-enable Discoverable: %s", e)

        await asyncio.sleep(0.5)

        # Re-inject UUID into advertising data
        if set_extended_adv_data(uuid, name):
            logger.info("Advertising restarted with UUID")
        else:
            logger.warning("Could not re-inject UUID into advertising")
