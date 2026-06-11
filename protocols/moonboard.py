"""MoonBoard ASCII climb-frame protocol decoder.

The MoonBoard's LED controller speaks a plain-ASCII protocol over the
Nordic UART Service. A climb (a "problem") is sent as:

    l#<token><pos>,<token><pos>,...#

e.g. ``l#S0,P1,E197#``. ``l#`` is the lights-only prefix; holds are
comma-separated; the frame is terminated by ``#``.

A real board also accepts an optional ``~`` config block before the
``l#...#`` payload — most importantly ``~D...#``, which means "light
the aux LED above each hold". CruxCoach's own encoder
(``MoonBoardFrameEncoder``) only emits the plain ``l#...#`` shape, but
this decoder mirrors BoardSesh's parser state machine so the simulator
is robust against either form.

Because BLE writes are capped at ~20 bytes, a frame may arrive split
across several writes. ``MoonBoardProtocol.feed`` buffers incoming
bytes and only decodes once a complete ``#...#``-delimited payload has
been seen.

Ported from BoardSesh's ``moonboard-protocol`` C++ library
(``moonboard_protocol.cpp`` / ``.h``) — the definitive source for the
parser, token->role mapping, the serial<->hold arithmetic, the role
colors and the aux-LED offset table.
"""

import logging
from typing import Callable, NamedTuple

logger = logging.getLogger(__name__)

# --- Board geometry ---------------------------------------------------------

NUM_COLUMNS: int = 11
NUM_ROWS: int = 18
HOLD_COUNT: int = NUM_COLUMNS * NUM_ROWS  # 198
STRIP_LED_COUNT: int = 200

# Column letters A..K — the MoonBoard labels its 11 columns this way.
COLUMN_LETTERS: str = "ABCDEFGHIJK"

# --- Role codes -------------------------------------------------------------
#
# Aurora-aligned role codes, matching the `frames` column convention
# (p{holdId}r{roleCode}) and BoardSesh's MoonBoard rendering — see
# moonboard_protocol.h.
ROLE_START: int = 42
ROLE_HAND: int = 43
ROLE_FINISH: int = 44
ROLE_FOOT: int = 45
ROLE_AUX: int = 46
ROLE_LEFT: int = 47
ROLE_MATCH: int = 48

# Human-readable role names (for logging / GUI legends).
ROLE_NAMES: dict[int, str] = {
    ROLE_START: "start",
    ROLE_HAND: "hand",
    ROLE_FINISH: "finish",
    ROLE_FOOT: "foot",
    ROLE_AUX: "aux",
    ROLE_LEFT: "left",
    ROLE_MATCH: "match",
}

# --- Role colors ------------------------------------------------------------
#
# RGB triples, verbatim from moonboard_protocol.cpp:
#   start=green, hand=blue, finish=red, foot=cyan, left=violet,
#   match=pink, aux=yellow.
COLOR_GREEN: tuple[int, int, int] = (0, 255, 0)
COLOR_BLUE: tuple[int, int, int] = (0, 0, 255)
COLOR_RED: tuple[int, int, int] = (255, 0, 0)
COLOR_CYAN: tuple[int, int, int] = (0, 255, 255)
COLOR_YELLOW: tuple[int, int, int] = (255, 255, 0)
COLOR_VIOLET: tuple[int, int, int] = (128, 0, 255)
COLOR_PINK: tuple[int, int, int] = (255, 0, 160)

ROLE_COLORS: dict[int, tuple[int, int, int]] = {
    ROLE_START: COLOR_GREEN,
    ROLE_HAND: COLOR_BLUE,
    ROLE_FINISH: COLOR_RED,
    ROLE_FOOT: COLOR_CYAN,
    ROLE_LEFT: COLOR_VIOLET,
    ROLE_MATCH: COLOR_PINK,
    ROLE_AUX: COLOR_YELLOW,
}

# token (lower-cased) -> role code. 'r' and 'p' both mean a hand hold.
_TOKEN_TO_ROLE: dict[str, int] = {
    "s": ROLE_START,
    "r": ROLE_HAND,
    "p": ROLE_HAND,
    "l": ROLE_LEFT,
    "m": ROLE_MATCH,
    "f": ROLE_FOOT,
    "e": ROLE_FINISH,
}

# Additional ("lights above holds") LED offsets, indexed by serial
# strip position, from the ArduinoMoonBoardLED reference project — copied
# verbatim from moonboard_protocol.cpp (MOONBOARD_ADDITIONAL_LED_OFFSETS).
ADDITIONAL_LED_OFFSETS: tuple[int, ...] = (
    0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
    -1, -1, -1, -1, -1, -1, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, -1, -1, -1,
    -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 0, 0, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, 0, 0, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
    -1, -1, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, -1, -1, -1, -1, -1, -1, -1,
    -1, -1, -1, -1, -1, -1, -1, -1, -1, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0,
)


# --- serial <-> grid arithmetic --------------------------------------------

def serial_position_to_hold_id(
    serial_position: int, grid_rows: int = NUM_ROWS,
) -> int:
    """Convert a 0-indexed serial LED-strip position to a 1-based hold id.

    The strip is serpentine-wired column by column: even columns run
    bottom-to-top, odd columns top-to-bottom. The per-column height
    [grid_rows] is 18 for standard 11×18 boards and 12 for Mini 2020.
    Returns -1 if the position is outside the variant's grid.

    Direct port of ``MoonBoardProtocol::serialPositionToHoldId``,
    generalised over variant grid height.
    """
    hold_count = NUM_COLUMNS * grid_rows
    if serial_position < 0 or serial_position >= hold_count:
        return -1
    column_index = serial_position // grid_rows
    column_offset = serial_position % grid_rows
    if column_index % 2 == 0:
        row_index = column_offset
    else:
        row_index = grid_rows - 1 - column_offset
    return (row_index * NUM_COLUMNS) + column_index + 1


def hold_id_to_serial_position(
    hold_id: int, grid_rows: int = NUM_ROWS,
) -> int:
    """Convert a 1-based hold id to a 0-indexed serial LED-strip position.

    Inverse of :func:`serial_position_to_hold_id`. Returns -1 if the
    hold id is outside the variant's range.
    """
    hold_count = NUM_COLUMNS * grid_rows
    if hold_id < 1 or hold_id > hold_count:
        return -1
    zero_based = hold_id - 1
    column_index = zero_based % NUM_COLUMNS
    row_index = zero_based // NUM_COLUMNS
    if column_index % 2 == 0:
        return (column_index * grid_rows) + row_index
    return (column_index * grid_rows) + (grid_rows - 1 - row_index)


def serial_position_to_grid(
    serial_position: int, grid_rows: int = NUM_ROWS,
) -> tuple[int, int]:
    """Convert a serial strip position to a ``(column, row)`` grid cell.

    ``column`` is 0..10 (A..K), ``row`` is 0..(grid_rows-1) with row 0
    at the bottom. Raises :class:`ValueError` if the position is
    off-grid.
    """
    hold_count = NUM_COLUMNS * grid_rows
    if serial_position < 0 or serial_position >= hold_count:
        raise ValueError(
            f"serial position out of grid range (0..{hold_count - 1}): "
            f"{serial_position}"
        )
    column = serial_position // grid_rows
    in_column = serial_position % grid_rows
    row = in_column if column % 2 == 0 else (grid_rows - 1 - in_column)
    return column, row


def token_to_role_code(token: str) -> int:
    """Map a single protocol token to its role code (0 if unknown).

    Tokens are case-insensitive: ``S``=start, ``R``/``P``=hand,
    ``L``=left-hand, ``M``=match, ``F``=foot, ``E``=end/finish.
    """
    return _TOKEN_TO_ROLE.get(token.lower(), 0)


def token_to_color(token: str) -> tuple[int, int, int]:
    """Map a protocol token to its RGB role color.

    Unknown / hand tokens fall back to blue, matching the C++ decoder.
    """
    code = token_to_role_code(token)
    return ROLE_COLORS.get(code, COLOR_BLUE)


# --- Decoded data types -----------------------------------------------------

class DecodedHold(NamedTuple):
    """A single hold decoded from a climb frame.

    Attributes:
        serial_position: 0-indexed LED-strip position (0..197).
        hold_id: 1-based grid hold id ``(row*11 + col + 1)``.
        column: Grid column 0..10 (A..K).
        row: Grid row 0..17, row 0 at the bottom.
        token: The original protocol token character (e.g. ``'S'``).
        role_code: Aurora-aligned role code (see ``ROLE_*``).
        color: RGB role color for this hold.
    """

    serial_position: int
    hold_id: int
    column: int
    row: int
    token: str
    role_code: int
    color: tuple[int, int, int]


class LedCommand(NamedTuple):
    """A single LED to light: serial strip index + RGB color.

    Aux-LED ("lights above holds") markers also surface as ``LedCommand``s
    even though they sit at a strip index with no hold.
    """

    led_index: int
    r: int
    g: int
    b: int


class DecodedProblem(NamedTuple):
    """The full result of decoding one ``l#...#`` climb frame.

    Attributes:
        holds: Decoded holds, in frame order.
        led_commands: LEDs to light (holds + any aux markers).
        raw_payload: The full raw frame including ``l#``/``~`` wrappers.
        problem_payload: Just the inner ``<token><pos>,...`` body.
        lights_above_holds: True if a ``~D`` aux-LED config preceded it.
    """

    holds: list[DecodedHold]
    led_commands: list[LedCommand]
    raw_payload: str
    problem_payload: str
    lights_above_holds: bool


# --- Parser state machine ---------------------------------------------------

# Parser states — mirror the C++ ``ParserState`` enum.
_PARSER_IDLE = 0
_PARSER_CONFIG = 1
_PARSER_WAIT_FOR_PAYLOAD_START = 2
_PARSER_READING_PAYLOAD = 3


class MoonBoardProtocol:
    """Buffers BLE writes into complete MoonBoard frames and decodes them.

    Feed raw bytes via :meth:`feed`; when a complete ``#...#`` payload is
    seen the frame is decoded and ``on_message`` (if supplied) is invoked
    with the resulting holds as ``(role_code, column, row, r, g, b)``
    tuples — the shape :class:`board_state.BoardState` expects.

    Mirrors BoardSesh's ``MoonBoardProtocol`` C++ class.
    """

    def __init__(
        self,
        on_message: Callable[[list[tuple[int, int, int, int, int, int]]], None]
        | None = None,
        grid_rows: int = NUM_ROWS,
    ) -> None:
        """Initialize the decoder.

        Args:
            on_message: Callback invoked with the decoded holds when a
                full frame is received.
            grid_rows: Per-column height the BLE serpentine walks — 18
                for standard 11×18 boards, 12 for Mini 2020. Default
                matches the standard boards.
        """
        self.on_message = on_message
        self.grid_rows = grid_rows
        self._state = _PARSER_IDLE
        self._pending_lights_above_holds = False
        self._raw_payload = ""
        self._problem_payload = ""
        self._decoded: DecodedProblem | None = None

    @property
    def decoded_problem(self) -> DecodedProblem | None:
        """The most recently decoded problem, or None if none yet."""
        return self._decoded

    def reset(self) -> None:
        """Discard any partially-buffered frame and return to idle."""
        self._state = _PARSER_IDLE
        self._pending_lights_above_holds = False
        self._raw_payload = ""
        self._problem_payload = ""

    def feed(self, data: bytes | bytearray | str) -> bool:
        """Feed raw bytes (or a string) from a BLE write into the decoder.

        Bytes are processed one at a time so a frame split across
        arbitrary chunk boundaries still reassembles. Returns True if at
        least one complete frame was decoded during this call.
        """
        if isinstance(data, str):
            data = data.encode("ascii", errors="ignore")
        completed = False
        for byte in data:
            if self._process_byte(chr(byte)):
                completed = True
        return completed

    def _process_byte(self, ch: str) -> bool:
        """Process a single incoming character. Returns True on frame end."""
        completed = False

        if self._state == _PARSER_IDLE:
            if ch == "~":
                self._pending_lights_above_holds = False
                self._raw_payload = "~"
                self._state = _PARSER_CONFIG
            elif ch == "l":
                self._pending_lights_above_holds = False
                self._raw_payload = "l"
                self._state = _PARSER_WAIT_FOR_PAYLOAD_START

        elif self._state == _PARSER_CONFIG:
            if ch == "~":
                self._pending_lights_above_holds = False
                self._raw_payload = "~"
            elif ch == "D":
                # '~D...#' means "light the aux LED above each hold".
                self._pending_lights_above_holds = True
                self._raw_payload += ch
            elif ch == "l":
                self._raw_payload += ch
                self._state = _PARSER_WAIT_FOR_PAYLOAD_START
            else:
                self._raw_payload += ch

        elif self._state == _PARSER_WAIT_FOR_PAYLOAD_START:
            if ch == "#":
                self._raw_payload += ch
                self._problem_payload = ""
                self._state = _PARSER_READING_PAYLOAD
            elif ch == "~":
                self._pending_lights_above_holds = False
                self._raw_payload = "~"
                self._state = _PARSER_CONFIG
            elif ch == "l":
                self._pending_lights_above_holds = False
                self._raw_payload = "l"

        elif self._state == _PARSER_READING_PAYLOAD:
            if ch == "#":
                self._raw_payload += ch
                completed = self._finalize_payload()
                self.reset()
            elif ch == "~":
                self._pending_lights_above_holds = False
                self._raw_payload = "~"
                self._problem_payload = ""
                self._state = _PARSER_CONFIG
            else:
                self._raw_payload += ch
                self._problem_payload += ch

        return completed

    def _finalize_payload(self) -> bool:
        """Decode the buffered payload and fire the callback. Returns True."""
        logger.debug("Payload complete: %s", self._raw_payload)
        problem = decode_problem(
            self._problem_payload,
            self._raw_payload,
            self._pending_lights_above_holds,
            grid_rows=self.grid_rows,
        )
        self._decoded = problem
        logger.info(
            "Decoded frame: %d holds, %d LEDs (lights_above_holds=%s)",
            len(problem.holds),
            len(problem.led_commands),
            problem.lights_above_holds,
        )
        if self.on_message:
            holds = [
                (h.role_code, h.column, h.row, *h.color) for h in problem.holds
            ]
            self.on_message(holds)
        return True


def decode_problem(
    payload: str,
    raw_payload: str = "",
    lights_above_holds: bool = False,
    grid_rows: int = NUM_ROWS,
) -> DecodedProblem:
    """Decode the inner ``<token><pos>,<token><pos>,...`` body of a frame.

    This is the pure parsing step, independent of BLE buffering — handy
    for tests and for callers that already have a complete payload.

    Args:
        payload: The inner body, e.g. ``"S0,P1,E197"`` (no ``l#``/``#``).
        raw_payload: The full raw frame, stored on the result for debug.
        lights_above_holds: If True, also emit yellow aux-LED markers.
        grid_rows: Per-column height for the serpentine arithmetic —
            18 for standard 11×18 boards, 12 for Mini 2020.
    """
    holds: list[DecodedHold] = []
    led_commands: list[LedCommand] = []

    payload = payload.strip()
    if not payload:
        return DecodedProblem(holds, led_commands, raw_payload, payload,
                              lights_above_holds)

    for token_str in payload.split(","):
        token_str = token_str.strip()
        if not token_str:
            continue
        token = token_str[0]
        role_code = token_to_role_code(token)
        if role_code == 0:
            logger.debug("Skipping unknown token: %r", token_str)
            continue

        # Parse the leading run of digits after the token as the position.
        digits = ""
        for c in token_str[1:]:
            if c.isdigit():
                digits += c
            else:
                break
        if not digits:
            logger.debug("Token %r has no position, skipping", token_str)
            continue
        serial_position = int(digits)

        hold_id = serial_position_to_hold_id(serial_position, grid_rows)
        if hold_id <= 0:
            logger.debug(
                "Serial position %d outside grid, skipping", serial_position
            )
            continue

        column, row = serial_position_to_grid(serial_position, grid_rows)
        color = token_to_color(token)
        holds.append(
            DecodedHold(
                serial_position=serial_position,
                hold_id=hold_id,
                column=column,
                row=row,
                token=token,
                role_code=role_code,
                color=color,
            )
        )
        led_commands.append(LedCommand(serial_position, *color))

        # Aux-LED "lights above holds" marker (skip for finish holds).
        should_add_aux = (
            lights_above_holds
            and token.lower() != "e"
            and 0 <= serial_position < STRIP_LED_COUNT
        )
        if should_add_aux:
            offset = ADDITIONAL_LED_OFFSETS[serial_position]
            if offset != 0:
                aux_index = serial_position + offset
                if 0 <= aux_index < STRIP_LED_COUNT:
                    led_commands.append(LedCommand(aux_index, *COLOR_YELLOW))

    logger.debug(
        "Parsed %d holds, %d LED commands", len(holds), len(led_commands)
    )
    return DecodedProblem(holds, led_commands, raw_payload, payload,
                          lights_above_holds)
