"""Entry point for the consolidated CruxCoach board simulator.

Simulates one board by default, or two virtual boards multiplexed on the
same controller. Pick board, layout and (for Aurora-protocol boards)
product size on the command line. The BLE peripheral runs in a background
thread; the GUI stays in the main thread, while headless mode logs decoded
climbs as ASCII grids.

Usage:
    sudo venv/bin/python main.py --board kilter
    sudo venv/bin/python main.py --board tension --layout tb2
    sudo venv/bin/python main.py --board moonboard --layout mini-2020
    sudo venv/bin/python main.py --board soill --size 1 --headless
    python main.py --list        # show boards, layouts and sizes (no BLE)

The default is one board.  ``--instances 2`` (or the GUI's Simulation
selector) exposes two simultaneous hardware advertising identities on one
capable adapter.
"""

import argparse
import logging
import signal
import sys
import threading

import config
from ble.adapter import DEFAULT_ADAPTER, InvalidAdapterError, normalize_adapter
from boards import (BOARDS, PROTOCOL_AURORA, PROTOCOL_MOONBOARD,
                    PROTOCOL_QUANTUM, board_for)
from selection import Selection

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def print_board_list() -> None:
    """Print every board with its layouts, sizes and protocol family."""
    from board_geometry import list_sizes

    for board in BOARDS.values():
        print(f"{board.key}: {board.display_name!r} [{board.protocol} protocol]")
        for variant in board.variants:
            default = " (default)" if variant is board.default_variant else ""
            if board.protocol == PROTOCOL_AURORA:
                print(f"  --layout {variant.key}{default}: {variant.display_name} "
                      f"[layout_id={variant.layout_id}, "
                      f"product_id={variant.product_id}]")
                for size in list_sizes(board, variant.product_id):
                    sdefault = " (default)" if size.id == variant.default_size_id else ""
                    print(f"    --size {size.id}{sdefault}: {size.name} "
                          f"[edges l={size.edge_left} r={size.edge_right} "
                          f"b={size.edge_bottom} t={size.edge_top}]")
            elif board.protocol == PROTOCOL_MOONBOARD:
                print(f"  --layout {variant.key}{default}: {variant.display_name} "
                      f"[11x{variant.grid_rows} grid, no --size]")
            else:
                subset = f", {variant.diode_kind} diodes" if variant.diode_kind else ""
                print(f"  --layout {variant.key}{default}: {variant.display_name} "
                      f"[{variant.columns}x{variant.rows} schematic{subset}, no --size]")
        print()


def _adapter_arg(value: str) -> str:
    """argparse type for --adapter: canonical name or a CLI error (exit 2).

    Rejecting the name here — before the BlueZ preflight — keeps a typo a
    plain usage error instead of a confusing "no Bluetooth" failure.
    """
    try:
        return normalize_adapter(value)
    except InvalidAdapterError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Software BLE simulator for CruxCoach boards: Aurora, "
                    "MoonBoard and Quantum protocol families.",
    )
    parser.add_argument(
        "--board", default="kilter", choices=list(BOARDS),
        help="Which board to simulate (default: %(default)s).",
    )
    parser.add_argument(
        "--layout", default=None,
        help="Board layout/variant key (e.g. kilter: original | homewall; "
             "tension: tb1 | tb2 | tb2-spray; moonboard: 2016 | "
             "masters-2017 | masters-2019 | mini-2020; quantum: "
             "xl | l | m | s | belay). "
             "Default: the board's first variant.",
    )
    parser.add_argument(
        "--size", type=int, default=None,
        help="Aurora product_size_id (see --list); Aurora-protocol boards "
             "only. Default: the layout's default size.",
    )
    parser.add_argument(
        "--api-level", type=int, default=None, choices=(2, 3),
        help="Aurora protocol API level advertised as the '@N' name suffix "
             f"(default: {config.API_LEVEL}); Aurora-protocol boards only.",
    )
    parser.add_argument(
        "--serial", default=None,
        help="Board serial advertised as the '#serial' name suffix "
             f"(default: {config.BOARD_SERIAL}); Aurora-protocol boards only.",
    )
    parser.add_argument(
        "--instances", type=int, choices=(1, 2), default=1,
        help="Virtual boards on this adapter; two use simultaneous hardware "
             "advertising (default: %(default)s).",
    )
    parser.add_argument(
        "--second-board", choices=list(BOARDS), default=None,
        help="Initial board in slot 2 (default: same as --board).",
    )
    parser.add_argument(
        "--second-layout", default=None,
        help="Initial layout in slot 2 (default: its board default, or the "
             "same layout when --second-board is omitted).",
    )
    parser.add_argument(
        "--second-size", type=int, default=None,
        help="Initial Aurora product_size_id in slot 2.",
    )
    parser.add_argument(
        "--second-serial", default=None,
        help="Aurora serial for slot 2 (default: slot-1 serial incremented).",
    )
    parser.add_argument(
        "--adapter", type=_adapter_arg, default=DEFAULT_ADAPTER,
        help="Bluetooth controller to drive, e.g. hci0, hci1 "
             "(default: %(default)s; see 'hciconfig'). Two virtual boards "
             "can share this controller with --instances 2.",
    )
    parser.add_argument(
        "--connections", default="single", choices=("single", "multi"),
        help="How many centrals the emulated controller accepts at once. "
             "'single' matches real Aurora hardware — it stops advertising "
             "while a client is on, which is what CruxRelay exists for. "
             "'multi' keeps advertising so several apps can connect. "
             "Switchable at runtime in the GUI (default: %(default)s).",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run without the Tkinter GUI; log decoded holds to stdout. "
             "Can also be enabled via the BOARDSIM_HEADLESS env var.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all boards, layouts and sizes, then exit (no BLE).",
    )
    return parser.parse_args()


def _build_session(sel, api_level, serial):
    """Resolve a :class:`Selection` into ``(board, variant, session)``.

    ``api_level``/``serial`` are Aurora-only knobs; they are dropped for a
    MoonBoard selection (``create_session`` rejects them there), so a board
    switch onto the MoonBoard never trips the option guard.
    """
    from protocols.session import create_session

    board = board_for(sel.board_key)
    variant = board.variant_for(sel.layout_key)
    aurora = board.protocol == PROTOCOL_AURORA
    session = create_session(
        board, variant, sel.size_id,
        api_level if aurora else None,
        serial if aurora else None,
    )
    return board, variant, session


def _next_serial(serial: str) -> str:
    """Derive a distinct, compact serial for the second virtual board."""
    import re

    match = re.fullmatch(r"(.*?)(\d+)", serial)
    if match:
        prefix, digits = match.groups()
        return prefix + str(int(digits) + 1).zfill(len(digits))
    return serial[:11] + "2"


def _run_headless(session, multi_connect: bool,
                  adapter: str = DEFAULT_ADAPTER) -> int:
    """Single board, no GUI: wait for BLE writes until Ctrl+C or a fatal
    peripheral error. Headless has no board bar — switch by restarting."""
    from ble.peripheral import BLEPeripheral

    renderer = session.create_renderer(headless=True)
    stop_event = threading.Event()
    state = {"code": 0}

    def shutdown(fatal: bool = False) -> None:
        if fatal:
            state["code"] = 1
            logger.error("BLE peripheral died — shutting down")
        else:
            logger.info("Shutting down...")
        ble.stop()
        stop_event.set()

    def peripheral_failed(_exc: BaseException) -> None:
        # Runs on the BLE thread; let the main thread perform stop()/join().
        state["code"] = 1
        logger.error("BLE peripheral died — shutting down")
        stop_event.set()

    ble = BLEPeripheral(
        ble_name=session.ble_name, profile=session.gatt_profile,
        on_data=session.feed,
        on_connect=lambda: renderer.update_status("Verbunden"),
        on_disconnect=lambda: (session.connection_lost(),
                               renderer.update_status("Advertising...")),
        on_fatal=peripheral_failed,
        multi_connect=multi_connect,
        adapter=adapter,
    )
    signal.signal(signal.SIGINT, lambda *_: shutdown())
    try:
        ble.start()
        logger.info("Headless mode — waiting for BLE writes (Ctrl+C to quit)")
        stop_event.wait()
    except Exception:
        logger.exception("Fatal error")
        state["code"] = 1
    finally:
        ble.stop()
    return state["code"]


def _run_headless_multi(sessions: list, multi_connect: bool,
                        adapter: str = DEFAULT_ADAPTER) -> int:
    """Two board sessions and renderers behind one physical controller."""
    from ble.multiplex import MultiplexedBLEPeripheral, VirtualBoard

    renderers = [session.create_renderer(headless=True) for session in sessions]
    stop_event = threading.Event()
    state = {"code": 0}

    def shutdown(fatal: bool = False) -> None:
        if fatal:
            state["code"] = 1
            logger.error("BLE multiplexer died — shutting down")
        ble.stop()
        stop_event.set()

    def peripheral_failed(_exc: BaseException) -> None:
        # Runs on the BLE thread; joining it here would join the current thread.
        state["code"] = 1
        logger.error("BLE multiplexer died — shutting down")
        stop_event.set()

    boards = [
        VirtualBoard(
            key=f"slot-{index + 1}", ble_name=session.ble_name,
            profile=session.gatt_profile, on_data=session.feed,
            on_connect=lambda r=renderer: r.update_status("Verbunden"),
            on_disconnect=lambda r=renderer, s=session: (
                s.connection_lost(), r.update_status("Advertising...")),
        )
        for index, (session, renderer) in enumerate(zip(sessions, renderers))
    ]
    ble = MultiplexedBLEPeripheral(
        boards, on_fatal=peripheral_failed,
        multi_connect=multi_connect, adapter=adapter)
    signal.signal(signal.SIGINT, lambda *_: shutdown())
    try:
        ble.start()
        logger.info("Headless two-board mode — waiting for BLE writes")
        stop_event.wait()
    finally:
        ble.stop()
    return state["code"]


def _run_gui(selection, second_selection, api_level, serial, second_serial,
             multi_connect: bool, instance_count: int = 1,
             adapter: str = DEFAULT_ADAPTER) -> int:
    """GUI mode: ONE persistent Tk root; a board-bar pick tears down the
    current panel + BLE peripheral and rebuilds them for the new board.

    A switch changes the BLE name, GATT profile and renderer, so it is a
    clean rebuild — but the root and its main loop persist. Recreating
    tk.Tk() per switch crashed Tcl ("async handler deleted by the wrong
    thread"): the BLE thread queues canvas updates via root.after(), and
    tearing the root down underneath them frees Tcl handlers off-thread.
    Swapping only the root's children keeps the one interpreter alive.

    The adapter is fixed for the whole session: the board bar changes
    WHICH board this realm presents, never which controller it lives on,
    so a switch on hci1 never disturbs the board running on hci0.
    """
    import tkinter as tk

    from ble.multiplex import MultiplexedBLEPeripheral, VirtualBoard
    from ble.peripheral import BLEPeripheral

    root = tk.Tk()
    root.configure(bg="#1a1a1a")
    root.resizable(False, False)
    state: dict = {
        "ble": None, "fatal": False, "multi": multi_connect,
        "count": instance_count,
        "selections": [selection, second_selection],
    }

    def teardown() -> None:
        # Stop BLE first (joins its thread) so no more root.after() updates
        # are queued, THEN destroy the panel widgets.
        if state["ble"] is not None:
            state["ble"].stop()
            state["ble"] = None
        for child in root.winfo_children():
            child.destroy()

    def close(fatal: bool = False) -> None:
        if fatal:
            state["fatal"] = True
            logger.error("BLE peripheral died — shutting down")
        else:
            logger.info("Shutting down...")
        teardown()
        root.quit()

    def switch(slot: int, new_sel) -> None:
        logger.info("Switching board %d → %s / %s",
                    slot + 1, new_sel.board_key, new_sel.layout_key)
        state["selections"][slot] = new_sel
        teardown()
        build()

    def set_instances(count: int) -> None:
        if count == state["count"]:
            return
        logger.info("Simulation mode → %d board(s)", count)
        state["count"] = count
        teardown()
        build()

    def set_connections(multi: bool) -> None:
        # Live switch: the peripheral brings advertising up or takes it down
        # itself, so neither the panel nor the BLE thread is rebuilt.
        state["multi"] = multi
        if state["ble"] is not None:
            state["ble"].set_multi_connect(multi)

    def build() -> None:
        count = state["count"]
        serials = [serial, second_serial]
        resolved = [
            _build_session(state["selections"][slot], api_level, serials[slot])
            for slot in range(count)
        ]
        sessions = [item[2] for item in resolved]
        root.title(sessions[0].window_title if count == 1
                   else "Board Simulator — two virtual boards")

        if count == 1:
            parents = [root]
        else:
            workspace = tk.Frame(root, bg="#101014")
            workspace.pack(fill=tk.BOTH, expand=True)
            parents = []
            for slot in range(2):
                frame = tk.Frame(
                    workspace, bg="#1a1a1a", highlightthickness=2,
                    highlightbackground="#34343c")
                frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                           padx=(4 if slot == 0 else 2,
                                 4 if slot == 1 else 2), pady=4)
                tk.Label(
                    frame, text=f"BOARD {slot + 1}", bg="#34343c",
                    fg="#f0f0f0", font=("Helvetica", 11, "bold"),
                    pady=5).pack(fill=tk.X, side=tk.TOP)
                parents.append(frame)

        panels = []
        for slot, ((board, variant, session), parent) in enumerate(
                zip(resolved, parents)):
            panel = session.create_panel(
                parent, state["selections"][slot],
                lambda new_sel, index=slot: switch(index, new_sel),
                state["multi"], set_connections,
                instance_count=count, on_instances=set_instances)
            panels.append(panel)
            logger.info("Board %d: %s — %s [%s protocol] / %s",
                        slot + 1, board.display_name, variant.display_name,
                        board.protocol, session.ble_name)

        def fatal(_exc: BaseException) -> None:
            root.after(0, close, True)
        if count == 1:
            session = sessions[0]
            panel = panels[0]
            ble = BLEPeripheral(
                ble_name=session.ble_name, profile=session.gatt_profile,
                on_data=session.feed,
                on_connect=lambda p=panel: p.update_status("Verbunden"),
                on_disconnect=lambda p=panel, s=session: (
                    s.connection_lost(), p.update_status("Advertising...")),
                on_fatal=fatal, multi_connect=state["multi"], adapter=adapter)
        else:
            virtual_boards = [
                VirtualBoard(
                    key=f"slot-{slot + 1}", ble_name=session.ble_name,
                    profile=session.gatt_profile, on_data=session.feed,
                    on_connect=lambda p=panel: p.update_status("Verbunden"),
                    on_disconnect=lambda p=panel, s=session: (
                        s.connection_lost(), p.update_status("Advertising...")),
                )
                for slot, (session, panel) in enumerate(zip(sessions, panels))
            ]
            ble = MultiplexedBLEPeripheral(
                virtual_boards, on_fatal=fatal,
                multi_connect=state["multi"], adapter=adapter)
        state["ble"] = ble
        ble.start()

    root.protocol("WM_DELETE_WINDOW", close)
    signal.signal(signal.SIGINT, lambda *_: root.after(0, close))
    build()
    try:
        root.mainloop()
    finally:
        if state["ble"] is not None:
            state["ble"].stop()
        try:
            root.destroy()
        except tk.TclError:
            pass
    return 1 if state["fatal"] else 0


def main() -> int:
    args = _parse_args()
    if args.list:
        print_board_list()
        return 0

    headless = args.headless or config.headless_mode()

    # Resolve + validate the initial selection BEFORE the BlueZ preflight so
    # a bad --layout/--size/option combo exits 2 (a clear CLI error) rather
    # than 1 (the no-Bluetooth fail-fast).
    sel = Selection(args.board, args.layout, args.size)
    second_board_key = args.second_board or args.board
    second_layout = args.second_layout
    if second_layout is None and args.second_board is None:
        second_layout = args.layout
    second_sel = Selection(second_board_key, second_layout, args.second_size)
    first_serial = args.serial or config.BOARD_SERIAL
    second_serial = args.second_serial or _next_serial(first_serial)
    try:
        board, variant, session = _build_session(sel, args.api_level, args.serial)
        second_board, second_variant, second_session = _build_session(
            second_sel, args.api_level, second_serial)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2
    if (board.protocol == PROTOCOL_AURORA
            and second_board.protocol == PROTOCOL_AURORA
            and board.key == second_board.key
            and session.ble_name == second_session.ble_name):
        logger.error("the two virtual boards have the same Aurora identity; "
                     "choose a distinct --second-serial")
        return 2
    # Normalise so the board bar shows the real current layout (not None).
    sel = Selection(board.key, variant.key, args.size)
    second_sel = Selection(
        second_board.key, second_variant.key, args.second_size)

    logger.info("Board Simulator starting")
    logger.info("Board: %s — %s [%s protocol]", board.display_name,
                variant.display_name, board.protocol)
    logger.info("Mode: %s", "headless" if headless else "GUI")
    logger.info("Adapter: %s", args.adapter)
    logger.info("Virtual boards: %d", args.instances)
    multi = args.connections == "multi"
    logger.info("Connections: %s", args.connections)

    # Fail fast on machines without BlueZ / without THIS adapter instead of
    # silently advertising into the void.
    from ble.peripheral import BlueZUnavailableError, preflight_check
    try:
        preflight_check(args.adapter)
    except BlueZUnavailableError as exc:
        logger.error("Bluetooth unavailable: %s", exc)
        logger.error("Live BLE needs a Linux box with BlueZ and an adapter; "
                     "--list and the test suite run without one.")
        return 1

    try:
        if headless:
            if args.instances == 2:
                return _run_headless_multi(
                    [session, second_session], multi, args.adapter)
            return _run_headless(session, multi, args.adapter)
        return _run_gui(
            sel, second_sel, args.api_level, args.serial, second_serial,
            multi, args.instances, args.adapter)
    finally:
        logger.info("Goodbye")


if __name__ == "__main__":
    sys.exit(main())
