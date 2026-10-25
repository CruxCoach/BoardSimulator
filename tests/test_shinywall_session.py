"""Shiny Wall session, GATT shape, advertising, geometry and CLI wiring.

No adapter needed: writes go through the real GattCharacteristic.WriteValue
(as BlueZ would call it) so the write context (type, MTU, peer) reaches the
session the way it does live.
"""

import json
import logging
import os
import subprocess
import sys

import pytest
from dbus_fast import Variant

import config
from ble import advertising
from ble.gatt import GattCharacteristic, WriteInfo, build_application, current_write
from boards import board_for
from protocols.session import create_session
from protocols.shinywall_session import ShinyWallSession
from protocols.shinywall import (CODE_FOOT, CODE_HAND, CODE_START, CODE_TOP,
                                 ShinyWallOptions, encode_vector, route_writes,
                                 state_vector)
from render.shinywall_headless import render_holds_text
from shinywall_geometry import GEOMETRY_FORMAT, ShinyWallGeometry

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEER = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
ROUTE = {0: CODE_START, 12: CODE_HAND, 13: CODE_FOOT, 310: CODE_TOP}


def make(layout: str = "st1", **options) -> ShinyWallSession:
    board = board_for("shinywall")
    shiny = ShinyWallOptions(**options) if options else None
    return create_session(board, board.variant_for(layout), None, None, None,
                          shiny=shiny)


def wire(session: ShinyWallSession) -> GattCharacteristic:
    """The session's write characteristic, as BlueZ sees it."""
    app = build_application(session.gatt_profile, lambda data, _peer: session.feed(data))
    (char,) = app.services[0].characteristics
    return char


def write(char: GattCharacteristic, data: bytes, kind: str = "request",
          mtu: int | None = 517, peer: str = PEER) -> None:
    options = {"device": Variant("o", peer), "type": Variant("s", kind)}
    if mtu:
        options["mtu"] = Variant("q", mtu)
    char.WriteValue(data, options)


class TestProfileAndIdentity:
    def test_default_profile(self) -> None:
        session = make()
        profile = session.gatt_profile
        assert profile.advertised_uuid == config.SHINYWALL_SERVICE_UUID
        (service,) = profile.services
        (char,) = service.characteristics
        assert service.uuid == config.SHINYWALL_SERVICE_UUID
        assert char.uuid == config.SHINYWALL_WRITE_UUID and char.receives_writes
        assert char.flags == ("read", "write", "write-without-response", "notify")

    def test_custom_service_and_no_advertised_uuid(self) -> None:
        uuid = "12345678-1234-5678-1234-56789abcdef0"
        session = make(service_uuid=uuid.upper(), advertise_uuid=False,
                       props=("write",))
        assert session.gatt_profile.advertised_uuid == ""
        assert session.gatt_profile.services[0].uuid == uuid
        assert session.gatt_profile.services[0].characteristics[0].flags == ("write",)

    @pytest.mark.parametrize("layout, name", [
        ("st1", "ShinyWall_STP_0001"), ("st4", "ShinyWall_STF_0001"),
        ("st2", "ShinyWall_STM_0001"), ("st5", "ShinyWall_STMF_0001"),
        ("st0", "Shiny Wall ST0 (ShinyMini)"),
    ])
    def test_default_names(self, layout, name) -> None:
        assert make(layout).ble_name == name

    def test_exact_name_override_is_independent_of_variant(self) -> None:
        assert make("st5", name="ShinyWall_STP_ABC123").ble_name == "ShinyWall_STP_ABC123"

    def test_too_long_name_fails_loudly(self) -> None:
        with pytest.raises(ValueError, match="at most 29"):
            make(name="ShinyWall_STMF_" + "0" * 15)

    def test_hold_count_override(self) -> None:
        session = make("st2", hold_count=476)
        assert session.hold_count == 476
        assert session.window_title.endswith("(476 holds)")

    def test_other_families_reject_shiny_options(self) -> None:
        board = board_for("kilter")
        with pytest.raises(ValueError, match="only apply to the Shiny Wall"):
            create_session(board, board.default_variant, None, None, None,
                           shiny=ShinyWallOptions())

    @pytest.mark.parametrize("kwargs", [
        dict(size_id=10), dict(api_level=3), dict(serial="1"),
        dict(controller="aurora"), dict(strict_pacing=True)])
    def test_shiny_rejects_aurora_options(self, kwargs) -> None:
        board = board_for("shinywall")
        args = dict(size_id=None, api_level=None, serial=None)
        args.update({k: v for k, v in kwargs.items() if k in args})
        with pytest.raises(ValueError, match="not the Shiny Wall"):
            create_session(board, board.default_variant, args["size_id"],
                           args["api_level"], args["serial"],
                           kwargs.get("controller"),
                           kwargs.get("strict_pacing", False))


class TestWritePath:
    def test_route_through_gatt_lights_the_wall(self, caplog) -> None:
        session = make()
        char = wire(session)
        caplog.set_level(logging.INFO)
        for chunk in route_writes(ROUTE, 311, mtu=185):
            write(char, chunk, mtu=185)
        assert session.state.get_holds() == ROUTE
        text = caplog.text
        assert "RX 1 B (#1, request, mtu 185) from AA:BB:CC:DD:EE:FF: '-'" in text
        assert "CLEAR '-': all holds off" in text
        assert "STATE 311 codes applied" in text
        assert "from 2 write(s) (requestx2), sizes [180, 133]" in text
        assert "start 1 [0], hand 1 [12], foot 1 [13], top 1 [310]" in text

    def test_write_context_is_only_set_during_the_callback(self) -> None:
        seen: list = []
        char = GattCharacteristic(0, "uuid", ["write"], "/service")
        char.write_callback = lambda _data, _peer: seen.append(current_write())
        write(char, b"-", kind="command", mtu=247)
        assert seen == [WriteInfo("command", 247, 0, PEER)]
        assert seen[0].address == "AA:BB:CC:DD:EE:FF"
        assert current_write() is None

    def test_mixed_write_types_are_counted(self, caplog) -> None:
        session = make()
        char = wire(session)
        caplog.set_level(logging.INFO)
        message = encode_vector(state_vector(ROUTE, 311))
        write(char, message[:300], kind="request")
        write(char, message[300:], kind="command")
        assert session.state.get_holds() == ROUTE
        assert "(requestx1, commandx1)" in caplog.text

    def test_non_ascii_write_is_logged_as_hex(self, caplog) -> None:
        session = make()
        caplog.set_level(logging.INFO)
        write(wire(session), b"\x01\x02-")
        assert "hex 01022d" in caplog.text
        assert "GARBAGE 2 B" in caplog.text

    def test_partial_messages_are_kept_per_central(self) -> None:
        session = make()
        char = wire(session)
        message = encode_vector(state_vector(ROUTE, 311))
        other = "/org/bluez/hci0/dev_11_22_33_44_55_66"
        write(char, message[:200], peer=PEER)
        write(char, b"-", peer=other)          # other central clears
        write(char, message[200:], peer=PEER)  # first one finishes its route
        assert session.state.get_holds() == ROUTE

    def test_mtu_limit_drops_long_writes(self, caplog) -> None:
        session = make(mtu=23)
        char = wire(session)
        caplog.set_level(logging.INFO)
        write(char, b"-" * 20, mtu=517)  # MTU 23 carries 20 B: accepted
        assert session.dropped == 0
        write(char, b"-" * 21, mtu=517)
        assert session.dropped == 1
        assert "DROP: 21-byte write exceeds the simulated ATT MTU 23" in caplog.text
        assert "ExchangeMTU = 23" in caplog.text
        for chunk in route_writes(ROUTE, 311, mtu=23):
            write(char, chunk, mtu=23)
        assert session.state.get_holds() == ROUTE
        assert session.dropped == 1

    def test_processing_delay_sleeps_per_write(self) -> None:
        sleeps: list[float] = []
        board = board_for("shinywall")
        session = ShinyWallSession(board, board.default_variant,
                                   ShinyWallOptions(delay_ms=50),
                                   sleep=sleeps.append)
        for chunk in route_writes(ROUTE, 311, mtu=517):
            session.feed(chunk)
        assert sleeps == [0.05, 0.05]  # '-' + the 313-byte vector
        assert session.state.get_holds() == ROUTE

    def test_gap_between_writes_is_logged(self, caplog) -> None:
        times = iter([10.0, 10.031])
        board = board_for("shinywall")
        session = ShinyWallSession(board, board.default_variant,
                                   clock=lambda: next(times))
        caplog.set_level(logging.INFO)
        session.feed(b"-")
        session.feed(b"-")
        assert "(#2, type ?, +31 ms)" in caplog.text

    def test_strict_length_rejects_wrong_variant(self, caplog) -> None:
        session = make("st2", length_mode="strict")
        caplog.set_level(logging.INFO)
        for chunk in route_writes(ROUTE, 311):
            session.feed(chunk)
        assert session.state.get_holds() == {}
        assert "LENGTH vector has 311 codes, this wall has 475 holds" in caplog.text
        assert "REJECT strict length" in caplog.text

    def test_session_logs_before_renderers_see_the_state(self, caplog) -> None:
        session = make()
        order: list[str] = []
        session.state.register_callback(lambda _state: order.append("render"))
        caplog.set_level(logging.INFO)
        logger = logging.getLogger("protocols.shinywall_session")
        handler = logging.Handler()
        handler.emit = lambda record: order.append(record.getMessage().split()[0])
        logger.addHandler(handler)
        try:
            session.feed(encode_vector(state_vector(ROUTE, 311)))
        finally:
            logger.removeHandler(handler)
        assert order == ["RX", "STATE", "render"]


class TestAck:
    def test_off_by_default(self) -> None:
        session = make()
        assert all(session.feed(chunk) is None
                   for chunk in route_writes(ROUTE, 311, mtu=517))

    def test_ok_after_each_understood_message(self) -> None:
        from ble.gatt import GattUpdate

        session = make(ack=True)
        replies = []
        for chunk in route_writes(ROUTE, 311, mtu=23) + [b"f2.", b"Z.", b"+2."]:
            replies.append(session.feed(chunk))
        ok = GattUpdate(notification=b"OK", notify_uuid=config.SHINYWALL_WRITE_UUID)
        assert replies[0] == [ok]                       # '-'
        assert all(r is None for r in replies[1:-4])    # vector chunks
        assert replies[-4] == [ok]                      # last chunk: vector done
        assert replies[-3] == [ok]                      # f2.
        assert replies[-2] is None                      # unknown command
        assert replies[-1] == [ok]                      # short vector (lenient)

    def test_ok_reaches_a_subscribed_central_through_gatt(self) -> None:
        session = make(ack=True)
        app = build_application(session.gatt_profile, lambda data, _peer: None)
        (char,) = app.services[0].characteristics
        char.StartNotify()
        for reply in session.feed(b"-"):
            app.publish(reply)
        assert bytes(char.Value) == b"OK"

    def test_separate_notify_characteristic(self) -> None:
        uuid = "12345678-0000-1000-8000-00805f9b34fb"
        session = make(ack=True, notify_uuid=uuid)
        write_char, notify_char = session.gatt_profile.services[0].characteristics
        assert notify_char.uuid == uuid and notify_char.flags == ("notify",)
        assert not notify_char.receives_writes
        app = build_application(session.gatt_profile, lambda data, _peer: None)
        notify = app.services[0].characteristics[1]
        notify.StartNotify()
        for reply in session.feed(b"-"):
            app.publish(reply)
        assert bytes(notify.Value) == b"OK"
        assert bytes(app.services[0].characteristics[0].Value) == b""


class TestDisconnect:
    def test_keep_leaves_leds_on_and_reports_partial(self, caplog) -> None:
        session = make()
        caplog.set_level(logging.INFO)
        session.feed(encode_vector(state_vector(ROUTE, 311)))
        session.feed(b"+2040")
        session.connection_lost()
        assert session.state.get_holds() == ROUTE
        assert "ABORT link ended inside a state vector" in caplog.text
        assert "4 hold(s) stay lit" in caplog.text
        # A fresh link starts with a clean parser: the tail is a new message.
        session.feed(b"040.")
        assert session.state.get_holds() == ROUTE
        assert "COMMAND unknown command '0'" in caplog.text

    def test_clear_turns_leds_off(self) -> None:
        session = make(on_disconnect="clear")
        session.feed(encode_vector(state_vector(ROUTE, 311)))
        session.connection_lost()
        assert session.state.get_holds() == {}


class TestAdvertisingData:
    def test_uuid_layout_unchanged(self) -> None:
        data = advertising.build_adv_data(config.SHINYWALL_SERVICE_UUID, "x")
        assert data[:5] == bytes([0x02, 0x01, 0x06, 0x11, 0x07])
        assert len(data) == 21
        assert data[5:] == bytes.fromhex(
            config.SHINYWALL_SERVICE_UUID.replace("-", ""))[::-1]

    def test_no_uuid_carries_flags_and_name(self) -> None:
        data = advertising.build_adv_data("", "ShinyWall_STP_0001")
        assert data == bytes([2, 1, 6, 19, 9]) + b"ShinyWall_STP_0001"

    def test_no_uuid_long_name_only_flags(self) -> None:
        assert advertising.build_adv_data(None, "x" * 29) == bytes([2, 1, 6])

    def test_scan_response_for_29_byte_name_fits(self) -> None:
        assert len(advertising.build_scan_response("x" * 29)) == 31

    def test_extended_setup_sends_flags_only_without_uuid(self, monkeypatch) -> None:
        sent: list[tuple[str, list[str]]] = []
        monkeypatch.setattr(advertising, "_hcitool_cmd",
                            lambda label, opcode, params, adapter="hci0":
                            sent.append((label, params)) or True)
        assert advertising.set_extended_adv_data("", "Shiny Wall ST0 (ShinyMini)")
        adv = dict(sent)["ext adv data"]
        payload = bytes(int(octet, 16) for octet in adv[4:])
        assert int(adv[3], 16) == len(payload)
        assert payload[:3] == bytes([2, 1, 6]) and b"ShinyMini" in payload


class TestGeometry:
    def test_placeholder_is_index_order_on_the_grid(self) -> None:
        variant = board_for("shinywall").variant_for("st1")
        geometry = ShinyWallGeometry(variant)
        assert geometry.is_placeholder and "placeholder" in geometry.source
        assert len(geometry.holds) == 311 and geometry.unplaced == 0
        assert (geometry.hold(13).x, geometry.hold(13).y) == (1.0, 1.0)
        u, v = geometry.to_unit(geometry.hold(0))
        assert u < 0.1 and v > 0.9  # bottom left

    def test_geometry_file_is_used(self, tmp_path) -> None:
        path = tmp_path / "geometry.json"
        path.write_text(json.dumps({
            "format": GEOMETRY_FORMAT,
            "variants": {"st1": {"hold_count": 3, "confidence": "low",
                                 "grid": {"columns": 12, "rows": 35},
                                 "holds": [
                                     {"index": 0, "name": "a1", "col": 0, "row": 0, "x": 0.0, "y": 0.0},
                                     {"index": 1, "name": "a2", "col": 0, "row": 1, "x": 0.5, "y": 1.0},
                                     {"index": 2, "name": None, "col": 1, "row": 0, "x": 1.0, "y": 0.0}]}}}))
        variant = board_for("shinywall").variant_for("st1")
        geometry = ShinyWallGeometry(variant, path=str(path))
        assert geometry.hold_count == 3 and not geometry.is_placeholder
        assert "confidence low" in geometry.source
        assert geometry.hold(1).name == "a2"
        # --shiny-holds wins over the file.
        assert ShinyWallGeometry(variant, 5, str(path)).unplaced == 2
        session = make(geometry_path=str(path))
        assert session.hold_count == 3

    def test_geometry_file_errors_are_loud(self, tmp_path) -> None:
        variant = board_for("shinywall").variant_for("st2")
        path = tmp_path / "g.json"
        path.write_text(json.dumps({"format": GEOMETRY_FORMAT, "variants": {}}))
        with pytest.raises(ValueError, match="no variant 'st2'"):
            ShinyWallGeometry(variant, path=str(path))
        path.write_text(json.dumps({"format": "other"}))
        with pytest.raises(ValueError, match="not a"):
            ShinyWallGeometry(variant, path=str(path))

    def test_headless_grid(self) -> None:
        variant = board_for("shinywall").variant_for("st1")
        text = render_holds_text(ShinyWallGeometry(variant), {**ROUTE, 30: 5})
        lines = text.splitlines()
        assert len(lines) == 26  # ceil(311 / 12)
        assert lines[-1] == "S..........."
        assert lines[-2] == "HF.........."
        assert lines[-3][6] == "?"
        assert lines[0] == "..........T"


def run_main(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, os.path.join(REPO_ROOT, "main.py"), *args],
                          capture_output=True, text=True, timeout=60, cwd=REPO_ROOT)


class TestCli:
    def test_list_shows_variants_and_names(self) -> None:
        out = run_main("--list").stdout
        assert "shinywall: 'Shiny Wall' [shinywall protocol]" in out
        assert "--layout st1 (default): Standard [12x35 grid, 311 holds" in out
        assert "'ShinyWall_STMF_0001'" in out
        assert "'Shiny Wall ST0 (ShinyMini)'" in out

    def test_too_long_name_exits_2(self) -> None:
        result = run_main("--board", "shinywall", "--name", "x" * 30, "--headless")
        assert result.returncode == 2
        assert "at most 29" in result.stderr

    def test_shiny_flag_on_other_board_exits_2(self) -> None:
        result = run_main("--board", "moonboard", "--shiny-length", "strict",
                          "--headless")
        assert result.returncode == 2
        assert "--shiny-length only applies to the Shiny Wall" in result.stderr

    def test_aurora_flag_on_shiny_exits_2(self) -> None:
        result = run_main("--board", "shinywall", "--size", "7", "--headless")
        assert result.returncode == 2
        assert "not the Shiny Wall" in result.stderr

    def test_bad_props_exit_2(self) -> None:
        result = run_main("--board", "shinywall", "--shiny-props", "read",
                          "--headless")
        assert result.returncode == 2

    def test_options_parse_into_shiny_options(self, monkeypatch) -> None:
        import main

        monkeypatch.setattr(sys, "argv", [
            "main.py", "--board", "shinywall", "--name", "ShinyWall_STP_ABC123",
            "--no-advertised-uuid", "--shiny-holds", "312", "--shiny-length",
            "strict", "--shiny-codes", "accept", "--shiny-mtu", "185",
            "--shiny-delay", "20", "--shiny-on-disconnect", "clear",
            "--shiny-props", "write,notify", "--shiny-ack",
            "--shiny-notify-uuid", "12345678-0000-1000-8000-00805f9b34fb"])
        options, flags = main._shiny_options(main._parse_args())
        assert options == ShinyWallOptions(
            name="ShinyWall_STP_ABC123", advertise_uuid=False, hold_count=312,
            length_mode="strict", codes_mode="accept", mtu=185, delay_ms=20.0,
            on_disconnect="clear", props=("write", "notify"), ack=True,
            notify_uuid="12345678-0000-1000-8000-00805f9b34fb")
        assert "--no-advertised-uuid" in flags and "--shiny-mtu" in flags

    def test_second_slot_gets_its_own_name(self) -> None:
        import main

        options = ShinyWallOptions(name="ShinyWall_STP_ABC123")
        second = main._slot_shiny(options, 1)
        assert second.name is None and second.identity == "0002"
        assert main._slot_shiny(None, 0) is None

    def test_att_log_defaults_to_shiny(self) -> None:
        import main

        shiny, kilter = make(), create_session(
            board_for("kilter"), board_for("kilter").default_variant,
            None, None, None)
        assert main._att_log_wanted(None, [shiny])
        assert not main._att_log_wanted(None, [kilter])
        assert main._att_log_wanted(True, [kilter])
        assert not main._att_log_wanted(False, [shiny])


class TestTwoBoards:
    def test_ok_reaches_only_the_shiny_slot(self) -> None:
        from ble.hci_monitor import AdvertisingConnection
        from ble.multiplex import MultiplexedBLEPeripheral, VirtualBoard

        shiny = make(ack=True)
        kilter = create_session(board_for("kilter"),
                                board_for("kilter").default_variant,
                                None, None, None)
        mux = MultiplexedBLEPeripheral([
            VirtualBoard("shiny", shiny.ble_name, shiny.gatt_profile, shiny.feed),
            VirtualBoard("kilter", kilter.ble_name, kilter.gatt_profile, kilter.feed),
        ])
        app = build_application(mux._profile, mux._handle_gatt_write)
        mux._app = app
        chars = {char._uuid: char for service in app.services
                 for char in service.characteristics}
        chars[config.SHINYWALL_WRITE_UUID].StartNotify()
        mux._handle_hci_event(AdvertisingConnection(1, 0x40, "AA:00:00:00:00:01"))
        mux._handle_gatt_write(b"-", "/org/bluez/hci0/dev_AA_00_00_00_00_01")
        assert bytes(chars[config.SHINYWALL_WRITE_UUID].Value) == b"OK"
        assert bytes(chars[config.RX_CHARACTERISTIC_UUID].Value) == b""
