"""Clean-room Quantum binary protocols (2.0.14 default, 1.44 legacy).

The default response policy follows the captured Quantum XL controller: a
successful ATT write changes the LEDs, but does not imply an application-level
``fff1`` echo or a changed ``fff4`` route roster.  Parser-shaped state replies
remain available as an explicitly synthetic app-test policy.
"""

from __future__ import annotations

import json
import logging
import struct
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Callable, Iterable

logger = logging.getLogger(__name__)


class Command(IntEnum):
    ACTIVATE_WALL = 0x41
    TURN_OFF_BY_ROUTE = 0x42
    TURN_OFF_BY_USER = 0x43
    BOARD_SWIPE = 0x44
    TURN_OFF_ALL = 0x45
    CHANGE_ROUTE_PARAMS = 0x46
    REQUEST_USER_ROUTE_LIST = 0x47
    ACTIVATE_WALL_LED_ID = 0x48
    TURN_ON_ALL = 0x64
    SET_START_HOLDS = 0x65
    SET_STEP_HOLDS = 0x66
    SET_FINISH_HOLDS = 0x67


class WireVersion(str, Enum):
    EWALLS_2_0_14 = "2.0.14"
    EWALLS_1_44 = "1.44"


class ResponsePolicy(str, Enum):
    """Controller response behaviour selected independently of wire syntax."""

    OBSERVED_XL = "observed-xl"
    SYNTHETIC_STATE = "synthetic-state"


CURRENT_COMMANDS = frozenset({
    Command.ACTIVATE_WALL, Command.TURN_OFF_BY_ROUTE,
    Command.TURN_OFF_BY_USER, Command.BOARD_SWIPE, Command.TURN_OFF_ALL,
    Command.REQUEST_USER_ROUTE_LIST, Command.TURN_ON_ALL,
})
LEGACY_ONLY_COMMANDS = frozenset(set(Command) - CURRENT_COMMANDS)
CHUNK_LIMITS = {
    Command.ACTIVATE_WALL: 92, Command.BOARD_SWIPE: 92,
    Command.ACTIVATE_WALL_LED_ID: 32, Command.SET_START_HOLDS: 120,
    Command.SET_STEP_HOLDS: 120, Command.SET_FINISH_HOLDS: 120,
}
FIXED_LENGTHS = {
    Command.TURN_OFF_BY_ROUTE: 21, Command.TURN_OFF_BY_USER: 21,
    Command.TURN_OFF_ALL: 8, Command.CHANGE_ROUTE_PARAMS: 25,
    Command.TURN_ON_ALL: 9,
}
EXCEPTION_CODES = {
    1: "ILLEGAL_FUNCTION", 2: "ILLEGAL_DATA_ADDRESS",
    3: "ILLEGAL_DATA_VALUE", 4: "SLAVE_DEVICE_FAILURE",
    5: "ROUTE_IN_USE", 6: "SPOT_UNAVAILABLE", 7: "COLOR_TAKEN",
    8: "USER_ID_IN_USE", 9: "NO_MORE_USERS_ACCEPTED",
    10: "USER_ID_IN_ROUTESETTER_MODE", 11: "ACAD_ID_NOT_IN_MAP",
    254: "WRITE_ACK_TIMEOUT",
}

# eWalls 2.0.14's normal route-play path passes 0xffff.  This is an app-side
# default recovered from the original implementation, not a firmware rule:
# the captured XL lit both 300-second and 0xffff routes but kept fff4 empty.
EWALLS_ROUTE_DURATION_SECONDS = 0xFFFF


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def uuid_to_bytes(value: str) -> bytes:
    compact = value.replace("-", "")
    if len(compact) != 32:
        raise ValueError("id must contain exactly 32 hexadecimal characters")
    try:
        return bytes.fromhex(compact)
    except ValueError as exc:
        raise ValueError("id must contain only hexadecimal characters") from exc


def uuid_from_bytes(value: bytes) -> str:
    if len(value) != 16:
        raise ValueError("UUID payload must contain 16 bytes")
    text = value.hex()
    return f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}"


def _legacy_id16(value: str) -> bytes:
    return value.encode("utf-8")[:16].ljust(16, b"\0")


def _decode_legacy_id(value: bytes) -> str:
    return value.rstrip(b"\0").decode("utf-8", errors="replace")


def _rgb(value: str | tuple[int, int, int]) -> bytes:
    encoded = bytes(value) if isinstance(value, tuple) else bytes.fromhex(
        value.removeprefix("#"))
    if len(encoded) != 3:
        raise ValueError("color must contain exactly three RGB bytes")
    return encoded


def _with_crc(body: bytes, wire: WireVersion) -> bytes:
    order = "big" if wire is WireVersion.EWALLS_2_0_14 else "little"
    return body + crc16_modbus(body).to_bytes(2, order)


def encode(command: Command | int, *, route_id: str = "", user_id: str = "",
           color: str | tuple[int, int, int] = "#000000", duration: int = 0,
           enable_animation: int = 0, animation_speed: int = 1,
           diodes: Iterable[int] = (), row_number: int = 0,
           internal_index: int = 1, count_of_routes: int = 61,
           wire: WireVersion = WireVersion.EWALLS_2_0_14) -> bytes:
    """Encode one frame; long diode lists must be chunked separately."""
    cmd = Command(command)
    if wire is WireVersion.EWALLS_2_0_14 and cmd not in CURRENT_COMMANDS:
        raise ValueError(f"{cmd.name} is not emitted by eWalls 2.0.14")
    values = list(diodes)
    limit = CHUNK_LIMITS.get(cmd)
    if limit is not None and len(values) > limit:
        raise ValueError(f"{cmd.name} allows at most {limit} diodes per frame")
    id16 = uuid_to_bytes if wire is WireVersion.EWALLS_2_0_14 else _legacy_id16
    if cmd in (Command.SET_START_HOLDS, Command.SET_STEP_HOLDS,
               Command.SET_FINISH_HOLDS):
        payload = (_rgb(color) + struct.pack(">H", animation_speed)
                   + bytes((2 * len(values),))
                   + b"".join(struct.pack(">H", d) for d in values))
    elif cmd == Command.TURN_ON_ALL:
        payload = _rgb(color) + struct.pack(">H", duration)
    elif cmd == Command.ACTIVATE_WALL_LED_ID:
        payload = id16(route_id) + id16(user_id) + _rgb(color)
        payload += struct.pack(">HBB", duration, enable_animation, 4 * len(values))
        payload += b"".join(struct.pack(">I", d) for d in values)
    elif cmd == Command.REQUEST_USER_ROUTE_LIST:
        payload = (bytes((row_number & 0xFF,))
                   if wire is WireVersion.EWALLS_2_0_14
                   else struct.pack(">HH", internal_index, count_of_routes))
    elif cmd == Command.CHANGE_ROUTE_PARAMS:
        payload = id16(route_id) + _rgb(color) + struct.pack(">H", duration)
    elif cmd == Command.TURN_OFF_ALL:
        payload = struct.pack(">HH", 1, 0)
    elif cmd == Command.TURN_OFF_BY_USER:
        payload = id16(user_id) + b"\0"
    elif cmd == Command.TURN_OFF_BY_ROUTE:
        payload = id16(route_id) + b"\0"
    else:
        payload = id16(route_id) + id16(user_id) + _rgb(color)
        payload += struct.pack(">HBB", duration, enable_animation, 2 * len(values))
        payload += b"".join(struct.pack(">H", d) for d in values)
    return _with_crc(bytes((1, cmd)) + payload, wire)


def encode_chunks(command: Command | int, *, diodes: Iterable[int] = (),
                  **kwargs) -> list[bytes]:
    cmd = Command(command)
    values = list(diodes)
    limit = CHUNK_LIMITS.get(cmd)
    groups = [values] if limit is None else [
        values[i:i + limit] for i in range(0, len(values), limit)]
    return [encode(cmd, diodes=group, **kwargs) for group in (groups or [[]])]


@dataclass(frozen=True)
class QuantumAction:
    command: Command
    route_id: str = ""
    user_id: str = ""
    color: tuple[int, int, int] = (0, 0, 0)
    duration: int = 0
    animation: int = 0
    diodes: tuple[int, ...] = ()
    replace: bool = True
    legacy: bool = False
    wire: WireVersion = WireVersion.EWALLS_2_0_14


@dataclass(frozen=True)
class ProtocolEvent:
    ok: bool
    code: str
    command: Command | None = None
    detail: str = ""


def encode_exception(command: Command | int, code: int) -> bytes:
    if code not in EXCEPTION_CODES:
        raise ValueError(f"unknown Quantum exception code {code}")
    return bytes((1, int(command) | 0x80, code))


def encode_broadcast(action: QuantumAction,
                     players: Iterable[QuantumAction] | None = None) -> bytes | None:
    """Encode synthetic state matching eWalls 2.0.14 ``parseBroadcast``.

    This describes what the original app can parse.  It is not evidence that a
    real controller echoes every accepted command; the captured XL does not.
    """
    cmd = action.command
    if cmd in (Command.ACTIVATE_WALL, Command.BOARD_SWIPE):
        active = list(players) if players is not None else [action]
        payload = b"".join(
            uuid_to_bytes(player.route_id) + uuid_to_bytes(player.user_id)
            + struct.pack(">H", player.duration) + bytes(player.color)
            for player in active)
        return bytes((1, cmd, len(active) & 0xFF, 0)) + payload
    if cmd == Command.REQUEST_USER_ROUTE_LIST:
        active = list(players or ())
        payload = b"".join(
            uuid_to_bytes(player.route_id) + uuid_to_bytes(player.user_id)
            + struct.pack(">H", player.duration) + bytes(player.color)
            for player in active)
        return bytes((1, cmd, len(active) & 0xFF, 0)) + payload
    if cmd == Command.TURN_OFF_BY_USER:
        return bytes((1, cmd)) + uuid_to_bytes(action.user_id) + b"\0\0\0"
    if cmd == Command.TURN_OFF_ALL:
        return bytes((1, cmd, 0, 0, 0, 0))
    if cmd == Command.TURN_ON_ALL:
        return bytes((1, cmd, 0xFF))
    return None


class QuantumProtocol:
    """Streaming auto-detecting decoder with diagnostic events/replies."""

    MAX_BUFFER = 4096

    def __init__(self, on_action: Callable[[QuantumAction], None],
                 on_event: Callable[[ProtocolEvent], None] | None = None,
                 reject_commands: set[Command] | None = None,
                 reject_code: int = 4,
                 response_policy: ResponsePolicy = ResponsePolicy.OBSERVED_XL) -> None:
        self.on_action = on_action
        self.on_event = on_event
        self.reject_commands = reject_commands or set()
        if reject_code not in EXCEPTION_CODES:
            raise ValueError("unknown reject_code")
        self.reject_code = reject_code
        self.response_policy = response_policy
        self._buffer = bytearray()
        self._json_buffer = ""
        self._continuation: tuple[Command, str, str, WireVersion] | None = None
        self._players: dict[str, QuantumAction] = {}

    def reset_transport(self) -> None:
        self._buffer.clear()
        self._json_buffer = ""
        self._continuation = None

    def _event(self, ok: bool, code: str, command: Command | None = None,
               detail: str = "") -> None:
        if self.on_event:
            self.on_event(ProtocolEvent(ok, code, command, detail))
        (logger.debug if ok else logger.warning)(
            "Quantum %s %s", code, detail or (command.name if command else ""))

    def feed(self, data: bytes | bytearray) -> list[bytes]:
        replies: list[bytes] = []
        if not data:
            return replies
        if self._json_buffer or bytes(data).lstrip().startswith((b"{", b"[")):
            self._feed_json(bytes(data))
            return replies
        self._buffer.extend(data)
        if len(self._buffer) > self.MAX_BUFFER:
            self._buffer.clear()
            self._event(False, "OVERFLOW", detail="binary buffer limit exceeded")
            return replies
        while self._buffer:
            if self._buffer[0] != 1:
                del self._buffer[0]
                self._event(False, "PREFIX", detail="discarded byte before frame")
                continue
            if len(self._buffer) < 2:
                break
            try:
                command = Command(self._buffer[1])
            except ValueError:
                bad = self._buffer[1]
                del self._buffer[:2]
                self._event(False, "COMMAND", detail=f"unknown 0x{bad:02x}")
                continue
            candidate = self._candidate(command)
            if candidate is None:
                break
            length, wire, crc_ok = candidate
            frame = bytes(self._buffer[:length])
            del self._buffer[:length]
            if not crc_ok:
                self._continuation = None
                self._event(False, "CRC", command, f"wire={wire.value}")
                replies.append(encode_exception(command, 3))
                continue
            if command in self.reject_commands:
                self._event(False, EXCEPTION_CODES[self.reject_code], command,
                            "fault profile")
                replies.append(encode_exception(command, self.reject_code))
                continue
            try:
                action = self._decode(frame, command, wire)
            except (ValueError, struct.error) as exc:
                self._continuation = None
                self._event(False, "PAYLOAD", command, str(exc))
                replies.append(encode_exception(command, 3))
                continue
            self.on_action(action)
            self._event(True, "ACK", command)
            if (wire is WireVersion.EWALLS_2_0_14 and
                    self.response_policy is ResponsePolicy.SYNTHETIC_STATE):
                self._track_players(action)
                reply = encode_broadcast(action, self._players.values())
                if reply is not None:
                    replies.append(reply)
        return replies

    def _track_players(self, action: QuantumAction) -> None:
        if action.command in (Command.ACTIVATE_WALL, Command.BOARD_SWIPE):
            if action.command == Command.BOARD_SWIPE:
                self._players = {
                    route: player for route, player in self._players.items()
                    if player.user_id != action.user_id or route == action.route_id}
            self._players[action.route_id] = action
        elif action.command == Command.TURN_OFF_BY_ROUTE:
            self._players.pop(action.route_id, None)
        elif action.command == Command.TURN_OFF_BY_USER:
            self._players = {
                route: player for route, player in self._players.items()
                if player.user_id != action.user_id}
        elif action.command == Command.TURN_OFF_ALL:
            self._players.clear()

    @staticmethod
    def _crc_ok(frame: bytes, wire: WireVersion) -> bool:
        order = "big" if wire is WireVersion.EWALLS_2_0_14 else "little"
        return int.from_bytes(frame[-2:], order) == crc16_modbus(frame[:-2])

    def _candidate(self, command: Command) -> tuple[int, WireVersion, bool] | None:
        if command == Command.REQUEST_USER_ROUTE_LIST:
            if len(self._buffer) < 5:
                return None
            current = bytes(self._buffer[:5])
            if self._crc_ok(current, WireVersion.EWALLS_2_0_14):
                return 5, WireVersion.EWALLS_2_0_14, True
            if len(self._buffer) < 8:
                if len(self._buffer) > 5 and self._buffer[5] == 1:
                    return 5, WireVersion.EWALLS_2_0_14, False
                return None
            legacy = bytes(self._buffer[:8])
            return 8, WireVersion.EWALLS_1_44, self._crc_ok(
                legacy, WireVersion.EWALLS_1_44)

        length = FIXED_LENGTHS.get(command)
        if length is None and command in (
                Command.ACTIVATE_WALL, Command.BOARD_SWIPE,
                Command.ACTIVATE_WALL_LED_ID):
            if len(self._buffer) <= 40:
                return None
            length = 43 + self._buffer[40]
        elif length is None and command in (
                Command.SET_START_HOLDS, Command.SET_STEP_HOLDS,
                Command.SET_FINISH_HOLDS):
            if len(self._buffer) <= 7:
                return None
            length = 10 + self._buffer[7]
        if length is None or len(self._buffer) < length:
            return None
        frame = bytes(self._buffer[:length])
        if command not in LEGACY_ONLY_COMMANDS and self._crc_ok(
                frame, WireVersion.EWALLS_2_0_14):
            return length, WireVersion.EWALLS_2_0_14, True
        return length, WireVersion.EWALLS_1_44, self._crc_ok(
            frame, WireVersion.EWALLS_1_44)

    def _decode(self, frame: bytes, command: Command,
                wire: WireVersion) -> QuantumAction:
        body = frame[:-2]
        decode_id = (uuid_from_bytes if wire is WireVersion.EWALLS_2_0_14
                     else _decode_legacy_id)
        route_id = user_id = ""
        color = (0, 0, 0)
        duration = animation = 0
        diodes: tuple[int, ...] = ()
        key = (command, "", "", wire)
        if command in (Command.ACTIVATE_WALL, Command.BOARD_SWIPE,
                       Command.ACTIVATE_WALL_LED_ID):
            route_id, user_id = decode_id(body[2:18]), decode_id(body[18:34])
            color = tuple(body[34:37])  # type: ignore[assignment]
            duration, animation, count = int.from_bytes(body[37:39], "big"), body[39], body[40]
            width = 4 if command == Command.ACTIVATE_WALL_LED_ID else 2
            if count % width or len(body) != 41 + count:
                raise ValueError("invalid diode byte count")
            diodes = tuple(int.from_bytes(body[i:i + width], "big")
                           for i in range(41, len(body), width))
            key = (command, route_id, user_id, wire)
        elif command == Command.TURN_OFF_BY_ROUTE:
            route_id = decode_id(body[2:18])
        elif command == Command.TURN_OFF_BY_USER:
            user_id = decode_id(body[2:18])
        elif command == Command.CHANGE_ROUTE_PARAMS:
            route_id = decode_id(body[2:18])
            color, duration = tuple(body[18:21]), int.from_bytes(body[21:23], "big")
        elif command == Command.TURN_ON_ALL:
            color, duration = tuple(body[2:5]), int.from_bytes(body[5:7], "big")
        elif command in (Command.SET_START_HOLDS, Command.SET_STEP_HOLDS,
                         Command.SET_FINISH_HOLDS):
            color, duration, count = tuple(body[2:5]), int.from_bytes(body[5:7], "big"), body[7]
            if count % 2 or len(body) != 8 + count:
                raise ValueError("invalid editor diode byte count")
            diodes = tuple(int.from_bytes(body[i:i + 2], "big")
                           for i in range(8, len(body), 2))
            key = (command, "", "", wire)
        replace = self._continuation != key
        limit = CHUNK_LIMITS.get(command)
        self._continuation = key if limit and len(diodes) == limit else None
        return QuantumAction(command, route_id, user_id, color, duration,
                             animation, diodes, replace,
                             wire is WireVersion.EWALLS_1_44, wire)

    def _feed_json(self, data: bytes) -> None:
        try:
            self._json_buffer += data.decode("utf-8")
        except UnicodeDecodeError as exc:
            self._json_buffer = ""
            self._event(False, "JSON_UTF8", detail=str(exc))
            return
        decoder = json.JSONDecoder()
        while self._json_buffer.strip():
            source = self._json_buffer.lstrip()
            try:
                value, end = decoder.raw_decode(source)
            except json.JSONDecodeError:
                return
            self._json_buffer = source[end:]
            if not isinstance(value, dict):
                self._event(False, "JSON_SHAPE", detail="expected object")
                continue
            try:
                action = self._decode_json(value)
            except (KeyError, TypeError, ValueError) as exc:
                self._event(False, "JSON_PAYLOAD", detail=str(exc))
                continue
            self.on_action(action)
            self._event(True, "LEGACY_ACK", action.command)

    @staticmethod
    def _decode_json(value: dict) -> QuantumAction:
        raw = value.get("command", value.get("cmd", Command.ACTIVATE_WALL))
        command = Command[raw.upper()] if isinstance(raw, str) else Command(int(raw))
        addresses = []
        for diode in value.get("diodes", []):
            if isinstance(diode, dict):
                diode = diode.get("autocadId", diode.get("idLedNode", diode.get("id")))
            addresses.append(int(diode, 0) if isinstance(diode, str) else int(diode))
        return QuantumAction(
            command, str(value.get("routeId", value.get("id", "")))[:16],
            str(value.get("userId", ""))[:16], tuple(_rgb(value.get("color", "#000000"))),
            int(value.get("duration", 0)), int(value.get("enableAnimation", 0)),
            tuple(addresses), legacy=True, wire=WireVersion.EWALLS_1_44)
