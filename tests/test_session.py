"""Tests for the protocol abstraction: sessions, GATT profiles, fail-fast.

The session layer is what makes the two protocol families pluggable
behind the shared BLE peripheral — these tests pin down the per-family
GATT shape, BLE identity and decoder wiring, plus the option validation
and the BlueZ preflight error type.
"""

import pytest

import config
from boards import board_for
from protocols.session import (
    AURORA_GATT_PROFILE,
    MOONBOARD_GATT_PROFILE,
    QUANTUM_GATT_PROFILE,
    AuroraSession,
    MoonSession,
    QuantumSession,
    create_session,
)


def make_session(board_key: str, layout: str | None = None, **kwargs):
    board = board_for(board_key)
    variant = board.variant_for(layout)
    return create_session(
        board, variant,
        kwargs.get("size_id"), kwargs.get("api_level"), kwargs.get("serial"))


class TestGattProfiles:
    def test_aurora_profile_shape(self) -> None:
        # Two services: empty discovery service + UART with one writable RX.
        profile = AURORA_GATT_PROFILE
        assert profile.advertised_uuid == config.AURORA_ADVERTISING_SERVICE_UUID
        assert [s.uuid for s in profile.services] == [
            config.AURORA_ADVERTISING_SERVICE_UUID, config.UART_SERVICE_UUID]
        discovery, uart = profile.services
        assert discovery.characteristics == ()
        (rx,) = uart.characteristics
        assert rx.uuid == config.RX_CHARACTERISTIC_UUID
        assert rx.receives_writes
        assert set(rx.flags) == {"write", "write-without-response"}

    def test_moonboard_profile_shape(self) -> None:
        # ONE service (NUS, advertised itself) with RX write + TX notify stub.
        profile = MOONBOARD_GATT_PROFILE
        assert profile.advertised_uuid == config.UART_SERVICE_UUID
        (uart,) = profile.services
        assert uart.uuid == config.UART_SERVICE_UUID
        rx, tx = uart.characteristics
        assert rx.uuid == config.RX_CHARACTERISTIC_UUID and rx.receives_writes
        assert tx.uuid == config.TX_CHARACTERISTIC_UUID
        assert tx.flags == ("notify",) and not tx.receives_writes

    def test_exactly_one_rx_characteristic_per_profile(self) -> None:
        for profile in (AURORA_GATT_PROFILE, MOONBOARD_GATT_PROFILE,
                        QUANTUM_GATT_PROFILE):
            rx_count = sum(
                1 for svc in profile.services
                for char in svc.characteristics if char.receives_writes)
            assert rx_count == 1

    def test_quantum_profile_shape(self) -> None:
        profile = QUANTUM_GATT_PROFILE
        assert profile.advertised_uuid == config.QUANTUM_SERVICE_UUID
        (service,) = profile.services
        write, notify, state, identity = service.characteristics
        assert write.uuid == config.QUANTUM_WRITE_UUID and write.receives_writes
        assert notify.uuid == config.QUANTUM_NOTIFY_UUID
        assert notify.flags == ("notify",)
        assert state.uuid == config.QUANTUM_STATE_UUID and state.flags == ("read",)
        assert identity.uuid == config.QUANTUM_CONFIG_UUID and identity.flags == ("read",)


class TestSessionFactory:
    def test_aurora_boards_get_aurora_sessions(self) -> None:
        for key in ("kilter", "tension", "grasshopper", "decoy", "soill",
                    "touchstone"):
            session = make_session(key)
            assert isinstance(session, AuroraSession), key
            assert session.gatt_profile is AURORA_GATT_PROFILE

    def test_moonboard_gets_moon_session(self) -> None:
        session = make_session("moonboard")
        assert isinstance(session, MoonSession)
        assert session.gatt_profile is MOONBOARD_GATT_PROFILE

    def test_quantum_gets_quantum_session(self) -> None:
        session = make_session("quantum")
        assert isinstance(session, QuantumSession)
        assert session.gatt_profile.services[0].uuid == config.QUANTUM_SERVICE_UUID
        assert session.ble_name.startswith("QB_")
        identity = session.gatt_profile.services[0].characteristics[-1].initial_value
        assert len(identity) == 41 and identity[34] == 0

    def test_quantum_fff5_identity_has_current_model_type_mapping(self) -> None:
        expected = {"xl": 0, "m": 1, "s": 2, "belay": 3, "l": 4}
        for layout, type_byte in expected.items():
            session = make_session("quantum", layout)
            identity = session.gatt_profile.services[0].characteristics[-1].initial_value
            assert len(identity) == 41
            assert identity[34] == type_byte
            assert identity[:24].rstrip(b"\0") == session.ble_name.encode("ascii")
            assert identity[24:30] == bytes.fromhex("020000000001")
            assert int.from_bytes(identity[35:37], "big") == session.variant.columns
            assert int.from_bytes(identity[37:39], "big") == session.variant.rows
            assert identity[30:34] == b"\0\0\0\0"  # unknown, not invented

    def test_aurora_ble_name_defaults(self) -> None:
        session = make_session("kilter")
        assert session.ble_name == "Kilter Board#0001@3"

    def test_aurora_ble_name_overrides(self) -> None:
        session = make_session("tension", "tb2", api_level=2, serial="abc1")
        assert session.ble_name == "Tension Board#abc1@2"
        assert session.api_level == 2

    def test_moonboard_ble_name(self) -> None:
        assert make_session("moonboard").ble_name == "MoonBoard"

    @pytest.mark.parametrize("kwargs", [
        {"size_id": 10}, {"api_level": 3}, {"serial": "0001"},
    ])
    def test_moonboard_rejects_aurora_options(self, kwargs) -> None:
        with pytest.raises(ValueError, match="Aurora-protocol"):
            make_session("moonboard", **kwargs)

    def test_aurora_size_default_comes_from_variant(self) -> None:
        session = make_session("kilter", "homewall")
        assert session.geometry.size.id == 21

    def test_aurora_invalid_size_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown product size"):
            make_session("kilter", size_id=99)


class TestSessionDecoding:
    """feed() must route GATT writes into the right family's decoder."""

    def test_aurora_session_decodes_climb(self) -> None:
        from protocols.aurora_encoder import encode_climb, hex_to_color_byte

        session = make_session("kilter")
        led_map = session.geometry.placement_led_map()
        role = session.geometry.roles[12]  # start
        placement = sorted(led_map)[0]
        chunks = encode_climb(
            [(led_map[placement], hex_to_color_byte(role.led_color))],
            api_level=3)
        for chunk in chunks:
            session.feed(chunk)

        holds = session.state.get_holds()
        assert list(holds) == [led_map[placement]]
        (rgb,) = holds.values()
        assert session.resolver.resolve(*rgb).id == 12

    def test_moon_session_decodes_frame(self) -> None:
        session = make_session("moonboard")
        session.feed(b"l#S0,P1,E197#")
        holds = session.state.get_holds()
        assert (0, 0) in holds and (10, 17) in holds
        assert len(holds) == 3

    def test_quantum_session_decodes_and_preserves_state_on_reconnect(self) -> None:
        from protocols.quantum import Command, encode
        session = make_session("quantum", "s")
        session.feed(encode(
            Command.ACTIVATE_WALL,
            route_id="00112233-4455-6677-8899-aabbccddeeff",
            user_id="ffeeddcc-bbaa-9988-7766-554433221100",
            color="#010203", diodes=[1003]))
        assert list(session.state.get_holds()) == [1003]
        session.disconnect()
        assert list(session.state.get_holds()) == [1003]

    def test_moon_session_respects_variant_grid(self) -> None:
        # Mini 2020: 12-row serpentine — E131 is the top of column 10.
        session = make_session("moonboard", "mini-2020")
        session.feed(b"l#E131#")
        assert list(session.state.get_holds()) == [(10, 11)]

    def test_moon_variant_switch_swaps_decoder_and_clears(self) -> None:
        board = board_for("moonboard")
        session = make_session("moonboard")  # 2016, 18 rows
        session.feed(b"l#S17#")  # column 0 top on an 18-row board
        assert list(session.state.get_holds()) == [(0, 17)]

        session.switch_variant(board.variant_for("mini-2020"))
        assert session.state.get_holds() == {}  # cleared on switch
        session.feed(b"l#S11#")  # column 0 top on a 12-row board
        assert list(session.state.get_holds()) == [(0, 11)]


class TestFailFast:
    """BlueZ preflight: a clear error instead of silent hanging."""

    def test_preflight_raises_without_bluez(self) -> None:
        # This test box has no BlueZ on the system bus — exactly the
        # environment the fail-fast path exists for. On a machine WITH
        # BlueZ + adapter the preflight passes and this test is skipped.
        from ble.peripheral import BlueZUnavailableError, preflight_check

        try:
            preflight_check()
        except BlueZUnavailableError as exc:
            assert str(exc)  # carries a human-readable reason
        else:
            pytest.skip("BlueZ available on this machine — preflight passes")

    def test_error_is_a_runtime_error(self) -> None:
        from ble.peripheral import BlueZUnavailableError

        assert issubclass(BlueZUnavailableError, RuntimeError)
