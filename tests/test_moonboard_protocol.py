"""Tests for the MoonBoard ASCII climb-frame protocol decoder.

Covers:
  - the serpentine serial<->grid arithmetic (against BoardSesh's C++)
  - token->role + token->color mapping
  - decoding ``l#S0,P1,E197#`` into (role, column, row) holds
  - BLE writes split across multiple ≤20-byte chunks
  - the ``~``-config-prefixed variants (~D...#, ~l#...#)
  - round-trips against frames CruxCoach's MoonBoardFrameEncoder emits
"""

import pytest

from protocols.moonboard import (
    COLOR_BLUE,
    COLOR_CYAN,
    COLOR_GREEN,
    COLOR_PINK,
    COLOR_RED,
    COLOR_VIOLET,
    COLOR_YELLOW,
    HOLD_COUNT,
    NUM_COLUMNS,
    NUM_ROWS,
    ROLE_FINISH,
    ROLE_FOOT,
    ROLE_HAND,
    ROLE_LEFT,
    ROLE_MATCH,
    ROLE_START,
    MoonBoardProtocol,
    decode_problem,
    hold_id_to_serial_position,
    serial_position_to_grid,
    serial_position_to_hold_id,
    token_to_color,
    token_to_role_code,
)


# ---------------------------------------------------------------------------
# serial <-> grid arithmetic
# ---------------------------------------------------------------------------

class TestSerialToHoldId:
    """serialPositionToHoldId — ported from BoardSesh's C++ decoder."""

    @pytest.mark.parametrize(
        "serial,expected_hold_id",
        [
            (0, 1),      # column 0, bottom
            (17, 188),   # column 0, top (even column runs bottom->top)
            (18, 189),   # column 1, top (odd column runs top->bottom)
            (35, 2),     # column 1, bottom
            (36, 3),     # column 2, bottom
            (197, 198),  # column 10, top — last hold
        ],
    )
    def test_known_positions(self, serial: int, expected_hold_id: int) -> None:
        assert serial_position_to_hold_id(serial) == expected_hold_id

    def test_out_of_range_returns_negative(self) -> None:
        assert serial_position_to_hold_id(-1) == -1
        assert serial_position_to_hold_id(HOLD_COUNT) == -1
        assert serial_position_to_hold_id(198) == -1
        assert serial_position_to_hold_id(199) == -1

    def test_full_range_maps_to_unique_hold_ids(self) -> None:
        """Every strip position 0..197 maps to a unique hold id 1..198."""
        hold_ids = {serial_position_to_hold_id(p) for p in range(HOLD_COUNT)}
        assert hold_ids == set(range(1, HOLD_COUNT + 1))


class TestHoldIdToSerial:
    """holdIdToSerialPosition is the exact inverse of serialPositionToHoldId."""

    def test_round_trip_all_positions(self) -> None:
        for serial in range(HOLD_COUNT):
            hold_id = serial_position_to_hold_id(serial)
            assert hold_id_to_serial_position(hold_id) == serial

    def test_out_of_range_returns_negative(self) -> None:
        assert hold_id_to_serial_position(0) == -1
        assert hold_id_to_serial_position(HOLD_COUNT + 1) == -1


class TestSerialToGrid:
    """serial_position_to_grid: even columns bottom->top, odd top->bottom."""

    @pytest.mark.parametrize(
        "serial,column,row",
        [
            (0, 0, 0),     # col 0 even -> in-column 0 maps to row 0
            (17, 0, 17),   # col 0 even -> in-column 17 maps to row 17
            (18, 1, 17),   # col 1 odd  -> in-column 0 maps to row 17
            (35, 1, 0),    # col 1 odd  -> in-column 17 maps to row 0
            (197, 10, 17), # col 10 even
        ],
    )
    def test_known_cells(self, serial: int, column: int, row: int) -> None:
        assert serial_position_to_grid(serial) == (column, row)

    def test_off_grid_raises(self) -> None:
        with pytest.raises(ValueError):
            serial_position_to_grid(198)
        with pytest.raises(ValueError):
            serial_position_to_grid(-1)

    def test_grid_matches_hold_id_formula(self) -> None:
        """(column, row) is consistent with hold_id = row*11 + col + 1."""
        for serial in range(HOLD_COUNT):
            column, row = serial_position_to_grid(serial)
            assert 0 <= column < NUM_COLUMNS
            assert 0 <= row < NUM_ROWS
            assert serial_position_to_hold_id(serial) == row * NUM_COLUMNS + column + 1


# ---------------------------------------------------------------------------
# token mapping
# ---------------------------------------------------------------------------

class TestTokenToRole:
    """Tokens are case-insensitive; R and P both mean a hand hold."""

    @pytest.mark.parametrize(
        "token,role",
        [
            ("S", ROLE_START), ("s", ROLE_START),
            ("R", ROLE_HAND), ("r", ROLE_HAND),
            ("P", ROLE_HAND), ("p", ROLE_HAND),
            ("L", ROLE_LEFT), ("l", ROLE_LEFT),
            ("M", ROLE_MATCH), ("m", ROLE_MATCH),
            ("F", ROLE_FOOT), ("f", ROLE_FOOT),
            ("E", ROLE_FINISH), ("e", ROLE_FINISH),
        ],
    )
    def test_known_tokens(self, token: str, role: int) -> None:
        assert token_to_role_code(token) == role

    def test_unknown_token_is_zero(self) -> None:
        assert token_to_role_code("X") == 0
        assert token_to_role_code("9") == 0


class TestTokenToColor:
    """Role colors, verbatim from moonboard_protocol.cpp."""

    @pytest.mark.parametrize(
        "token,color",
        [
            ("S", COLOR_GREEN),   # start  = green
            ("R", COLOR_BLUE),    # hand   = blue
            ("P", COLOR_BLUE),    # hand   = blue
            ("E", COLOR_RED),     # finish = red
            ("F", COLOR_CYAN),    # foot   = cyan
            ("L", COLOR_VIOLET),  # left   = violet
            ("M", COLOR_PINK),    # match  = pink
        ],
    )
    def test_role_colors(self, token: str, color: tuple[int, int, int]) -> None:
        assert token_to_color(token) == color

    def test_unknown_token_falls_back_to_blue(self) -> None:
        assert token_to_color("X") == COLOR_BLUE


# ---------------------------------------------------------------------------
# decode_problem — the pure parsing step
# ---------------------------------------------------------------------------

class TestDecodeProblem:
    """Decoding the inner '<token><pos>,...' body of a frame."""

    def test_decodes_canonical_frame(self) -> None:
        """l#S0,P1,E197# -> the (role, column, row) holds named in the spec."""
        problem = decode_problem("S0,P1,E197")
        assert len(problem.holds) == 3

        start, hand, finish = problem.holds

        # S0  — start, serpentine pos 0 -> column 0, row 0.
        assert start.token == "S"
        assert start.role_code == ROLE_START
        assert (start.column, start.row) == (0, 0)
        assert start.hold_id == 1
        assert start.color == COLOR_GREEN

        # P1  — hand, pos 1 -> column 0, row 1.
        assert hand.token == "P"
        assert hand.role_code == ROLE_HAND
        assert (hand.column, hand.row) == (0, 1)
        assert hand.hold_id == 12
        assert hand.color == COLOR_BLUE

        # E197 — finish, pos 197 -> column 10, row 17.
        assert finish.token == "E"
        assert finish.role_code == ROLE_FINISH
        assert (finish.column, finish.row) == (10, 17)
        assert finish.hold_id == 198
        assert finish.color == COLOR_RED

    def test_led_commands_match_holds_without_aux(self) -> None:
        """Without lights-above-holds there is exactly one LED per hold."""
        problem = decode_problem("S0,P1,E197")
        assert len(problem.led_commands) == 3
        assert [c.led_index for c in problem.led_commands] == [0, 1, 197]

    def test_empty_payload(self) -> None:
        problem = decode_problem("")
        assert problem.holds == []
        assert problem.led_commands == []

    def test_lowercase_tokens(self) -> None:
        problem = decode_problem("s0,p1,e197")
        assert [h.role_code for h in problem.holds] == [
            ROLE_START, ROLE_HAND, ROLE_FINISH,
        ]

    def test_all_roles(self) -> None:
        """A frame exercising every supported token."""
        problem = decode_problem("S0,R1,P2,L3,M4,F5,E6")
        assert [h.role_code for h in problem.holds] == [
            ROLE_START, ROLE_HAND, ROLE_HAND, ROLE_LEFT,
            ROLE_MATCH, ROLE_FOOT, ROLE_FINISH,
        ]

    def test_unknown_token_skipped(self) -> None:
        """An unrecognised token is dropped, the rest still decode."""
        problem = decode_problem("S0,X9,P1")
        assert [h.token for h in problem.holds] == ["S", "P"]

    def test_off_grid_position_skipped(self) -> None:
        """A position past the 198-hold grid is dropped, not fatal."""
        problem = decode_problem("S0,P198,P250,E197")
        assert [h.serial_position for h in problem.holds] == [0, 197]

    def test_token_without_position_skipped(self) -> None:
        problem = decode_problem("S0,P,E197")
        assert [h.serial_position for h in problem.holds] == [0, 197]


# ---------------------------------------------------------------------------
# MoonBoardProtocol — BLE buffering state machine
# ---------------------------------------------------------------------------

def _decode_one(data) -> list:
    """Feed `data` to a fresh decoder, return the holds from on_message."""
    received: list = []
    decoder = MoonBoardProtocol(on_message=lambda holds: received.append(holds))
    decoder.feed(data)
    return received


class TestProtocolFeed:
    """Feeding complete and partial frames into the buffering decoder."""

    def test_single_complete_frame(self) -> None:
        received = _decode_one(b"l#S0,P1,E197#")
        assert len(received) == 1
        # on_message yields (role_code, column, row, r, g, b) tuples.
        holds = received[0]
        assert len(holds) == 3
        assert holds[0] == (ROLE_START, 0, 0, *COLOR_GREEN)
        assert holds[1] == (ROLE_HAND, 0, 1, *COLOR_BLUE)
        assert holds[2] == (ROLE_FINISH, 10, 17, *COLOR_RED)

    def test_feed_returns_true_on_complete_frame(self) -> None:
        decoder = MoonBoardProtocol()
        assert decoder.feed(b"l#S0#") is True

    def test_feed_returns_false_on_partial_frame(self) -> None:
        decoder = MoonBoardProtocol()
        assert decoder.feed(b"l#S0,P1") is False

    def test_string_input_accepted(self) -> None:
        received = _decode_one("l#S0,P1,E197#")
        assert len(received) == 1
        assert len(received[0]) == 3

    def test_decoded_problem_property(self) -> None:
        decoder = MoonBoardProtocol()
        decoder.feed(b"l#S0,P1,E197#")
        problem = decoder.decoded_problem
        assert problem is not None
        assert problem.problem_payload == "S0,P1,E197"
        assert problem.raw_payload == "l#S0,P1,E197#"
        assert len(problem.holds) == 3


class TestSplitAcrossWrites:
    """A frame split across multiple ≤20-byte BLE writes must reassemble."""

    def test_byte_at_a_time(self) -> None:
        frame = b"l#S0,P1,E197#"
        received: list = []
        decoder = MoonBoardProtocol(on_message=lambda h: received.append(h))
        for i, byte in enumerate(frame):
            done = decoder.feed(bytes([byte]))
            # The callback must only fire once, on the final '#'.
            if i < len(frame) - 1:
                assert done is False
                assert received == []
        assert len(received) == 1
        assert len(received[0]) == 3

    def test_two_chunks(self) -> None:
        # Split mid-payload, as a 20-byte BLE MTU would.
        received = _decode_one_chunks([b"l#S0,P", b"1,E197#"])
        assert len(received) == 1
        assert len(received[0]) == 3

    def test_twenty_byte_chunks(self) -> None:
        """A long frame split into real 20-byte BLE chunks."""
        # A frame with many holds, well over 20 bytes.
        holds = ",".join(f"P{p}" for p in range(0, 40, 2))
        frame = f"l#{holds}#".encode("ascii")
        assert len(frame) > 40  # genuinely multi-chunk
        chunks = [frame[i:i + 20] for i in range(0, len(frame), 20)]
        assert len(chunks) >= 3
        received = _decode_one_chunks(chunks)
        assert len(received) == 1
        assert len(received[0]) == 20

    def test_split_exactly_on_delimiters(self) -> None:
        """Chunk boundaries landing right on '#' must still work."""
        received = _decode_one_chunks([b"l#", b"S0,P1,E197", b"#"])
        assert len(received) == 1
        assert len(received[0]) == 3

    def test_two_frames_in_one_write(self) -> None:
        """Back-to-back frames in a single write both decode."""
        received = _decode_one(b"l#S0#l#P1#")
        assert len(received) == 2
        assert received[0][0] == (ROLE_START, 0, 0, *COLOR_GREEN)
        assert received[1][0] == (ROLE_HAND, 0, 1, *COLOR_BLUE)

    def test_leading_garbage_ignored(self) -> None:
        """Stray bytes before the 'l' are skipped (parser stays idle)."""
        received = _decode_one(b"\x00\xffxyzl#S0,P1#")
        assert len(received) == 1
        assert len(received[0]) == 2


def _decode_one_chunks(chunks) -> list:
    """Feed a sequence of chunks to one decoder, return on_message holds."""
    received: list = []
    decoder = MoonBoardProtocol(on_message=lambda h: received.append(h))
    for chunk in chunks:
        decoder.feed(chunk)
    return received


class TestConfigPrefixedVariants:
    """The '~'-config-prefixed forms a real board's decoder accepts."""

    def test_tilde_l_prefix(self) -> None:
        """~l#...# — a '~' config block then the lights payload."""
        received = _decode_one(b"~l#S0,P1,E197#")
        assert len(received) == 1
        assert len(received[0]) == 3

    def test_tilde_d_lights_above_holds(self) -> None:
        """~D#l#...# sets 'lights above holds' — adds yellow aux LEDs."""
        decoder = MoonBoardProtocol()
        decoder.feed(b"~D#l#S0,P1,E197#")
        problem = decoder.decoded_problem
        assert problem is not None
        assert problem.lights_above_holds is True
        # Holds themselves are unchanged...
        assert len(problem.holds) == 3
        # ...but aux yellow LEDs are emitted for non-finish holds.
        aux = [c for c in problem.led_commands
               if (c.r, c.g, c.b) == COLOR_YELLOW]
        assert len(aux) >= 1

    def test_tilde_d_skips_aux_for_finish(self) -> None:
        """The finish hold ('E') never gets an aux marker."""
        decoder = MoonBoardProtocol()
        # E197 sits at offset table index 197 (value 0) — but even where
        # the offset is non-zero, finish holds are skipped by design.
        decoder.feed(b"~D#l#E1#")  # serial pos 1 has offset +1
        problem = decoder.decoded_problem
        assert problem is not None
        aux = [c for c in problem.led_commands
               if (c.r, c.g, c.b) == COLOR_YELLOW]
        assert aux == []

    def test_plain_frame_has_no_lights_above_holds(self) -> None:
        """A plain l#...# frame leaves lights_above_holds False."""
        decoder = MoonBoardProtocol()
        decoder.feed(b"l#S0#")
        problem = decoder.decoded_problem
        assert problem is not None
        assert problem.lights_above_holds is False

    def test_config_block_then_payload_split(self) -> None:
        """A ~D config + payload split across writes still decodes."""
        received = _decode_one_chunks([b"~D", b"#l#S0,", b"P1#"])
        assert len(received) == 1
        assert len(received[0]) == 2


# ---------------------------------------------------------------------------
# Round-trip against CruxCoach's MoonBoardFrameEncoder
# ---------------------------------------------------------------------------

def _encoder_serial_position(hold_id: int) -> int:
    """Reimplements MoonBoardFrameEncoder.serialPosition (Kotlin).

    Kept here so the test is self-contained — it asserts our decoder's
    arithmetic is the exact inverse of CruxCoach's encoder.
    """
    assert 1 <= hold_id <= HOLD_COUNT
    zero_based = hold_id - 1
    column = zero_based % NUM_COLUMNS
    row = zero_based // NUM_COLUMNS
    if column % 2 == 0:
        return column * NUM_ROWS + row
    return column * NUM_ROWS + (NUM_ROWS - 1 - row)


def _encode_frame(frames: str) -> str:
    """Reimplements MoonBoardFrameEncoder.encodeToString (Kotlin).

    frames is 'p{holdId}r{roleCode}' pairs; the encoder emits
    'l#<token><serialPos>,...#'. Roles: 42->S, 43->P, 44->E.
    """
    role_token = {42: "S", 43: "P", 44: "E"}
    tokens: list[str] = []
    for segment in frames.split("p"):
        segment = segment.strip()
        if not segment:
            continue
        r_index = segment.find("r")
        if r_index <= 0:
            continue
        # hold id = leading digit run before 'r'.
        hold_run = ""
        for c in segment[:r_index]:
            if c.isdigit():
                hold_run += c
            else:
                break
        if not hold_run:
            continue
        hold_id = int(hold_run)
        # role code = leading digit run after 'r'.
        role_run = ""
        for c in segment[r_index + 1:]:
            if c.isdigit():
                role_run += c
            else:
                break
        if not role_run:
            continue
        role_code = int(role_run)
        token = role_token.get(role_code)
        if token is None:
            continue
        if not 1 <= hold_id <= HOLD_COUNT:
            continue
        tokens.append(f"{token}{_encoder_serial_position(hold_id)}")
    return "l#" + ",".join(tokens) + "#"


class TestRoundTripWithEncoder:
    """Decoder o Encoder = identity for frames CruxCoach emits."""

    def test_serial_arithmetic_matches_encoder(self) -> None:
        """Our decoder's hold_id<->serial inverts the encoder's serialPosition."""
        for hold_id in range(1, HOLD_COUNT + 1):
            serial = _encoder_serial_position(hold_id)
            # encoder hold_id -> serial; decoder serial -> hold_id.
            assert serial_position_to_hold_id(serial) == hold_id
            # and our own inverse agrees with the encoder.
            assert hold_id_to_serial_position(hold_id) == serial

    def test_round_trip_known_climb(self) -> None:
        """Encode a 'frames' string, decode the wire frame, recover holds."""
        # frames: hold 1 (start), hold 50 (hand), hold 198 (finish).
        frames = "p1r42p50r43p198r44"
        wire = _encode_frame(frames)
        problem = decode_problem(wire[2:-1])  # strip 'l#' and '#'

        decoded = [(h.hold_id, h.role_code) for h in problem.holds]
        assert decoded == [(1, 42), (50, 43), (198, 44)]

    def test_round_trip_canonical_frame(self) -> None:
        """The spec example l#S0,P1,E197# matches an encoded climb.

        S0=hold 1, P1=hold 12, E197=hold 198.
        """
        frames = "p1r42p12r43p198r44"
        assert _encode_frame(frames) == "l#S0,P1,E197#"

        problem = decode_problem("S0,P1,E197")
        assert [h.hold_id for h in problem.holds] == [1, 12, 198]

    def test_round_trip_every_hold(self) -> None:
        """Encode each hold individually, decode it, recover the hold id."""
        for hold_id in range(1, HOLD_COUNT + 1):
            wire = _encode_frame(f"p{hold_id}r43")
            problem = decode_problem(wire[2:-1])
            assert len(problem.holds) == 1
            assert problem.holds[0].hold_id == hold_id
            assert problem.holds[0].role_code == ROLE_HAND

    def test_encoder_full_frame_decodes(self) -> None:
        """A full encoded frame fed through the buffering decoder."""
        frames = "p1r42p99r43p100r43p198r44"
        wire = _encode_frame(frames).encode("ascii")
        received = _decode_one(wire)
        assert len(received) == 1
        assert len(received[0]) == 4
        # First hold is the start (green), last is the finish (red).
        assert received[0][0][0] == ROLE_START
        assert received[0][-1][0] == ROLE_FINISH
