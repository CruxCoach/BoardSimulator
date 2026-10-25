"""Shiny Wall controller protocol: ASCII messages over one characteristic.

Messages follow ``<command><payload>.``, except the clear (source: the
official app's BLE controller, see the CruxCoach run folder's
APP-BEHAVIOUR-REPORT; no firmware or hardware was available):

- ``-`` clears the wall (no terminating dot).
- ``+<digits>.`` sets the whole wall: one digit per physical hold
  ``0 … hold_count-1`` without separators (0 off, 2 start, 4 hand, 6 foot,
  8 top). The official app sends ``-`` first, then this vector.
- ``*<r><id>.`` sets one hold without clearing (the official app's route
  editor): role digit ``r`` (0 off, 1 start, 2 hand, 3 foot, 4 top — not the
  vector codes) and the hold id, taken as the vector index. ``*@.`` is the
  disco mode (maintenance) and ``*a/b/….`` a game command; both are logged
  only, their effect is not known.
- ``f<initFootLeds>.`` sent once after connecting when the wall has the
  setting (meaning unknown: logged only).
- anything else ending in ``.`` is an unknown command (logged only); the
  old draft format ``.c0+c1+…+cN.`` is reported as such.

The official app writes pieces of ``MTU - 5`` bytes, so a message can span
writes. This decoder is a byte-level state machine: the same stream gives
the same result however it is split, and every deviation is reported
instead of guessed around:

- A vector whose length differs from ``hold_count`` is applied as far as it
  fits and reported (``lenient``, the default) or rejected with the state
  unchanged (``strict``).
- A byte in a vector that is not a digit is an invalid code: that hold
  stays off. A digit that is not a known code is reported and switched off
  (``reject``, the default) or kept as an unknown role (``accept``).
- ``-`` inside a message discards the partial message, then clears.
- Bytes outside any message (spaces, control bytes, a stray ``.``) are
  reported as garbage once per run.
- A message longer than the overflow limit is discarded up to its end.

The decoder is pure (no BLE, no logging); ``protocols.shinywall_session``
logs the returned events with the write context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

import config

CODE_OFF = 0
CODE_START = 2
CODE_HAND = 4
CODE_FOOT = 6
CODE_TOP = 8
ROLE_NAMES: dict[int, str] = {
    CODE_START: "start", CODE_HAND: "hand", CODE_FOOT: "foot", CODE_TOP: "top",
}
KNOWN_CODES = frozenset({CODE_OFF, *ROLE_NAMES})
ROLE_LETTERS: dict[int, str] = {
    CODE_START: "S", CODE_HAND: "H", CODE_FOOT: "F", CODE_TOP: "T",
}
# Simulator display colours (neutral, not the vendor's); mirrored in
# mobile/shared/protocols.js.
ROLE_RGB: dict[int, tuple[int, int, int]] = {
    CODE_START: (34, 221, 68), CODE_HAND: (58, 140, 255),
    CODE_FOOT: (255, 210, 31), CODE_TOP: (255, 59, 59),
}
UNKNOWN_RGB = (255, 255, 255)
# Role digits of the single-hold command '*<r><id>.' (the app's route
# editor), mapped to the vector codes.
EDITOR_ROLES: dict[int, int] = {
    0: CODE_OFF, 1: CODE_START, 2: CODE_HAND, 3: CODE_FOOT, 4: CODE_TOP,
}

CMD_CLEAR = ord("-")
CMD_STATE = ord("+")
CMD_SINGLE = ord("*")
CMD_FOOT = ord("f")
TERMINATOR = ord(".")

LENGTH_LENIENT = "lenient"
LENGTH_STRICT = "strict"
LENGTH_MODES = (LENGTH_LENIENT, LENGTH_STRICT)
CODES_REJECT = "reject"
CODES_ACCEPT = "accept"
CODES_MODES = (CODES_REJECT, CODES_ACCEPT)
DISCONNECT_KEEP = "keep"
DISCONNECT_CLEAR = "clear"
DISCONNECT_MODES = (DISCONNECT_KEEP, DISCONNECT_CLEAR)

# The official app's chunking: MTU - 5 bytes per write (18 at the default
# ATT MTU of 23), 100 ms before the first chunk, 50 ms between chunks.
CHUNK_OVERHEAD = 5
MIN_CHUNK_BYTES = 18
APP_FIRST_CHUNK_DELAY_MS = 100
APP_CHUNK_GAP_MS = 50
APP_REQUESTED_MTU = 512
ACK = b"OK"

SHOWN_BYTES = 48

# Event kinds.
EV_GARBAGE = "garbage"
EV_ABORT = "abort"
EV_CLEAR = "clear"
EV_LENGTH = "length"
EV_INVALID = "invalid"
EV_UNKNOWN = "unknown-code"
EV_REJECT = "reject"
EV_STATE = "state"
EV_SINGLE = "single"
EV_HOLD = "hold"
EV_DISCO = "disco"
EV_GAME = "game"
EV_FOOT = "foot"
EV_COMMAND = "command"
EV_LEGACY = "legacy"
EV_OVERFLOW = "overflow"
EVENT_KINDS = (EV_GARBAGE, EV_ABORT, EV_CLEAR, EV_LENGTH, EV_INVALID,
               EV_UNKNOWN, EV_REJECT, EV_STATE, EV_SINGLE, EV_HOLD, EV_DISCO,
               EV_GAME, EV_FOOT, EV_COMMAND, EV_LEGACY, EV_OVERFLOW)
# Messages the controller understood (acknowledged with "OK" when enabled).
UNDERSTOOD = frozenset({EV_CLEAR, EV_STATE, EV_HOLD, EV_DISCO, EV_GAME,
                        EV_FOOT})


@dataclass(frozen=True)
class ShinyEvent:
    """One decoder observation; ``kind`` is one of :data:`EVENT_KINDS`."""

    kind: str
    detail: str
    indices: tuple[int, ...] = field(default=())


@dataclass(frozen=True)
class ShinyWallOptions:
    """Simulator knobs for the Shiny Wall mode (all CLI flags in one place).

    ``hold_count`` overrides the variant's (and the geometry file's) vector
    length; ``mtu`` is the ATT MTU the simulated controller can receive
    (longer writes are dropped); ``delay_ms`` is a processing delay per
    write that blocks the BLE loop like a busy controller; ``ack`` notifies
    "OK" after every understood message, on the write characteristic (the
    official app listens there) or on an extra notify-only characteristic
    ``notify_uuid``.
    """

    name: str | None = None
    identity: str = config.SHINYWALL_DEFAULT_IDENTITY
    service_uuid: str = config.SHINYWALL_SERVICE_UUID
    advertise_uuid: bool = True
    hold_count: int | None = None
    length_mode: str = LENGTH_LENIENT
    codes_mode: str = CODES_REJECT
    mtu: int | None = None
    delay_ms: float = 0.0
    on_disconnect: str = DISCONNECT_KEEP
    props: tuple[str, ...] = config.SHINYWALL_DEFAULT_PROPS
    ack: bool = False
    notify_uuid: str | None = None
    geometry_path: str | None = None

    def validated(self) -> "ShinyWallOptions":
        """Return a normalised copy, or raise ValueError with a CLI hint."""
        from dataclasses import replace

        if self.name is not None:
            config.check_ble_name(self.name)
        if self.hold_count is not None and not 1 <= self.hold_count <= 4096:
            raise ValueError("--shiny-holds must be between 1 and 4096")
        if self.length_mode not in LENGTH_MODES:
            raise ValueError(f"--shiny-length must be one of {LENGTH_MODES}")
        if self.codes_mode not in CODES_MODES:
            raise ValueError(f"--shiny-codes must be one of {CODES_MODES}")
        if self.on_disconnect not in DISCONNECT_MODES:
            raise ValueError(
                f"--shiny-on-disconnect must be one of {DISCONNECT_MODES}")
        if self.mtu is not None and not 23 <= self.mtu <= 517:
            raise ValueError("--shiny-mtu must be between 23 and 517")
        if self.delay_ms < 0:
            raise ValueError("--shiny-delay must not be negative")
        props = tuple(dict.fromkeys(p.strip().lower() for p in self.props
                                    if p.strip()))
        unknown = [p for p in props if p not in config.SHINYWALL_ALLOWED_PROPS]
        if unknown:
            raise ValueError(
                f"unknown --shiny-props {unknown}; choose from "
                f"{list(config.SHINYWALL_ALLOWED_PROPS)}")
        if not {"write", "write-without-response"} & set(props):
            raise ValueError(
                "--shiny-props needs write and/or write-without-response")
        notify_uuid = (config.normalize_uuid(self.notify_uuid)
                       if self.notify_uuid else None)
        if notify_uuid == config.SHINYWALL_WRITE_UUID:
            raise ValueError("--shiny-notify-uuid must differ from the write "
                             "characteristic")
        if self.ack and not notify_uuid and not {"notify", "indicate"} & set(props):
            props += ("notify",)
        return replace(self, props=props, notify_uuid=notify_uuid,
                       service_uuid=config.normalize_uuid(self.service_uuid))


def _shown(raw: bytes | bytearray) -> str:
    """Printable excerpt of raw bytes for a log line."""
    text = bytes(raw[:SHOWN_BYTES]).decode("ascii", errors="backslashreplace")
    text = "".join(c if c.isprintable() else repr(c)[1:-1] for c in text)
    more = f"… ({len(raw)} B)" if len(raw) > SHOWN_BYTES else ""
    return f"'{text}'{more}"


def _index_list(indices: Sequence, limit: int = 24) -> str:
    shown = ", ".join(str(i) for i in indices[:limit])
    return f"[{shown}{', …' if len(indices) > limit else ''}]"


def summarize(codes: Mapping[int, int]) -> str:
    """``start 2 [12, 40], hand 5 […], foot 0 [], top 1 [300]`` (+ unknown)."""
    parts = []
    for code, role in ROLE_NAMES.items():
        indices = sorted(i for i, c in codes.items() if c == code)
        parts.append(f"{role} {len(indices)} {_index_list(indices)}")
    unknown = sorted(i for i, c in codes.items() if c not in KNOWN_CODES)
    if unknown:
        parts.append(f"unknown {len(unknown)} "
                     + _index_list([f"{i}={codes[i]}" for i in unknown]))
    return ", ".join(parts)


def _starts_message(byte: int) -> bool:
    """Printable, not a space, not the terminator: a command character."""
    return 0x21 <= byte <= 0x7E and byte != TERMINATOR


class ShinyWallProtocol:
    """Streaming decoder for one central's writes.

    ``on_state`` receives the complete new wall state (hold index -> code,
    lit holds only) after every clear, applied vector and single-hold
    command. A single-hold command changes the wall as ``current()`` returns
    it (the session passes the wall shared by all centrals); by default the
    decoder's own last state.
    """

    COMMAND_LIMIT = 4096

    def __init__(self, hold_count: int,
                 on_state: Callable[[dict[int, int]], None],
                 length_mode: str = LENGTH_LENIENT,
                 codes_mode: str = CODES_REJECT,
                 max_message_bytes: int | None = None,
                 current: Callable[[], Mapping[int, int]] | None = None) -> None:
        if hold_count < 1:
            raise ValueError("hold_count must be positive")
        self.hold_count = hold_count
        self.length_mode = length_mode
        self.codes_mode = codes_mode
        self.max_message_bytes = max_message_bytes or max(
            self.COMMAND_LIMIT, 4 * hold_count)
        self._on_state = on_state
        self._last: dict[int, int] = {}
        self._current = current or (lambda: self._last)
        self._command: int | None = None   # command byte of the open message
        self._discarding = False
        self._payload = bytearray()
        self._garbage = bytearray()
        self._garbage_total = 0
        self._events: list[ShinyEvent] = []
        self.messages = 0

    @property
    def in_message(self) -> bool:
        """Whether a message is partly received."""
        return self._command is not None

    def feed(self, data: Iterable[int]) -> list[ShinyEvent]:
        """Consume one write; return the events it completed."""
        for byte in data:
            self._byte(byte)
        events, self._events = self._events, []
        return events

    def flush(self) -> list[ShinyEvent]:
        """Report a pending garbage run now (e.g. at the end of a link)."""
        self._end_garbage()
        events, self._events = self._events, []
        return events

    def reset(self) -> list[ShinyEvent]:
        """Drop the partial message and garbage (the link ended)."""
        self._end_garbage()
        if self._command is not None:
            self._emit(EV_ABORT, f"link ended inside {self._what()}; discarded "
                       f"{self._partial()}")
        self._command = None
        self._discarding = False
        self._payload.clear()
        events, self._events = self._events, []
        return events

    # -- state machine --------------------------------------------------

    def _what(self) -> str:
        return {CMD_STATE: "a state vector", CMD_SINGLE: "a '*' command",
                CMD_FOOT: "an 'f' command"}.get(
                    self._command, f"a {chr(self._command or 63)!r} message")

    def _partial(self) -> str:
        return (f"{len(self._payload) + 1} B "
                f"{_shown(bytes([self._command or 0]) + self._payload)}")

    def _emit(self, kind: str, detail: str, indices: Sequence[int] = ()) -> None:
        self._events.append(ShinyEvent(kind, detail, tuple(indices)))

    def _end_garbage(self) -> None:
        if self._garbage_total:
            self._emit(EV_GARBAGE, f"{self._garbage_total} B outside a message: "
                       f"{_shown(self._garbage)} hex "
                       f"{bytes(self._garbage[:SHOWN_BYTES]).hex()}")
            self._garbage.clear()
            self._garbage_total = 0

    def _clear(self) -> None:
        self.messages += 1
        self._emit(EV_CLEAR, "'-': all holds off")
        self._publish({})

    def _publish(self, state: dict[int, int]) -> None:
        self._last = state
        self._on_state(state)

    def _byte(self, byte: int) -> None:
        if byte == CMD_CLEAR:
            self._end_garbage()
            if self._command is not None and not self._discarding:
                self._emit(EV_ABORT, f"'-' inside {self._what()}; discarded "
                           f"{self._partial()}")
            self._command = None
            self._discarding = False
            self._payload.clear()
            self._clear()
            return
        if self._command is None:
            if _starts_message(byte):
                self._end_garbage()
                self._command = byte
                self._payload.clear()
            else:
                self._garbage_total += 1
                if len(self._garbage) < SHOWN_BYTES:
                    self._garbage.append(byte)
            return
        if byte == TERMINATOR:
            command, payload, discarding = (self._command, bytes(self._payload),
                                            self._discarding)
            self._command = None
            self._discarding = False
            self._payload.clear()
            if not discarding:
                self.messages += 1
                self._dispatch(command, payload)
            return
        if self._discarding:
            return
        self._payload.append(byte)
        if len(self._payload) > self.max_message_bytes:
            self._emit(EV_OVERFLOW, f"{self._what()} longer than "
                       f"{self.max_message_bytes} B without a closing '.'; "
                       "discarded up to its end")
            self._discarding = True
            self._payload.clear()

    def _dispatch(self, command: int, payload: bytes) -> None:
        if command == CMD_STATE:
            self._vector(payload)
        elif command == CMD_SINGLE:
            self._single(payload)
        elif command == CMD_FOOT:
            self._emit(EV_FOOT, f"init foot LEDs f{_shown(payload)[1:-1]}. "
                       "(meaning unknown; logged, not applied)")
        elif chr(command).isdigit() and b"+" in payload:
            self._emit(EV_LEGACY, f"old dotted format '.{chr(command)}"
                       f"{_shown(payload)[1:-1]}.' ({len(payload) + 1} B) — "
                       "the controller expects '+', one digit per hold, '.'; "
                       "ignored")
        else:
            self._emit(EV_COMMAND, f"unknown command {chr(command)!r} with "
                       f"payload {_shown(payload)}; ignored")

    def _vector(self, payload: bytes) -> None:
        codes: list[int] = []
        invalid: list[int] = []
        unknown: list[int] = []
        for index, byte in enumerate(payload):
            if not 0x30 <= byte <= 0x39:
                invalid.append(index)
                codes.append(CODE_OFF)
                continue
            value = byte - 0x30
            if value not in KNOWN_CODES:
                unknown.append(index)
                if self.codes_mode == CODES_REJECT:
                    value = CODE_OFF
            codes.append(value)
        if invalid:
            shown = ", ".join(f"{i}={_shown(payload[i:i + 1])}"
                              for i in invalid[:12])
            self._emit(EV_INVALID, f"{len(invalid)} invalid code(s), hold(s) "
                       f"left off: {shown}{', …' if len(invalid) > 12 else ''}",
                       invalid)
        if unknown:
            action = ("kept as unknown role" if self.codes_mode == CODES_ACCEPT
                      else "hold(s) left off")
            shown = ", ".join(f"{i}={payload[i] - 0x30}" for i in unknown[:12])
            self._emit(EV_UNKNOWN, f"{len(unknown)} unknown code(s), {action}: "
                       f"{shown}{', …' if len(unknown) > 12 else ''}", unknown)
        count = len(codes)
        if count != self.hold_count:
            tail = [i for i in range(self.hold_count, count) if codes[i]]
            detail = (f"vector has {count} codes, this wall has "
                      f"{self.hold_count} holds")
            if count > self.hold_count:
                detail += (f"; {count - self.hold_count} extra code(s), lit ones "
                           f"beyond the wall: {_index_list(tail)}")
            else:
                detail += f"; holds {count}..{self.hold_count - 1} stay off"
            self._emit(EV_LENGTH, detail, tail)
            if self.length_mode == LENGTH_STRICT:
                self._emit(EV_REJECT, "strict length: vector rejected, "
                           "wall unchanged")
                return
        state = {i: c for i, c in enumerate(codes[:self.hold_count]) if c}
        self._emit(EV_STATE, f"{min(count, self.hold_count)} codes applied — "
                   f"{summarize(state)}; {self.hold_count - len(state)} off",
                   sorted(state))
        self._publish(state)

    def _single(self, payload: bytes) -> None:
        shown = f"*{_shown(payload)[1:-1]}."
        if payload == b"@":
            self._emit(EV_DISCO, f"{shown} disco mode (maintenance; logged, "
                       "not applied)")
            return
        if b"/" in payload:
            self._changes(shown, payload.split(b"/"))
            return
        if len(payload) < 2 or not payload.isdigit():
            self._emit(EV_SINGLE, f"{shown} not understood (expected "
                       "*<role><id>., *@. or a game *a/b/….); ignored")
            return
        role, index = payload[0] - 0x30, int(payload[1:])
        if role not in EDITOR_ROLES:
            self._emit(EV_UNKNOWN, f"{shown} role {role} unknown (0 off, 1 start, "
                       "2 hand, 3 foot, 4 top); ignored")
            return
        if index >= self.hold_count:
            self._emit(EV_INVALID, f"{shown} hold {index} is beyond the wall "
                       f"(holds 0..{self.hold_count - 1}); ignored", (index,))
            return
        wall = dict(self._current())
        code = EDITOR_ROLES[role]
        if code:
            wall[index] = code
        else:
            wall.pop(index, None)
        self._emit(EV_HOLD, f"{shown} hold {index} -> "
                   f"{ROLE_NAMES.get(code, 'off')}; {len(wall)} lit",
                   (index,) if code else ())
        self._publish(wall)


    def _changes(self, shown: str, parts: list[bytes]) -> None:
        """``*<role><id>/<role><id>/….``: several holds at once, the rest of
        the wall unchanged (the official app's games; CruxCoach's editor and
        games). Applied only when every part is valid."""
        changes: dict[int, int] = {}
        for part in parts:
            if len(part) < 2 or not part.isdigit():
                self._emit(EV_GAME, f"{shown} part '{part.decode('ascii', 'replace')}' "
                           "not understood (expected <role><id>); message ignored")
                return
            role, index = part[0] - 0x30, int(part[1:])
            if role not in EDITOR_ROLES or index >= self.hold_count:
                self._emit(EV_INVALID, f"{shown} part {part.decode()} has an unknown "
                           f"role or a hold beyond 0..{self.hold_count - 1}; message "
                           "ignored", (index,))
                return
            changes[index] = EDITOR_ROLES[role]
        wall = dict(self._current())
        for index, code in changes.items():
            if code:
                wall[index] = code
            else:
                wall.pop(index, None)
        lit = sorted(i for i, c in changes.items() if c)
        self._emit(EV_GAME, f"{shown} {len(changes)} hold(s) changed; "
                   f"{len(wall)} lit — {summarize(wall)}", tuple(lit))
        self._publish(wall)


# -- reference encoder (tests, mock client, mobile fixtures) -----------------

def encode_vector(codes: Sequence[int]) -> bytes:
    """``+`` + one digit per hold + ``.``, as the official app builds it."""
    if any(not 0 <= int(c) <= 9 for c in codes):
        raise ValueError("Shiny Wall codes are single digits")
    return b"+" + bytes(0x30 + int(c) for c in codes) + b"."


def state_vector(holds: Mapping[int, int], hold_count: int) -> list[int]:
    """Codes for every hold from a sparse ``index -> code`` map."""
    codes = [CODE_OFF] * hold_count
    for index, code in holds.items():
        codes[index] = code
    return codes


def chunks(message: bytes, mtu: int = 23) -> list[bytes]:
    """Split a message like the official app: ``max(MTU - 5, 18)`` bytes."""
    size = max(mtu - CHUNK_OVERHEAD, MIN_CHUNK_BYTES)
    return [message[i:i + size] for i in range(0, len(message), size)] or [b""]


def encode_hold(index: int, code: int) -> bytes:
    """``*<role><id>.`` for one hold, as the official app's route editor."""
    role = {c: r for r, c in EDITOR_ROLES.items()}[code]
    return f"*{role}{index}.".encode()


def route_writes(holds: Mapping[int, int], hold_count: int,
                 mtu: int = 23) -> list[bytes]:
    """The writes for showing a route: ``-``, then the chunked vector."""
    return chunks(b"-", mtu) + chunks(
        encode_vector(state_vector(holds, hold_count)), mtu)
