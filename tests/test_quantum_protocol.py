"""Quantum clean-room protocol, state and reconnect contracts."""

from __future__ import annotations

import json

import pytest

from board_state import QuantumBoardState
from protocols.quantum import (CHUNK_LIMITS, CURRENT_COMMANDS, Command,
                               EWALLS_ROUTE_DURATION_SECONDS, ProtocolEvent,
                               QuantumProtocol, ResponsePolicy, WireVersion,
                               crc16_modbus, encode as quantum_encode,
                               encode_broadcast, encode_chunks as quantum_chunks,
                               encode_exception, uuid_from_bytes, uuid_to_bytes)


ROUTE_UUID = "00112233-4455-6677-8899-aabbccddeeff"
USER_UUID = "ffeeddcc-bbaa-9988-7766-554433221100"


def encode(command, **kwargs):
    """The original regression suite pins the explicit 1.44 dialect."""
    return quantum_encode(command, wire=WireVersion.EWALLS_1_44, **kwargs)


def encode_chunks(command, **kwargs):
    return quantum_chunks(command, wire=WireVersion.EWALLS_1_44, **kwargs)


GOLDEN = {
    "off_all": "0145000100009dc5",
    "on_all": "0164112233010285c1",
    "activate": (
        "0141726f7574652d31323334353637383930757365722d3132333435363738393031"
        "102030012c0104000101025773"
    ),
    "editor": "016500ff0000010403e9181a9992",
}


def make_protocol(addresses=tuple(range(1000)), reject=None,
                  response_policy=ResponsePolicy.OBSERVED_XL):
    state = QuantumBoardState(addresses)
    events: list[ProtocolEvent] = []
    protocol = QuantumProtocol(
        state.apply, events.append, reject or set(),
        response_policy=response_policy)
    return protocol, state, events


def test_modbus_standard_check_vector() -> None:
    assert crc16_modbus(b"123456789") == 0x4B37


def test_golden_vectors_match_ewalls_144_reference_encoder() -> None:
    assert encode(Command.TURN_OFF_ALL).hex() == GOLDEN["off_all"]
    assert encode(Command.TURN_ON_ALL, color="#112233", duration=258).hex() == GOLDEN["on_all"]
    assert encode(
        Command.ACTIVATE_WALL, route_id="route-1234567890",
        user_id="user-12345678901", color="#102030", duration=300,
        enable_animation=1, diodes=[1, 258]).hex() == GOLDEN["activate"]
    assert encode(Command.SET_START_HOLDS, color="#00ff00",
                  animation_speed=1, diodes=[1001, 6170]).hex() == GOLDEN["editor"]


@pytest.mark.parametrize("command", list(Command))
def test_every_command_round_trips_and_acknowledges(command: Command) -> None:
    protocol, _state, events = make_protocol()
    kwargs = {"diodes": [1, 258]} if command in CHUNK_LIMITS else {}
    protocol.feed(encode(command, **kwargs))
    assert events[-1] == ProtocolEvent(True, "ACK", command)


def test_arbitrary_ble_fragmentation() -> None:
    protocol, state, _ = make_protocol()
    frame = encode(Command.ACTIVATE_WALL, route_id="r", user_id="u",
                   color="#112233", diodes=[1, 258, 999])
    for byte in frame:
        protocol.feed(bytes((byte,)))
    assert sorted(state.get_holds()) == [1, 258, 999]


def test_multi_chunk_activate_is_merged_not_replaced() -> None:
    protocol, state, events = make_protocol(tuple(range(200)))
    frames = encode_chunks(Command.ACTIVATE_WALL, route_id="route", user_id="user",
                           color="#00aaee", diodes=range(150))
    assert len(frames) == 2
    for frame in frames:
        protocol.feed(frame)
    assert len(state.get_holds()) == 150
    assert [event.code for event in events] == ["ACK", "ACK"]


def test_led_id_u32_addresses_decode() -> None:
    protocol, state, _ = make_protocol()
    addresses = [0x01010001, 0x01020002]
    protocol.feed(encode(Command.ACTIVATE_WALL_LED_ID, route_id="r",
                         color="#abcdef", diodes=addresses))
    assert sorted(state.get_holds()) == addresses


def test_crc_error_is_nacked_state_safe_and_stream_recovers() -> None:
    protocol, state, events = make_protocol()
    good = encode(Command.ACTIVATE_WALL, route_id="good", diodes=[7])
    bad = bytearray(encode(Command.ACTIVATE_WALL, route_id="bad", diodes=[6]))
    bad[-1] ^= 0x80
    protocol.feed(bytes(bad) + good)
    assert list(state.get_holds()) == [7]
    assert events[0].code == "CRC" and not events[0].ok
    assert events[1] == ProtocolEvent(True, "ACK", Command.ACTIVATE_WALL)


def test_fault_profile_rejects_command_without_state_change() -> None:
    protocol, state, events = make_protocol(reject={Command.TURN_ON_ALL})
    protocol.feed(encode(Command.TURN_ON_ALL, color="#ffffff"))
    assert state.get_holds() == {}
    assert events[-1].code == "SLAVE_DEVICE_FAILURE"


def test_disconnect_drops_partial_frame_but_preserves_controller_state() -> None:
    protocol, state, _ = make_protocol()
    first = encode(Command.ACTIVATE_WALL, route_id="r1", diodes=[1])
    protocol.feed(first)
    partial = encode(Command.ACTIVATE_WALL, route_id="lost", diodes=[2])
    protocol.feed(partial[:10])
    protocol.reset_transport()  # disconnect/reconnect
    assert list(state.get_holds()) == [1]
    protocol.feed(encode(Command.ACTIVATE_WALL, route_id="r2", diodes=[3]))
    assert sorted(state.get_holds()) == [1, 3]


def test_off_change_params_swipe_user_and_editor_commands() -> None:
    protocol, state, _ = make_protocol(tuple(range(6)))
    protocol.feed(encode(Command.ACTIVATE_WALL, route_id="r1", user_id="u",
                         color="#010203", diodes=[1, 2]))
    protocol.feed(encode(Command.CHANGE_ROUTE_PARAMS, route_id="r1",
                         color="#aabbcc", duration=20))
    assert (state.get_holds()[1].r, state.get_holds()[1].g,
            state.get_holds()[1].b) == (0xaa, 0xbb, 0xcc)
    protocol.feed(encode(Command.BOARD_SWIPE, route_id="r2", user_id="u",
                         color="#112233", diodes=[3]))
    assert sorted(state.get_holds()) == [3]
    protocol.feed(encode(Command.SET_START_HOLDS, color="#00ff00", diodes=[0]))
    protocol.feed(encode(Command.SET_STEP_HOLDS, color="#0000ff", diodes=[1]))
    protocol.feed(encode(Command.SET_FINISH_HOLDS, color="#ff0000", diodes=[2]))
    assert {state.get_holds()[i].role for i in (0, 1, 2)} == {
        "start", "step", "finish"}
    protocol.feed(encode(Command.TURN_OFF_BY_USER, user_id="u"))
    assert 3 not in state.get_holds()
    protocol.feed(encode(Command.TURN_OFF_ALL))
    assert state.get_holds() == {}


def test_turn_on_all_and_route_list_request() -> None:
    protocol, state, _ = make_protocol((10, 11, 12))
    protocol.feed(encode(Command.TURN_ON_ALL, color="#101010"))
    assert sorted(state.get_holds()) == [10, 11, 12]
    before = state.get_holds()
    protocol.feed(encode(Command.REQUEST_USER_ROUTE_LIST,
                         internal_index=2, count_of_routes=5))
    assert state.get_holds() == before


def test_fragmented_legacy_json_and_concatenated_objects() -> None:
    protocol, state, events = make_protocol()
    first = json.dumps({"command": "ACTIVATE_WALL", "routeId": "legacy",
                        "color": "#123456", "diodes": [4, 5]}).encode()
    second = json.dumps({"command": 66, "routeId": "legacy"}).encode()
    protocol.feed(first[:12])
    protocol.feed(first[12:] + second)
    assert state.get_holds() == {}
    assert [event.code for event in events] == ["LEGACY_ACK", "LEGACY_ACK"]


def test_two_quantum_sessions_never_share_route_or_transport_state() -> None:
    from boards import board_for
    from protocols.session import create_session

    board = board_for("quantum")
    variant = board.variant_for("s")
    first = create_session(board, variant, None, None, None)
    second = create_session(board, variant, None, None, None)
    frame_a = encode(Command.ACTIVATE_WALL, route_id="a", diodes=[1003])
    frame_b = encode(Command.ACTIVATE_WALL, route_id="b", diodes=[1004])
    first.feed(frame_a[:9])
    second.feed(frame_b)
    assert first.state.get_holds() == {}
    assert list(second.state.get_holds()) == [1004]
    first.feed(frame_a[9:])
    assert list(first.state.get_holds()) == [1003]
    assert list(second.state.get_holds()) == [1004]


CURRENT_GOLDEN = {
    "off_all": "014500010000c59d",
    "on_all": "01641122330102c185",
    "request": "0147073252",
    "activate": (
        "014100112233445566778899aabbccddeeffffeeddccbbaa99887766554433221100"
        "102030012c0104000101022a10"
    ),
}


def test_current_2014_golden_vectors_are_big_endian_crc_and_raw_uuid() -> None:
    assert quantum_encode(Command.TURN_OFF_ALL).hex() == CURRENT_GOLDEN["off_all"]
    assert quantum_encode(Command.TURN_ON_ALL, color="#112233", duration=258).hex() == CURRENT_GOLDEN["on_all"]
    assert quantum_encode(Command.REQUEST_USER_ROUTE_LIST, row_number=7).hex() == CURRENT_GOLDEN["request"]
    assert quantum_encode(
        Command.ACTIVATE_WALL, route_id=ROUTE_UUID, user_id=USER_UUID,
        color="#102030", duration=300, enable_animation=1,
        diodes=[1, 258]).hex() == CURRENT_GOLDEN["activate"]


def test_current_uuid_validation_and_roundtrip() -> None:
    assert uuid_from_bytes(uuid_to_bytes(ROUTE_UUID)) == ROUTE_UUID
    with pytest.raises(ValueError, match="32 hexadecimal"):
        quantum_encode(Command.ACTIVATE_WALL, route_id="short", user_id=USER_UUID)
    with pytest.raises(ValueError, match="hexadecimal"):
        uuid_to_bytes("z" * 32)


@pytest.mark.parametrize("command", sorted(CURRENT_COMMANDS, key=int))
def test_every_current_command_roundtrips(command: Command) -> None:
    protocol, _state, events = make_protocol()
    kwargs = {}
    if command in (Command.ACTIVATE_WALL, Command.BOARD_SWIPE):
        kwargs.update(route_id=ROUTE_UUID, user_id=USER_UUID, diodes=[1, 258])
    elif command == Command.TURN_OFF_BY_ROUTE:
        kwargs["route_id"] = ROUTE_UUID
    elif command == Command.TURN_OFF_BY_USER:
        kwargs["user_id"] = USER_UUID
    protocol.feed(quantum_encode(command, **kwargs))
    assert events[-1] == ProtocolEvent(True, "ACK", command)


def test_current_multi_chunk_crc_error_and_recovery() -> None:
    protocol, state, events = make_protocol(tuple(range(200)))
    frames = quantum_chunks(
        Command.ACTIVATE_WALL, route_id=ROUTE_UUID, user_id=USER_UUID,
        color="#00aaee", diodes=range(150))
    damaged = bytearray(frames[0])
    damaged[-1] ^= 1
    replies = protocol.feed(bytes(damaged) + b"".join(frames))
    assert len(state.get_holds()) == 150
    assert events[0].code == "CRC"
    assert replies[0] == encode_exception(Command.ACTIVATE_WALL, 3)


def test_current_broadcast_shapes_match_2014_parser_contract() -> None:
    protocol, _state, _events = make_protocol(
        response_policy=ResponsePolicy.SYNTHETIC_STATE)
    replies = protocol.feed(quantum_encode(
        Command.ACTIVATE_WALL, route_id=ROUTE_UUID, user_id=USER_UUID,
        color="#123456", duration=42, diodes=[1]))
    assert replies == [bytes.fromhex(
        "01410100" + ROUTE_UUID.replace("-", "") + USER_UUID.replace("-", "")
        + "002a123456")]
    assert encode_broadcast(protocol._decode(  # parser-independent shape check
        quantum_encode(Command.REQUEST_USER_ROUTE_LIST),
        Command.REQUEST_USER_ROUTE_LIST, WireVersion.EWALLS_2_0_14)) == b"\x01\x47\x00\x00"


def test_route_list_snapshot_survives_reconnect_and_is_session_local() -> None:
    first, _state, _events = make_protocol(
        response_policy=ResponsePolicy.SYNTHETIC_STATE)
    second, _other_state, _other_events = make_protocol(
        response_policy=ResponsePolicy.SYNTHETIC_STATE)
    first.feed(quantum_encode(
        Command.ACTIVATE_WALL, route_id=ROUTE_UUID, user_id=USER_UUID,
        color="#010203", duration=30, diodes=[1]))
    first.reset_transport()
    snapshot = first.feed(quantum_encode(Command.REQUEST_USER_ROUTE_LIST))[0]
    empty = second.feed(quantum_encode(Command.REQUEST_USER_ROUTE_LIST))[0]
    assert snapshot[1:4] == b"\x47\x01\x00"
    assert len(snapshot) == 41
    assert empty == b"\x01\x47\x00\x00"


def test_observed_xl_accepts_commands_without_inventing_state_replies() -> None:
    protocol, state, events = make_protocol()
    frames = [
        quantum_encode(Command.TURN_OFF_BY_USER, user_id=USER_UUID),
        quantum_encode(
            Command.ACTIVATE_WALL, route_id=ROUTE_UUID, user_id=USER_UUID,
            color="#00aaff", duration=EWALLS_ROUTE_DURATION_SECONDS,
            diodes=[1, 2]),
        quantum_encode(Command.REQUEST_USER_ROUTE_LIST),
    ]

    assert [protocol.feed(frame) for frame in frames] == [[], [], []]
    assert sorted(state.get_holds()) == [1, 2]
    assert [event.command for event in events] == [
        Command.TURN_OFF_BY_USER,
        Command.ACTIVATE_WALL,
        Command.REQUEST_USER_ROUTE_LIST,
    ]


def test_observed_xl_reconnect_preserves_leds_but_not_a_synthetic_roster() -> None:
    protocol, state, _events = make_protocol()
    protocol.feed(quantum_encode(
        Command.ACTIVATE_WALL, route_id=ROUTE_UUID, user_id=USER_UUID,
        duration=EWALLS_ROUTE_DURATION_SECONDS, diodes=[1]))

    protocol.reset_transport()

    assert list(state.get_holds()) == [1]
    assert protocol._players == {}
    assert protocol.feed(
        quantum_encode(Command.REQUEST_USER_ROUTE_LIST)) == []


def test_observed_xl_fault_injection_still_reports_modbus_exception() -> None:
    protocol, state, events = make_protocol(reject={Command.ACTIVATE_WALL})
    replies = protocol.feed(quantum_encode(
        Command.ACTIVATE_WALL, route_id=ROUTE_UUID, user_id=USER_UUID,
        duration=EWALLS_ROUTE_DURATION_SECONDS, diodes=[1]))

    assert replies == [encode_exception(Command.ACTIVATE_WALL, 4)]
    assert state.get_holds() == {}
    assert events[-1].code == "SLAVE_DEVICE_FAILURE"


@pytest.mark.parametrize("code", sorted({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 254}))
def test_every_fault_code_is_state_safe_and_parser_shaped(code: int) -> None:
    state = QuantumBoardState(tuple(range(10)))
    events: list[ProtocolEvent] = []
    protocol = QuantumProtocol(
        state.apply, events.append, {Command.ACTIVATE_WALL},
        reject_code=code)

    reply = protocol.feed(quantum_encode(
        Command.ACTIVATE_WALL, route_id=ROUTE_UUID, user_id=USER_UUID,
        duration=EWALLS_ROUTE_DURATION_SECONDS, diodes=[1]))

    assert reply == [bytes((1, 0xC1, code))]
    assert state.get_holds() == {}
    assert not events[-1].ok


def test_ewalls_route_play_duration_is_unlimited_u16() -> None:
    frame = quantum_encode(
        Command.ACTIVATE_WALL, route_id=ROUTE_UUID, user_id=USER_UUID,
        duration=EWALLS_ROUTE_DURATION_SECONDS, diodes=[1])
    assert frame[37:39] == b"\xff\xff"


def test_removed_2014_encoder_commands_require_explicit_legacy_mode() -> None:
    for command in (Command.CHANGE_ROUTE_PARAMS, Command.ACTIVATE_WALL_LED_ID,
                    Command.SET_START_HOLDS, Command.SET_STEP_HOLDS,
                    Command.SET_FINISH_HOLDS):
        with pytest.raises(ValueError, match="not emitted"):
            quantum_encode(command)
