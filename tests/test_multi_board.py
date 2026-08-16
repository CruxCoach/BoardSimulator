"""Hardware-independent contract for two boards on one BLE adapter."""

from dbus_fast import Variant

from ble import advertising
from ble.gatt import (CharacteristicSpec, GattCharacteristic, GattProfile,
                      ServiceSpec, merge_profiles)
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


def test_gatt_write_exposes_the_remote_device_path() -> None:
    writes: list[tuple[bytes, str | None]] = []
    char = GattCharacteristic(0, "uuid", ["write"], "/service")
    char.write_callback = lambda data, device: writes.append((data, device))

    char.WriteValue(b"abc", {
        "device": Variant("o", "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")})

    assert writes == [(b"abc", "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")]


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

    mux._current_slot = 0
    mux._handle_gatt_write(b"A", "/org/bluez/hci0/dev_AA")
    mux._current_slot = 1
    mux._handle_gatt_write(b"B", "/org/bluez/hci0/dev_BB")
    # Rotation no longer matters after the central has an assignment.
    mux._current_slot = 0
    mux._handle_gatt_write(b"B2", "/org/bluez/hci0/dev_BB")

    assert first_data == [b"A"]
    assert second_data == [b"B", b"B2"]
    assert connected == ["first", "second"]
    assert mux.assignments == {
        "/org/bluez/hci0/dev_AA": 0,
        "/org/bluez/hci0/dev_BB": 1,
    }


def test_exclusive_mode_advertises_only_the_unconnected_board() -> None:
    mux = MultiplexedBLEPeripheral([
        make_board("first", "Kilter Board#a001@3"),
        make_board("second", "Kilter Board#b002@3"),
    ])
    mux._current_slot = 0
    mux._assign_device("/org/bluez/hci0/dev_AA", 0)

    assert mux._advertisable_slots() == [1]
    assert mux._next_slot() == 1

    mux.set_multi_connect(True)
    assert mux._advertisable_slots() == [0, 1]
