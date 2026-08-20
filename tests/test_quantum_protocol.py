"""Quantum clean-room protocol, state and reconnect contracts."""

from __future__ import annotations

import json

import pytest

from board_state import QuantumBoardState
from protocols.quantum import (CHUNK_LIMITS, Command, ProtocolEvent,
                               QuantumProtocol, crc16_modbus, encode,
                               encode_chunks)


GOLDEN = {
    "off_all": "0145000100009dc5",
    "on_all": "0164112233010285c1",
    "activate": (
        "0141726f7574652d31323334353637383930757365722d3132333435363738393031"
        "102030012c0104000101025773"
    ),
    "editor": "016500ff0000010403e9181a9992",
}


def make_protocol(addresses=tuple(range(1000)), reject=None):
    state = QuantumBoardState(addresses)
    events: list[ProtocolEvent] = []
    protocol = QuantumProtocol(state.apply, events.append, reject or set())
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
    assert events[-1].code == "REJECTED"


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
