"""Shiny Wall decoder: '-' and '<command><payload>.' messages.

Every stream case is replayed at several write sizes (1, 7, 20 and 512
bytes): the decoder is a byte-level state machine, so the decoded states
and the event kinds must not depend on how an app splits a message.
"""

import pytest

from protocols.shinywall import (CODE_FOOT, CODE_HAND, CODE_START, CODE_TOP,
                                 CODES_ACCEPT, LENGTH_STRICT, ShinyWallOptions,
                                 ShinyWallProtocol, chunks, encode_vector,
                                 route_writes, state_vector, summarize)

CHUNK_SIZES = (1, 7, 20, 512)
HOLDS = 311


def replay(stream: bytes, size: int, hold_count: int = HOLDS, **kwargs):
    """Feed ``stream`` in ``size``-byte writes; return (states, kinds, events)."""
    states: list[dict[int, int]] = []
    decoder = ShinyWallProtocol(hold_count, states.append, **kwargs)
    events = []
    for i in range(0, len(stream), size):
        events.extend(decoder.feed(stream[i:i + size]))
    events.extend(decoder.flush())
    return states, [e.kind for e in events], events


def route(holds: dict[int, int], hold_count: int = HOLDS) -> bytes:
    return encode_vector(state_vector(holds, hold_count))


ROUTE = {0: CODE_START, 1: CODE_START, 40: CODE_HAND, 77: CODE_HAND,
         150: CODE_FOOT, 300: CODE_TOP}


@pytest.mark.parametrize("size", CHUNK_SIZES)
class TestStreams:
    def test_clear_then_route(self, size) -> None:
        states, kinds, _ = replay(b"-" + route(ROUTE), size)
        assert states == [{}, ROUTE]
        assert kinds == ["clear", "state"]

    def test_vector_replaces_the_whole_wall(self, size) -> None:
        stream = route({5: CODE_HAND}) + route({6: CODE_TOP})
        states, _, _ = replay(stream, size)
        assert states == [{5: CODE_HAND}, {6: CODE_TOP}]

    def test_garbage_before_between_and_after_messages(self, size) -> None:
        stream = b"\r\n " + route(ROUTE) + b"\x00\xff" + b"-" + b" \t"
        states, kinds, events = replay(stream, size)
        assert states == [ROUTE, {}]
        assert kinds == ["garbage", "state", "garbage", "clear", "garbage"]
        assert "3 B" in events[0].detail and "0d0a20" in events[0].detail

    def test_dash_inside_a_vector_aborts_then_clears(self, size) -> None:
        full = route(ROUTE)
        stream = full[:100] + b"-" + route({3: CODE_FOOT})
        states, kinds, events = replay(stream, size)
        assert states == [{}, {3: CODE_FOOT}]
        assert kinds == ["abort", "clear", "state"]
        assert "inside a state vector" in events[0].detail

    def test_short_vector_lenient_applies_and_warns(self, size) -> None:
        states, kinds, events = replay(b"+204.", size)
        assert states == [{0: CODE_START, 2: CODE_HAND}]
        assert kinds == ["length", "state"]
        assert "3 codes" in events[0].detail and "311 holds" in events[0].detail

    def test_long_vector_lenient_reports_lit_tail(self, size) -> None:
        codes = state_vector(ROUTE, HOLDS) + [0, CODE_TOP, 0]
        states, kinds, events = replay(encode_vector(codes), size)
        assert states == [ROUTE]
        assert kinds == ["length", "state"]
        assert events[0].indices == (312,)

    def test_wrong_length_strict_rejects_and_keeps_state(self, size) -> None:
        stream = route(ROUTE) + encode_vector([CODE_TOP] * 475)
        states, kinds, _ = replay(stream, size, length_mode=LENGTH_STRICT)
        assert states == [ROUTE]
        assert kinds == ["state", "length", "reject"]

    def test_exact_length_strict_applies(self, size) -> None:
        states, kinds, _ = replay(route(ROUTE), size, length_mode=LENGTH_STRICT)
        assert states == [ROUTE] and kinds == ["state"]

    def test_non_digits_are_invalid_codes(self, size) -> None:
        payload = bytearray(route(ROUTE))
        payload[1 + 40] = ord("x")   # index 40 (a hand) becomes invalid
        payload[1 + 41] = ord(" ")
        payload[1 + 42] = ord("+")
        states, kinds, events = replay(bytes(payload), size)
        expected = dict(ROUTE)
        del expected[40]
        assert states == [expected]
        assert kinds == ["invalid", "state"]
        assert events[0].indices == (40, 41, 42)

    def test_unknown_digits_reject_or_accept(self, size) -> None:
        codes = state_vector(ROUTE, HOLDS)
        codes[10] = 5
        codes[11] = 9
        stream = encode_vector(codes)
        states, kinds, events = replay(stream, size)
        assert states == [ROUTE]
        assert kinds == ["unknown-code", "state"]
        assert events[0].indices == (10, 11)
        accepted, _, _ = replay(stream, size, codes_mode=CODES_ACCEPT)
        assert accepted == [{**ROUTE, 10: 5, 11: 9}]

    def test_foot_disco_and_game_commands_are_logged_only(self, size) -> None:
        stream = b"f3." + b"*12/4." + b"*@." + route(ROUTE)
        states, kinds, events = replay(stream, size)
        assert states == [ROUTE]
        assert kinds == ["foot", "game", "disco", "state"]
        assert "f3." in events[0].detail and "*12/4." in events[1].detail

    def test_single_hold_edits_change_the_current_wall(self, size) -> None:
        stream = route(ROUTE) + b"*1123." + b"*0040." + b"*4005." + b"*2001."
        states, kinds, events = replay(stream, size)
        assert kinds == ["state", "hold", "hold", "hold", "hold"]
        after = dict(ROUTE)
        after[123] = CODE_START
        assert states[1] == after
        del after[40]
        assert states[2] == after
        after[5] = CODE_TOP
        assert states[3] == after
        after[1] = CODE_HAND             # a start becomes a hand
        assert states[4] == after
        assert events[1].indices == (123,) and events[2].indices == ()

    def test_single_hold_without_route_starts_from_dark(self, size) -> None:
        states, kinds, _ = replay(b"-*3007.", size)
        assert states == [{}, {7: CODE_FOOT}]
        assert kinds == ["clear", "hold"]

    def test_bad_single_hold_commands_change_nothing(self, size) -> None:
        stream = b"*1." + b"*9 1." + b"*51." + b"*1311." + b"*x."
        states, kinds, events = replay(stream, size)
        assert states == []
        assert kinds == ["single", "single", "unknown-code", "invalid", "single"]
        assert events[3].indices == (311,)

    def test_unknown_command_is_reported(self, size) -> None:
        states, kinds, events = replay(b"Z99." + route(ROUTE), size)
        assert states == [ROUTE]
        assert kinds == ["command", "state"]
        assert "unknown command 'Z'" in events[0].detail

    def test_old_dotted_format_is_reported(self, size) -> None:
        old = b".2+0+4+" + b"0+" * 307 + b"8."
        states, kinds, events = replay(b"-" + old, size)
        assert states == [{}]
        assert kinds == ["clear", "garbage", "legacy"]
        assert "old dotted format" in events[2].detail

    def test_overflow_discards_to_the_end_of_the_message(self, size) -> None:
        stream = b"+" + b"4" * 5000 + b"." + route(ROUTE)
        states, kinds, _ = replay(stream, size)
        assert states == [ROUTE]
        assert kinds == ["overflow", "state"]

    def test_runaway_command_overflows_then_dash_clears(self, size) -> None:
        stream = b"*" + b"9" * 5000 + b"-"
        states, kinds, _ = replay(stream, size)
        assert states == [{}]
        assert kinds == ["overflow", "clear"]

    def test_empty_vector_is_a_length_mismatch(self, size) -> None:
        states, kinds, _ = replay(b"+.", size)
        assert states == [{}]
        assert kinds == ["length", "state"]


class TestTransport:
    def test_reset_discards_partial_vector_and_reports(self) -> None:
        states: list = []
        decoder = ShinyWallProtocol(HOLDS, states.append)
        message = route(ROUTE)
        assert decoder.feed(message[:50]) == []
        assert decoder.in_message
        events = decoder.reset()
        assert [e.kind for e in events] == ["abort"]
        assert "link ended inside a state vector" in events[0].detail
        assert not decoder.in_message
        # The rest of the old vector reads as a new (unknown) message now.
        kinds = [e.kind for e in decoder.feed(message[50:])]
        assert kinds == ["command"]
        decoder.feed(b"-")
        assert states == [{}]

    def test_reset_without_partial_message_is_quiet(self) -> None:
        decoder = ShinyWallProtocol(HOLDS, lambda _s: None)
        decoder.feed(b"-")
        assert decoder.reset() == []

    def test_garbage_reported_once_per_run(self) -> None:
        decoder = ShinyWallProtocol(HOLDS, lambda _s: None)
        assert decoder.feed(b"\x01\x02") == []
        assert decoder.feed(b"\x03 . ") == []
        events = decoder.feed(b"-")
        assert [e.kind for e in events] == ["garbage", "clear"]
        assert "6 B" in events[0].detail

    def test_current_wall_comes_from_the_provider(self) -> None:
        states: list = []
        shared = {9: CODE_TOP}
        decoder = ShinyWallProtocol(HOLDS, states.append, current=lambda: shared)
        decoder.feed(b"*15.")
        assert states == [{9: CODE_TOP, 5: CODE_START}]

    def test_messages_counter(self) -> None:
        decoder = ShinyWallProtocol(HOLDS, lambda _s: None)
        decoder.feed(b"-" + route(ROUTE) + b"*1.")
        assert decoder.messages == 3


class TestEncoder:
    def test_hold_format(self) -> None:
        from protocols.shinywall import encode_hold
        assert encode_hold(123, CODE_START) == b"*1123."
        assert encode_hold(7, 0) == b"*07."

    def test_vector_format(self) -> None:
        assert encode_vector([0, 2, 4, 6, 8]) == b"+02468."
        with pytest.raises(ValueError):
            encode_vector([10])

    def test_st1_route_is_313_bytes(self) -> None:
        assert len(route(ROUTE)) == 313

    def test_chunks_follow_the_official_app_rule(self) -> None:
        message = route(ROUTE)
        assert [len(c) for c in chunks(message, 23)][:2] == [18, 18]
        assert chunks(message, 512) == [message]   # 507-byte chunks
        assert [len(c) for c in chunks(message, 185)] == [180, 133]
        assert b"".join(chunks(message, 23)) == message

    def test_route_writes_start_with_clear(self) -> None:
        writes = route_writes(ROUTE, HOLDS, mtu=23)
        assert writes[0] == b"-"
        assert b"".join(writes[1:]) == route(ROUTE)

    def test_summary_lists_roles_and_unknown(self) -> None:
        text = summarize({**ROUTE, 9: 5})
        assert "start 2 [0, 1]" in text and "top 1 [300]" in text
        assert "unknown 1 [9=5]" in text


class TestOptions:
    def test_defaults_validate(self) -> None:
        options = ShinyWallOptions().validated()
        assert options.props == ("read", "write", "write-without-response",
                                 "notify")
        assert options.service_uuid == "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
        assert not options.ack and options.notify_uuid is None

    @pytest.mark.parametrize("kwargs, message", [
        (dict(name="ShinyWall_STP_" + "x" * 16), "bytes"),
        (dict(hold_count=0), "--shiny-holds"),
        (dict(length_mode="exact"), "--shiny-length"),
        (dict(codes_mode="maybe"), "--shiny-codes"),
        (dict(mtu=10), "--shiny-mtu"),
        (dict(delay_ms=-1), "--shiny-delay"),
        (dict(on_disconnect="blink"), "--shiny-on-disconnect"),
        (dict(props=("read", "notify")), "write"),
        (dict(props=("write", "broadcast")), "unknown --shiny-props"),
        (dict(service_uuid="not-a-uuid"), "not a UUID"),
        (dict(notify_uuid="beb5483e-36e1-4688-b7f5-ea07361b26a8"), "differ"),
    ])
    def test_invalid_values_raise(self, kwargs, message) -> None:
        with pytest.raises(ValueError, match=message):
            ShinyWallOptions(**kwargs).validated()

    def test_props_are_normalised(self) -> None:
        options = ShinyWallOptions(props=(" Write ", "notify", "write")).validated()
        assert options.props == ("write", "notify")

    def test_ack_adds_notify_unless_a_separate_characteristic(self) -> None:
        assert ShinyWallOptions(props=("write",), ack=True).validated().props == (
            "write", "notify")
        separate = ShinyWallOptions(props=("write",), ack=True,
                                    notify_uuid="12345678-0000-1000-8000-00805F9B34FB")
        assert separate.validated().props == ("write",)
        assert separate.validated().notify_uuid == "12345678-0000-1000-8000-00805f9b34fb"

    def test_service_uuid_is_normalised(self) -> None:
        options = ShinyWallOptions(
            service_uuid="4FAFC201-1FB5-459E-8FCC-C5C9C331914B").validated()
        assert options.service_uuid == "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
