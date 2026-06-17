"""Entry point for the consolidated CruxCoach board simulator.

Simulates exactly ONE board per process — pick the board, layout and
(for Aurora-protocol boards) product size on the command line. Starts
the BLE peripheral in a background thread; with a GUI it runs a Tkinter
window in the main thread, in headless mode it logs every decoded climb
as an ASCII grid.

Usage:
    sudo venv/bin/python main.py --board kilter
    sudo venv/bin/python main.py --board tension --layout tb2
    sudo venv/bin/python main.py --board moonboard --layout mini-2020
    sudo venv/bin/python main.py --board soill --size 1 --headless
    python main.py --list        # show boards, layouts and sizes (no BLE)
"""

import argparse
import logging
import signal
import sys
import threading

import config
from boards import BOARDS, PROTOCOL_AURORA, board_for
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
            else:
                print(f"  --layout {variant.key}{default}: {variant.display_name} "
                      f"[11x{variant.grid_rows} grid, no --size]")
        print()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Software BLE simulator for all interactive CruxCoach "
                    "boards (Kilter, Tension, Grasshopper, Decoy, So iLL, "
                    "Touchstone — Aurora protocol; MoonBoard — NUS/ASCII).",
    )
    parser.add_argument(
        "--board", default="kilter", choices=list(BOARDS),
        help="Which board to simulate (default: %(default)s).",
    )
    parser.add_argument(
        "--layout", default=None,
        help="Board layout/variant key (e.g. kilter: original | homewall; "
             "tension: tb1 | tb2 | tb2-spray; moonboard: 2016 | "
             "masters-2017 | masters-2019 | mini-2020). "
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


def _run_headless(session) -> int:
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

    ble = BLEPeripheral(
        ble_name=session.ble_name, profile=session.gatt_profile,
        on_data=session.feed,
        on_connect=lambda: renderer.update_status("Verbunden"),
        on_disconnect=lambda: renderer.update_status("Advertising..."),
        on_fatal=lambda exc: shutdown(fatal=True),
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


def _run_gui_loop(sel, api_level, serial) -> int:
    """GUI mode: run one board; when the board bar requests a switch, stop
    the BLE peripheral and rebuild the session + window for the new board.

    A board switch changes the BLE name, GATT profile and renderer, so an
    in-place swap is not possible — this clean soft-restart is. The process
    and the Tkinter session stay alive across switches.
    """
    import time

    from ble.peripheral import BLEPeripheral

    while True:
        board, variant, session = _build_session(sel, api_level, serial)
        logger.info("Board: %s — %s [%s protocol]", board.display_name,
                    variant.display_name, board.protocol)
        logger.info("BLE name: %s", session.ble_name)

        renderer = session.create_renderer(headless=False, selection=sel)
        # Bind the current renderer into each callback so a late BLE
        # callback can never reach the next iteration's window.
        ble = BLEPeripheral(
            ble_name=session.ble_name, profile=session.gatt_profile,
            on_data=session.feed,
            on_connect=lambda r=renderer: r.update_status("Verbunden"),
            on_disconnect=lambda r=renderer: r.update_status("Advertising..."),
            on_fatal=lambda exc, r=renderer: r.request_quit(fatal=True),
        )
        signal.signal(signal.SIGINT, lambda *_, r=renderer: r.request_quit())

        try:
            ble.start()
            renderer.run()  # blocks until a switch request or window close
        finally:
            ble.stop()

        nxt = renderer.switch_request
        fatal = renderer.fatal
        renderer.close_window()
        if fatal:
            return 1
        if nxt is None:
            return 0
        sel = nxt
        time.sleep(0.4)  # let BlueZ release the adapter before re-advertising


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
    try:
        board, variant, session = _build_session(sel, args.api_level, args.serial)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2
    # Normalise so the board bar shows the real current layout (not None).
    sel = Selection(board.key, variant.key, args.size)

    logger.info("Board Simulator starting")
    logger.info("Board: %s — %s [%s protocol]", board.display_name,
                variant.display_name, board.protocol)
    logger.info("Mode: %s", "headless" if headless else "GUI")

    # Fail fast on machines without BlueZ / a Bluetooth adapter instead of
    # silently advertising into the void.
    from ble.peripheral import BlueZUnavailableError, preflight_check
    try:
        preflight_check()
    except BlueZUnavailableError as exc:
        logger.error("Bluetooth unavailable: %s", exc)
        logger.error("Live BLE needs a Linux box with BlueZ and an adapter; "
                     "--list and the test suite run without one.")
        return 1

    try:
        if headless:
            return _run_headless(session)
        return _run_gui_loop(sel, args.api_level, args.serial)
    finally:
        logger.info("Goodbye")


if __name__ == "__main__":
    sys.exit(main())
