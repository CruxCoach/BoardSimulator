"""Hardware-independent contract for two boards on one BLE adapter."""

import subprocess

from dbus_fast import Variant

from ble import advertising
from ble.gatt import (CharacteristicSpec, GattCharacteristic, GattProfile,
                      GattUpdate, ServiceSpec, build_application,
                      merge_profiles)
from ble.multiplex import MultiplexedBLEPeripheral, VirtualBoard


PROFILE_A = GattProfile(
    description="A", advertised_uuid="aaaa",
    services=(ServiceSpec("service-a", (
        CharacteristicSpec("write", ("write-without-response",), True),
    )),),
)
PROFILE_B = GattProfile(
    description="B", advertised_uuid="bbbb",
    services=(ServiceSpec("service-b"),),
)


def make_board(key: str, name: str, received: list[bytes] | None = None,
               connected: list[str] | None = None) -> VirtualBoard:
    return VirtualBoard(
        key=key, ble_name=name, profile=PROFILE_A,
        on_data=(received.append if received is not None else lambda data: None),
        on_connect=(lambda: connected.append(key)) if connected is not None else None,
    )


def test_static_random_addresses_are_stable_distinct_and_valid() -> None:
    first = advertising.static_random_address("board-a")
    assert first == advertising.static_random_address("board-a")
    assert first != advertising.static_random_address("board-b")
    assert len(first) == 6
    assert first[5] & 0xc0 == 0xc0


def test_virtual_advertising_programs_random_identity(monkeypatch) -> None:
    calls: list[tuple[str, list[str], str]] = []
    monkeypatch.setattr(advertising, "_hcitool_cmd",
                        lambda label, opcode, params, adapter="hci0":
                        calls.append((opcode, params, adapter)) or True)

    address = advertising.static_random_address("board-a")
    assert advertising.set_virtual_adv_data(
        "00000000-0000-0000-0000-000000000001",
        "Kilter Board#a001@3", address, "hci1")

    assert [opcode for opcode, _, _ in calls] == [
        "0x08 0x0039", "0x08 0x0035", "0x08 0x0036",
        "0x08 0x0037", "0x08 0x0038", "0x08 0x0039",
    ]
    assert calls[1][1] == ["00"] + [f"{octet:02x}" for octet in address]
    assert calls[2][1][10] == "01"  # own-address type: random
    assert {adapter for _, _, adapter in calls} == {"hci1"}


def test_two_hardware_sets_are_configured_and_enabled(monkeypatch) -> None:
    calls: list[tuple[str, list[str], str]] = []
    monkeypatch.setattr(advertising, "_hcitool_cmd",
                        lambda label, opcode, params, adapter="hci0":
                        calls.append((opcode, params, adapter)) or True)

    address = advertising.static_random_address("board-a")
    assert advertising.configure_hardware_adv_set(
        "00000000-0000-0000-0000-000000000001",
        "Kilter Board#a001@3", address, 2, "hci1")
    assert advertising.set_adv_sets_enabled([1, 2], True, "hci1")

    assert [opcode for opcode, _, _ in calls] == [
        "0x08 0x0036", "0x08 0x0035", "0x08 0x0037",
        "0x08 0x0038", "0x08 0x0039",
    ]
    assert calls[0][1][0] == "02"
    assert calls[1][1] == ["02"] + [f"{octet:02x}" for octet in address]
    assert calls[-1][1] == [
        "01", "02", "01", "00", "00", "00",
        "02", "00", "00", "00",
    ]
    assert {adapter for _, _, adapter in calls} == {"hci1"}


def test_supported_advertising_set_count_is_parsed(monkeypatch) -> None:
    output = """< HCI Command: ogf 0x08, ocf 0x003b, plen 0
> HCI Event: 0x0e plen 5
  01 3B 20 00 14
"""
    monkeypatch.setattr(advertising.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, output, ""))

    assert advertising.read_supported_adv_sets("hci0") == 20


def test_partially_configured_hardware_set_is_removed(monkeypatch) -> None:
    opcodes: list[str] = []

    def command(label, opcode, params, adapter="hci0") -> bool:
        opcodes.append(opcode)
        return opcode != "0x08 0x0035"

    monkeypatch.setattr(advertising, "_hcitool_cmd", command)
    address = advertising.static_random_address("board-a")

    assert not advertising.configure_hardware_adv_set(
        "00000000-0000-0000-0000-000000000001",
        "Kilter Board#a001@3", address, 1)
    assert opcodes[-1] == "0x08 0x003c"


def test_gatt_write_exposes_the_remote_device_path() -> None:
    writes: list[tuple[bytes, str | None]] = []
    char = GattCharacteristic(0, "uuid", ["write"], "/service")
    char.write_callback = lambda data, device: writes.append((data, device))

    char.WriteValue(b"abc", {
        "device": Variant("o", "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")})

    assert writes == [(b"abc", "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")]


def test_readable_characteristic_and_notification_value_are_stateful() -> None:
    char = GattCharacteristic(
        0, "uuid", ["read", "notify"], "/service", b"initial")
    assert bytes(char._value) == b"initial"
    char.StartNotify()
    assert char.Notifying
    char.notify(b"snapshot")
    assert bytes(char.Value) == b"snapshot"
    char.StopNotify()
    assert not char.Notifying


def test_profiles_are_merged_without_duplicate_services() -> None:
    merged = merge_profiles([PROFILE_A, PROFILE_B, PROFILE_A])
    assert [service.uuid for service in merged.services] == [
        "service-a", "service-b"]
    assert merged.services[0].characteristics[0].receives_writes


def test_each_device_is_routed_to_the_board_it_connected_through() -> None:
    first_data: list[bytes] = []
    second_data: list[bytes] = []
    connected: list[str] = []
    mux = MultiplexedBLEPeripheral([
        make_board("first", "Kilter Board#a001@3", first_data, connected),
        make_board("second", "Kilter Board#b002@3", second_data, connected),
    ])

    from ble.hci_monitor import AdvertisingConnection

    mux._handle_hci_event(AdvertisingConnection(1, 0x40, "AA:00:00:00:00:01"))
    mux._handle_gatt_write(b"A", "/org/bluez/hci0/dev_AA")
    mux._handle_hci_event(AdvertisingConnection(2, 0x41, "BB:00:00:00:00:02"))
    mux._handle_gatt_write(b"B", "/org/bluez/hci0/dev_BB")
    # Later writes keep their original assignment.
    mux._handle_gatt_write(b"B2", "/org/bluez/hci0/dev_BB")

    assert first_data == [b"A"]
    assert second_data == [b"B", b"B2"]
    assert connected == ["first", "second"]
    assert mux.assignments == {
        "/org/bluez/hci0/dev_AA": 0,
        "/org/bluez/hci0/dev_BB": 1,
    }


def test_two_quantum_instances_keep_fff4_and_fff5_device_scoped() -> None:
    import config
    from boards import board_for
    from protocols.quantum import (Command, EWALLS_ROUTE_DURATION_SECONDS,
                                   encode)
    from protocols.session import create_session

    board = board_for("quantum")
    xl = create_session(board, board.variant_for("xl"), None, None, None)
    small = create_session(board, board.variant_for("s"), None, None, None)
    mux = MultiplexedBLEPeripheral([
        VirtualBoard("xl", xl.ble_name, xl.gatt_profile, xl.feed),
        VirtualBoard("s", small.ble_name, small.gatt_profile, small.feed),
    ])
    app = build_application(mux._profile, mux._handle_gatt_write)
    mux._app = app
    mux._configure_read_callbacks(app)
    chars = {
        char._uuid: char
        for service in app.services
        for char in service.characteristics
    }
    state = chars[config.QUANTUM_STATE_UUID]
    identity = chars[config.QUANTUM_CONFIG_UUID]
    notify = chars[config.QUANTUM_NOTIFY_UUID]
    xl_device = "/org/bluez/hci0/dev_AA_00_00_00_00_01"
    small_device = "/org/bluez/hci0/dev_BB_00_00_00_00_02"

    from ble.hci_monitor import AdvertisingConnection
    mux._handle_hci_event(AdvertisingConnection(
        1, 0x40, "AA:00:00:00:00:01"))
    mux._handle_hci_event(AdvertisingConnection(
        2, 0x41, "BB:00:00:00:00:02"))

    # Identity reads may be the first GATT access. They must consume the
    # matching pending advertising assignment without needing a prior write.
    assert identity.read_callback is not None
    xl_identity = identity.read_callback(xl_device)
    small_identity = identity.read_callback(small_device)
    assert xl_identity[34] == 0
    assert small_identity[34] == 2

    xl_route = "00112233-4455-6677-8899-aabbccddeeff"
    small_route = "10213243-5465-7687-98a9-bacbdcedfe0f"
    user = "ffeeddcc-bbaa-9988-7766-554433221100"
    mux._handle_gatt_write(encode(
        Command.ACTIVATE_WALL, route_id=xl_route, user_id=user,
        color="#010203", duration=EWALLS_ROUTE_DURATION_SECONDS,
        diodes=[1003]), xl_device)
    mux._handle_gatt_write(encode(
        Command.ACTIVATE_WALL, route_id=small_route, user_id=user,
        color="#a0b0c0", duration=EWALLS_ROUTE_DURATION_SECONDS,
        diodes=[1003]), small_device)

    assert state.read_callback is not None
    xl_state = state.read_callback(xl_device)
    small_state = state.read_callback(small_device)
    assert xl_state[1:4] == b"\x47\x01\x00"
    assert small_state[1:4] == b"\x47\x01\x00"
    assert bytes.fromhex(xl_route.replace("-", "")) in xl_state
    assert bytes.fromhex(small_route.replace("-", "")) not in xl_state
    assert bytes.fromhex(small_route.replace("-", "")) in small_state
    assert bytes.fromhex(xl_route.replace("-", "")) not in small_state
    # BlueZ cannot target a Value notification to one central. Shared fff1 is
    # deliberately silent rather than leaking a neighbouring board's roster;
    # each board's authoritative fff4 remains available and isolated.
    assert bytes(notify.Value) == b""


def test_multiplex_forwards_replies_to_the_selected_profiles_characteristics() -> None:
    import config
    from boards import board_for
    from protocols.quantum import (Command, EWALLS_ROUTE_DURATION_SECONDS,
                                   encode)
    from protocols.session import create_session

    quantum_board = board_for("quantum")
    quantum = create_session(
        quantum_board, quantum_board.variant_for("xl"), None, None, None)
    other_profile = GattProfile(
        description="distinct reply profile", advertised_uuid="bbbb",
        services=(ServiceSpec("service-b", (
            CharacteristicSpec("other-write", ("write",), True),
            CharacteristicSpec("other-notify", ("notify",)),
            CharacteristicSpec("other-read", ("read",), initial_value=b"old"),
        )),),
    )
    mux = MultiplexedBLEPeripheral([
        VirtualBoard("quantum", quantum.ble_name,
                     quantum.gatt_profile, quantum.feed),
        VirtualBoard("other", "Other", other_profile,
                     lambda _data: [GattUpdate(b"other-event", b"other-state")]),
    ])
    app = build_application(mux._profile, mux._handle_gatt_write)
    mux._app = app
    mux._configure_read_callbacks(app)
    chars = {
        char._uuid: char
        for service in app.services
        for char in service.characteristics
    }
    quantum_device = "/org/bluez/hci0/dev_AA_00_00_00_00_01"
    other_device = "/org/bluez/hci0/dev_BB_00_00_00_00_02"

    from ble.hci_monitor import AdvertisingConnection
    mux._handle_hci_event(AdvertisingConnection(
        1, 0x40, "AA:00:00:00:00:01"))
    mux._handle_hci_event(AdvertisingConnection(
        2, 0x41, "BB:00:00:00:00:02"))

    route = "00112233-4455-6677-8899-aabbccddeeff"
    mux._handle_gatt_write(encode(
        Command.ACTIVATE_WALL, route_id=route,
        user_id="ffeeddcc-bbaa-9988-7766-554433221100",
        color="#010203", duration=EWALLS_ROUTE_DURATION_SECONDS,
        diodes=[1003]), quantum_device)

    assert bytes(chars[config.QUANTUM_NOTIFY_UUID].Value)[1:4] == b"\x41\x01\x00"
    assert bytes.fromhex(route.replace("-", "")) in (
        chars[config.QUANTUM_STATE_UUID].read_callback(quantum_device))
    assert bytes(chars["other-notify"].Value) == b""
    assert chars["other-read"].read_callback(other_device) == b"old"

    mux._handle_gatt_write(b"payload", other_device)

    assert bytes(chars["other-notify"].Value) == b"other-event"
    assert chars["other-read"].read_callback(other_device) == b"other-state"
    # The neighbour's reply must not overwrite Quantum state or notification.
    assert bytes(chars[config.QUANTUM_NOTIFY_UUID].Value)[1:4] == b"\x41\x01\x00"
    assert bytes.fromhex(route.replace("-", "")) in (
        chars[config.QUANTUM_STATE_UUID].read_callback(quantum_device))


def test_single_client_mode_reenables_only_after_disconnect() -> None:
    mux = MultiplexedBLEPeripheral([
        make_board("first", "Kilter Board#a001@3"),
        make_board("second", "Kilter Board#b002@3"),
    ])
    from ble.hci_monitor import AdvertisingConnection

    mux._handle_hci_event(AdvertisingConnection(1, 0x40, "AA:BB:CC:DD:EE:FF"))
    assert not mux._desired_enabled(0)
    assert mux._desired_enabled(1)

    pending = mux._take_pending("AA:BB:CC:DD:EE:FF")
    assert pending is not None
    mux._assign_device("/org/bluez/hci0/dev_AA", 0)
    assert not mux._desired_enabled(0)

    mux.set_multi_connect(True)
    assert mux._desired_enabled(0)
    assert mux._desired_enabled(1)
