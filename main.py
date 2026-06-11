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


def main() -> int:
    args = _parse_args()
    if args.list:
        print_board_list()
        return 0

    headless = args.headless or config.headless_mode()
    board = board_for(args.board)
    variant = board.variant_for(args.layout)

    from protocols.session import create_session
    try:
        session = create_session(board, variant, args.size,
                                 args.api_level, args.serial)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    logger.info("Board Simulator starting")
    logger.info("Board: %s — %s [%s protocol]", board.display_name,
                variant.display_name, board.protocol)
    logger.info("BLE name: %s", session.ble_name)
    logger.info("Mode: %s", "headless" if headless else "GUI")

    # Fail fast on machines without BlueZ / a Bluetooth adapter instead
    # of silently advertising into the void.
    from ble.peripheral import BLEPeripheral, BlueZUnavailableError
    try:
        from ble.peripheral import preflight_check
        preflight_check()
    except BlueZUnavailableError as exc:
        logger.error("Bluetooth unavailable: %s", exc)
        logger.error("Live BLE needs a Linux box with BlueZ and an adapter; "
                     "--list and the test suite run without one.")
        return 1

    renderer = session.create_renderer(headless)

    stop_event = threading.Event()
    exit_code = 0

    def on_ble_connect() -> None:
        renderer.update_status("Verbunden")  # type: ignore[attr-defined]

    def on_ble_disconnect() -> None:
        renderer.update_status("Advertising...")  # type: ignore[attr-defined]

    ble = BLEPeripheral(
        ble_name=session.ble_name,
        profile=session.gatt_profile,
        on_data=session.feed,
        on_connect=on_ble_connect,
        on_disconnect=on_ble_disconnect,
        on_fatal=lambda exc: shutdown(fatal=True),
    )

    def shutdown(fatal: bool = False) -> None:
        nonlocal exit_code
        if fatal:
            exit_code = 1
            logger.error("BLE peripheral died — shutting down")
        else:
            logger.info("Shutting down...")
        ble.stop()
        stop_event.set()
        if not headless:
            try:
                renderer._root.after(0, renderer._root.destroy)  # type: ignore[attr-defined]
            except Exception:
                pass

    signal.signal(signal.SIGINT, lambda *_: shutdown())

    try:
        ble.start()
        if headless:
            logger.info("Headless mode — waiting for BLE writes (Ctrl+C to quit)")
            stop_event.wait()
        else:
            renderer.set_close_callback(shutdown)  # type: ignore[attr-defined]
            renderer.run()  # type: ignore[attr-defined]  # blocks until window closed
    except Exception:
        logger.exception("Fatal error")
        exit_code = 1
    finally:
        ble.stop()
        logger.info("Goodbye")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
