"""Quantum Board binary CRC16/MODBUS and legacy-JSON protocol.

The binary format is reconstructed from ewalls Android 1.44.  Firmware
notification semantics have not been captured; acknowledgements here are
explicit simulator diagnostics and must not be treated as wire-compatible
controller responses.
"""

from __future__ import annotations

import json
import logging
import struct
from dataclasses import dataclass
from enum import IntEnum
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


CHUNK_LIMITS = {
    Command.ACTIVATE_WALL: 92,
    Command.BOARD_SWIPE: 92,
    Command.ACTIVATE_WALL_LED_ID: 32,
    Command.SET_START_HOLDS: 120,
    Command.SET_STEP_HOLDS: 120,
    Command.SET_FINISH_HOLDS: 120,
}
FIXED_LENGTHS = {
    Command.TURN_OFF_BY_ROUTE: 21,
    Command.TURN_OFF_BY_USER: 21,
    Command.TURN_OFF_ALL: 8,
    Command.CHANGE_ROUTE_PARAMS: 25,
    Command.REQUEST_USER_ROUTE_LIST: 8,
    Command.TURN_ON_ALL: 9,
}


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def _id16(value: str) -> bytes:
    return value.encode("utf-8")[:16].ljust(16, b"\0")


def _decode_id(value: bytes) -> str:
    return value.rstrip(b"\0").decode("utf-8", errors="replace")


def _rgb(value: str | tuple[int, int, int]) -> bytes:
    encoded = bytes(value) if isinstance(value, tuple) else bytes.fromhex(
        value.removeprefix("#"))
    if len(encoded) != 3:
        raise ValueError("color must contain exactly three RGB bytes")
    return encoded


def encode(command: Command | int, *, route_id: str = "", user_id: str = "",
           color: str | tuple[int, int, int] = "#000000", duration: int = 0,
           enable_animation: int = 0, animation_speed: int = 1,
           diodes: Iterable[int] = (), internal_index: int = 1,
           count_of_routes: int = 61) -> bytes:
    """Encode one app-compatible frame (chunk long lists beforehand)."""
    cmd = Command(command)
    values = list(diodes)
    limit = CHUNK_LIMITS.get(cmd)
    if limit is not None and len(values) > limit:
        raise ValueError(f"{cmd.name} allows at most {limit} diodes per frame")
    head = bytes((1, cmd))
    if cmd in (Command.SET_START_HOLDS, Command.SET_STEP_HOLDS,
               Command.SET_FINISH_HOLDS):
        payload = _rgb(color) + struct.pack(">H", animation_speed)
        payload += bytes((2 * len(values),))
        payload += b"".join(struct.pack(">H", d) for d in values)
    elif cmd == Command.TURN_ON_ALL:
        payload = _rgb(color) + struct.pack(">H", duration)
    elif cmd == Command.ACTIVATE_WALL_LED_ID:
        payload = _id16(route_id) + _id16(user_id) + _rgb(color)
        payload += struct.pack(">HBB", duration, enable_animation, 4 * len(values))
        payload += b"".join(struct.pack(">I", d) for d in values)
    elif cmd == Command.REQUEST_USER_ROUTE_LIST:
        payload = struct.pack(">HH", internal_index, count_of_routes)
    elif cmd == Command.CHANGE_ROUTE_PARAMS:
        payload = _id16(route_id) + _rgb(color) + struct.pack(">H", duration)
    elif cmd == Command.TURN_OFF_ALL:
        payload = struct.pack(">HH", 1, 0)
    elif cmd == Command.TURN_OFF_BY_USER:
        payload = _id16(user_id) + b"\0"
    elif cmd == Command.TURN_OFF_BY_ROUTE:
        payload = _id16(route_id) + b"\0"
    else:
        payload = _id16(route_id) + _id16(user_id) + _rgb(color)
        payload += struct.pack(">HBB", duration, enable_animation, 2 * len(values))
        payload += b"".join(struct.pack(">H", d) for d in values)
    body = head + payload
    return body + struct.pack("<H", crc16_modbus(body))


def encode_chunks(command: Command | int, *, diodes: Iterable[int] = (),
                  **kwargs) -> list[bytes]:
    cmd = Command(command)
    values = list(diodes)
    limit = CHUNK_LIMITS.get(cmd)
    groups = [values] if limit is None else [
        values[i:i + limit] for i in range(0, len(values), limit)]
    if not groups:
        groups = [[]]
    return [encode(cmd, diodes=group, **kwargs) for group in groups]


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


@dataclass(frozen=True)
class ProtocolEvent:
    ok: bool
    code: str
    command: Command | None = None
    detail: str = ""


class QuantumProtocol:
    """Streaming frame reassembler with deterministic diagnostics.

    ``feed`` accepts arbitrary GATT fragmentation. A bad CRC is rejected
    without changing controller state, and scanning resumes at the next
    frame prefix. ``reset_transport`` models a disconnect/reconnect while
    leaving the caller-owned board state intact.
    """

    MAX_BUFFER = 4096

    def __init__(self, on_action: Callable[[QuantumAction], None],
                 on_event: Callable[[ProtocolEvent], None] | None = None,
                 reject_commands: set[Command] | None = None) -> None:
        self.on_action = on_action
        self.on_event = on_event
        self.reject_commands = reject_commands or set()
        self._buffer = bytearray()
        self._json_buffer = ""
        self._continuation: tuple[Command, str, str] | None = None

    def reset_transport(self) -> None:
        self._buffer.clear()
        self._json_buffer = ""
        self._continuation = None

    def _event(self, ok: bool, code: str, command: Command | None = None,
               detail: str = "") -> None:
        event = ProtocolEvent(ok, code, command, detail)
        if self.on_event:
            self.on_event(event)
        if ok:
            logger.debug("Quantum ACK %s", command.name if command else code)
        else:
            logger.warning("Quantum error %s: %s", code, detail)

    def feed(self, data: bytes | bytearray) -> None:
        if not data:
            return
        # Legacy writes are JSON objects; fragmented JSON remains parseable
        # through the dedicated text accumulator.
        if self._json_buffer or bytes(data).lstrip().startswith((b"{", b"[")):
            self._feed_json(bytes(data))
            return
        self._buffer.extend(data)
        if len(self._buffer) > self.MAX_BUFFER:
            self._buffer.clear()
            self._event(False, "OVERFLOW", detail="binary buffer limit exceeded")
            return
        while self._buffer:
            if self._buffer[0] != 1:
                del self._buffer[0]
                self._event(False, "PREFIX", detail="discarded byte before frame")
                continue
            if len(self._buffer) < 2:
                return
            try:
                command = Command(self._buffer[1])
            except ValueError:
                bad = self._buffer[1]
                del self._buffer[:2]
                self._event(False, "COMMAND", detail=f"unknown 0x{bad:02x}")
                continue
            length = self._frame_length(command)
            if length is None or len(self._buffer) < length:
                return
            frame = bytes(self._buffer[:length])
            del self._buffer[:length]
            expected = crc16_modbus(frame[:-2])
            actual = int.from_bytes(frame[-2:], "little")
            if actual != expected:
                self._continuation = None
                self._event(False, "CRC", command,
                            f"expected {expected:04x}, got {actual:04x}")
                continue
            if command in self.reject_commands:
                self._event(False, "REJECTED", command, "fault profile")
                continue
            try:
                action = self._decode(frame, command)
            except (ValueError, struct.error) as exc:
                self._continuation = None
                self._event(False, "PAYLOAD", command, str(exc))
                continue
            self.on_action(action)
            self._event(True, "ACK", command)

    def _frame_length(self, command: Command) -> int | None:
        fixed = FIXED_LENGTHS.get(command)
        if fixed is not None:
            return fixed
        if command in (Command.ACTIVATE_WALL, Command.BOARD_SWIPE,
                       Command.ACTIVATE_WALL_LED_ID):
            return 43 + self._buffer[40] if len(self._buffer) > 40 else None
        if command in (Command.SET_START_HOLDS, Command.SET_STEP_HOLDS,
                       Command.SET_FINISH_HOLDS):
            return 10 + self._buffer[7] if len(self._buffer) > 7 else None
        return None

    def _decode(self, frame: bytes, command: Command) -> QuantumAction:
        body = frame[:-2]
        route_id = user_id = ""
        color = (0, 0, 0)
        duration = animation = 0
        diodes: tuple[int, ...] = ()
        key = (command, "", "")
        if command in (Command.ACTIVATE_WALL, Command.BOARD_SWIPE,
                       Command.ACTIVATE_WALL_LED_ID):
            route_id = _decode_id(body[2:18])
            user_id = _decode_id(body[18:34])
            color = tuple(body[34:37])  # type: ignore[assignment]
            duration = int.from_bytes(body[37:39], "big")
            animation = body[39]
            count = body[40]
            width = 4 if command == Command.ACTIVATE_WALL_LED_ID else 2
            if count % width or len(body) != 41 + count:
                raise ValueError("invalid diode byte count")
            diodes = tuple(int.from_bytes(body[i:i + width], "big")
                           for i in range(41, len(body), width))
            key = (command, route_id, user_id)
        elif command == Command.TURN_OFF_BY_ROUTE:
            route_id = _decode_id(body[2:18])
        elif command == Command.TURN_OFF_BY_USER:
            user_id = _decode_id(body[2:18])
        elif command == Command.CHANGE_ROUTE_PARAMS:
            route_id = _decode_id(body[2:18])
            color = tuple(body[18:21])  # type: ignore[assignment]
            duration = int.from_bytes(body[21:23], "big")
        elif command == Command.TURN_ON_ALL:
            color = tuple(body[2:5])  # type: ignore[assignment]
            duration = int.from_bytes(body[5:7], "big")
        elif command in (Command.SET_START_HOLDS, Command.SET_STEP_HOLDS,
                         Command.SET_FINISH_HOLDS):
            color = tuple(body[2:5])  # type: ignore[assignment]
            duration = int.from_bytes(body[5:7], "big")
            count = body[7]
            if count % 2 or len(body) != 8 + count:
                raise ValueError("invalid editor diode byte count")
            diodes = tuple(int.from_bytes(body[i:i + 2], "big")
                           for i in range(8, len(body), 2))
            key = (command, "", "")
        replace = self._continuation != key
        limit = CHUNK_LIMITS.get(command)
        self._continuation = key if limit and len(diodes) == limit else None
        return QuantumAction(command, route_id, user_id, color, duration,
                             animation, diodes, replace)

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
        if isinstance(raw, str):
            command = Command[raw.upper()]
        else:
            command = Command(int(raw))
        raw_color = value.get("color", "#000000")
        color = tuple(_rgb(raw_color))  # type: ignore[arg-type]
        addresses = []
        for diode in value.get("diodes", []):
            if isinstance(diode, dict):
                diode = diode.get("autocadId", diode.get("idLedNode", diode.get("id")))
            addresses.append(int(diode, 0) if isinstance(diode, str) else int(diode))
        return QuantumAction(
            command=command,
            route_id=str(value.get("routeId", value.get("id", "")))[:16],
            user_id=str(value.get("userId", ""))[:16],
            color=color, duration=int(value.get("duration", 0)),
            animation=int(value.get("enableAnimation", 0)),
            diodes=tuple(addresses), legacy=True,
        )
