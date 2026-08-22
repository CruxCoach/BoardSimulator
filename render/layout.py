"""Pure geometry helpers for the resizable board panels.

Kept free of Tkinter/PIL imports so the scaling math stays unit-testable
on machines without a display, and so the headless renderers never pull
in a GUI dependency through it.
"""

# Board height the per-panel drawing constants (ring radius, stroke width)
# are calibrated for; everything scales relative to it.
REFERENCE_HEIGHT: int = 750

# Smallest board height a window may be shrunk to before it stops being
# readable on a projector.
MIN_BOARD_HEIGHT: int = 260


def fit_board(avail_w: int, avail_h: int, aspect: float) -> tuple[int, int, int, int]:
    """Fit a board with the given aspect ratio into an available area.

    Returns ``(width, height, offset_x, offset_y)``: the largest box that keeps
    the aspect ratio, fits inside the area, and is centered in it. Leftover
    space becomes symmetric letterboxing.
    """
    avail_w = max(int(avail_w), 0)
    avail_h = max(int(avail_h), 0)
    if aspect <= 0 or avail_w == 0 or avail_h == 0:
        return avail_w, avail_h, 0, 0

    # Use the limiting dimension as-is and derive the other one, so the board
    # really fills the window instead of losing a pixel to double rounding.
    if avail_w / avail_h > aspect:
        board_h = avail_h
        board_w = min(avail_w, round(avail_h * aspect))
    else:
        board_w = avail_w
        board_h = min(avail_h, round(avail_w / aspect))
    return board_w, board_h, (avail_w - board_w) // 2, (avail_h - board_h) // 2


def scale_factor(board_h: int, base_h: int = REFERENCE_HEIGHT) -> float:
    """Scale of the current board height against the panel's opening height.

    ``base_h`` is the height the panel's drawing constants were calibrated
    for, so a panel at its default size renders exactly as before.
    """
    return board_h / max(base_h, 1)


def scaled(value: float, board_h: int, base_h: int = REFERENCE_HEIGHT,
           minimum: float = 1.0) -> float:
    """Scale a drawing metric (radius, stroke) to the current board height."""
    return max(minimum, value * scale_factor(board_h, base_h))
