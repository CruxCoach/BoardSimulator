"""Generate independent mobile conformance traces using the production Python core.

Each trace contains BLE writes, emitted complete states and GATT replies. Tests
replay each trace with original, bytewise and merged write boundaries. Values
come from the original encoders/decoders, not the mobile implementation.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ble.gatt import GattUpdate
from boards import BOARDS
from protocols.aurora_encoder import encode_climb_v2, encode_climb_v3, hex_to_color_byte
from protocols.quantum import Command, WireVersion, encode, encode_chunks
from protocols.session import create_session


def normalize(session, state: dict) -> list:
    if session.board.protocol == "quantum":
        return sorted([p, v.r, v.g, v.b] for p, v in state.items())
    if session.board.protocol == "moonboard":
        return sorted([row * 11 + col + 1, *v[1:]] for (col, row), v in state.items())
    return sorted([p, *v] for p, v in state.items())


def trace(item: dict, writes: list[bytes | None], api: int = 3) -> dict:
    board = BOARDS[item["board"]]
    session = create_session(board, board.variant_for(item["layout"]), item["size"],
                             api if item["family"] == "aurora" else None, None)
    states, replies, moon_details = [], [], []
    session.state.register_callback(lambda state: states.append(normalize(session, state)))
    for write in writes:
        if write is None:
            # Native mobile resets link transport for all families; Linux exposes
            # the session hook for Quantum only. Construct equivalent reset here.
            if item["family"] == "quantum": session.connection_lost()
            elif item["family"] == "moonboard": session._decoder.reset()
            else:
                from protocols.aurora_decoder import ProtocolDecoder
                session._decoder = ProtocolDecoder(api, session.state.update)
            continue
        old_count = len(states)
        for update in session.feed(write) or []:
            if isinstance(update, GattUpdate):
                replies.append(dict(notify=list(update.notification) if update.notification is not None else None,
                                    state=list(update.read_value)))
            else:
                replies.append(dict(notify=list(update)))
        if item["family"] == "moonboard" and len(states) > old_count:
            problem = session._decoder.decoded_problem
            moon_details.append([list(x) for x in problem.led_commands])
    return dict(id=item["id"], family=item["family"], api=api,
                rows=item.get("rows"), addresses=item.get("addresses", []),
                writes=[list(w) if w is not None else None for w in writes],
                states=states, replies=replies, moonLEDs=moon_details)


def build(catalog: list[dict]) -> list[dict]:
    traces = []
    for item in catalog:
        family = item["family"]
        if family == "aurora":
            for api in (2, 3):
                encode_climb = encode_climb_v2 if api == 2 else encode_climb_v3
                # Exercise actual board-local palettes and physical positions.
                positions = [int(p) for p in item["points"] if api == 3 or int(p) <= 1023]
                colors = [hex_to_color_byte(r["led_color"]) for r in item["roles"]]
                holds = [(p, colors[i % len(colors)]) for i, p in enumerate(positions[:280])]
                frames = encode_climb(holds)
                bad = bytearray(b"".join(encode_climb(holds[:3]))); bad[2] ^= 0x80
                writes = frames + [bytes(bad)] + encode_climb(holds[:4]) + encode_climb([])
                traces.append(trace(item, writes, api))
        elif family == "moonboard":
            n = 11 * item["rows"] - 1
            writes = [b"ignored", b"l#S0,P1,", f"E{n},F17,L18,M35,R36#".encode(),
                      b"~D#l#S1,P18,E35#", b"l#X1,P999,S-1,P3junk#", b"l##",
                      b"l#S", None, b"l#P2#"]
            traces.append(trace(item, writes))
        else:
            ids = item["addresses"]
            route = "00112233-4455-6677-8899-aabbccddeeff"
            other = "ffeeddcc-bbaa-9988-7766-554433221100"
            user = "10213243-5465-7687-98a9-bacbdcedfe0f"
            kw = dict(route_id=route, user_id=user, color="#12abef", duration=65535)
            writes = encode_chunks(Command.ACTIVATE_WALL, diodes=ids[:110], **kw)
            writes += [encode(Command.REQUEST_USER_ROUTE_LIST),
                       encode(Command.ACTIVATE_WALL, diodes=ids[-3:], **dict(kw, route_id=other)),
                       encode(Command.BOARD_SWIPE, diodes=ids[:3], **kw),
                       encode(Command.TURN_OFF_BY_ROUTE, route_id=route),
                       encode(Command.TURN_ON_ALL, color="#123456"),
                       encode(Command.TURN_OFF_ALL)]
            bad = bytearray(encode(Command.ACTIVATE_WALL, diodes=ids[:2], **kw)); bad[-1] ^= 1
            writes += [bytes(bad), encode(Command.ACTIVATE_WALL, diodes=ids[:4], **kw),
                       encode(Command.TURN_OFF_BY_USER, user_id=user),
                       encode(Command.ACTIVATE_WALL, diodes=ids[:2], **kw)[:9], None,
                       encode(Command.REQUEST_USER_ROUTE_LIST)]
            for cmd in Command:
                writes.append(encode(cmd, wire=WireVersion.EWALLS_1_44,
                                     route_id="legacy", user_id="user", color="#00ff00", diodes=ids[:3]))
            addresses32 = [int(p) for p in item["points"] if int(p) > 65535]
            writes.append(encode(Command.ACTIVATE_WALL_LED_ID,
                                 wire=WireVersion.EWALLS_1_44, route_id="u32", user_id="user",
                                 color="#ff00ff", diodes=addresses32[:3]))
            writes += [b'{"command":"ACTIVATE_WALL","routeId":"json","diodes":[',
                       str(ids[0]).encode()+b'],"color":"#ff0033"}',
                       b'{"cmd":"TURN_OFF_ALL"}{"cmd":"TURN_ON_ALL","color":"#123456"}']
            traces.append(trace(item, writes))
    return traces


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    destination = ROOT / "mobile/shared/generated"
    catalog = json.loads((destination / "catalog.json").read_text())
    traces = build(catalog)
    (ROOT / "mobile/tests/generated-fixtures.json").write_text(json.dumps(traces, separators=(",", ":")) + "\n")
    print(f"Generated {len(traces)} Python reference traces for {len(catalog)} selectable configurations")
