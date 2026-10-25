"""Shiny Wall GUI panel: a schematic wall, lit holds as rings + index.

No vendor images: every hold is a small dot at its position (geometry
file, or the placeholder index order), a lit hold a ring in its role's
colour with its index next to it, so a tester can read which index the app
lit. The legend says where the positions come from.
"""

from __future__ import annotations

import logging
import tkinter as tk
from typing import Callable

from board_state import ShinyHoldMap
from protocols.shinywall import (CODE_FOOT, CODE_HAND, CODE_START, CODE_TOP,
                                 ROLE_NAMES, ROLE_RGB, UNKNOWN_RGB)
from render.layout import MIN_BOARD_HEIGHT, fit_board
from render.switching import BoardBar
from selection import Selection
from shinywall_geometry import ShinyWallGeometry

logger = logging.getLogger(__name__)

BACKGROUND = "#15171c"
DOT = "#3a404c"
ROLE_COLORS = {code: "#%02x%02x%02x" % rgb for code, rgb in ROLE_RGB.items()}
UNKNOWN_COLOR = "#%02x%02x%02x" % UNKNOWN_RGB


def ring_radius(board_w: int, columns: int) -> float:
    """Ring radius in px: a bit under half a grid cell."""
    return max(3.0, board_w / max(columns, 1) * 0.38)


class ShinyWallGUI:
    def __init__(self, parent: tk.Misc, geometry: ShinyWallGeometry,
                 ble_name: str, selection: Selection | None = None,
                 on_switch: Callable[[Selection], None] | None = None,
                 multi_connect: bool = False,
                 on_connections: Callable[[bool], None] | None = None,
                 instance_count: int = 1,
                 on_instances: Callable[[int], None] | None = None,
                 board_height: int | None = None) -> None:
        self._parent = parent
        self._geometry = geometry
        self._holds: ShinyHoldMap = {}
        canvas_h = max(MIN_BOARD_HEIGHT,
                       board_height or (520 if instance_count == 2 else 750))
        canvas_w = int(canvas_h * geometry.aspect_ratio)

        self._status = tk.StringVar(value="Advertising...")
        bar = tk.Frame(parent, bg="#0b0d10")
        bar.pack(fill=tk.X)
        tk.Label(bar, textvariable=self._status, bg="#0b0d10", fg="#55dd88",
                 padx=10, pady=5).pack(side=tk.LEFT)
        tk.Label(bar, text=ble_name, bg="#0b0d10", fg="#80aaff",
                 padx=10, pady=5).pack(side=tk.RIGHT)
        if selection is not None and on_switch is not None:
            BoardBar(parent, selection, on_switch, multi_connect,
                     on_connections, instance_count, on_instances).pack(fill=tk.X)
        self._legend = tk.Frame(parent, bg=BACKGROUND)
        self._legend.pack(fill=tk.X)
        for code in (CODE_START, CODE_HAND, CODE_FOOT, CODE_TOP):
            tk.Label(self._legend, text=f"● {ROLE_NAMES[code]}", bg=BACKGROUND,
                     fg=ROLE_COLORS[code], padx=6).pack(side=tk.LEFT)
        tk.Label(self._legend, text="● unknown code", bg=BACKGROUND,
                 fg=UNKNOWN_COLOR, padx=6).pack(side=tk.LEFT)
        source = geometry.source
        if geometry.unplaced:
            source += f"; {geometry.unplaced} index(es) without a position"
        tk.Label(parent, text=f"{geometry.hold_count} holds · positions: {source}",
                 bg=BACKGROUND, fg="#9aa0aa", anchor="w", padx=8,
                 wraplength=max(320, canvas_w)).pack(fill=tk.X)
        self._count = tk.StringVar(value="0 holds lit")
        tk.Label(parent, textvariable=self._count, bg=BACKGROUND, fg="#d0d4dc",
                 anchor="w", padx=8).pack(fill=tk.X)
        self._canvas = tk.Canvas(parent, width=canvas_w, height=canvas_h,
                                 bg=BACKGROUND, highlightthickness=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.bind("<Configure>", lambda _event: self._draw())

    def update_holds(self, holds: ShinyHoldMap) -> None:
        self._parent.after(0, self._apply, dict(holds))

    def update_status(self, text: str) -> None:
        self._parent.after(0, self._status.set, text)

    def _apply(self, holds: ShinyHoldMap) -> None:
        self._holds = holds
        try:
            self._count.set(f"{len(holds)} holds lit")
            self._draw()
        except tk.TclError:
            return

    def _draw(self) -> None:
        canvas = self._canvas
        try:
            avail_w, avail_h = canvas.winfo_width(), canvas.winfo_height()
        except tk.TclError:
            return
        if avail_w < 2 or avail_h < 2:
            return
        board_w, board_h, off_x, off_y = fit_board(
            avail_w, avail_h, self._geometry.aspect_ratio)
        canvas.delete("all")
        columns = max(1, round(self._geometry.span[0]))
        radius = ring_radius(board_w, columns)
        dot = max(1.5, radius * 0.22)
        font = ("Helvetica", max(7, int(radius * 0.8)))
        for hold in self._geometry.holds:
            u, v = self._geometry.to_unit(hold)
            x, y = off_x + u * board_w, off_y + v * board_h
            code = self._holds.get(hold.index)
            if not code:
                size = dot * (0.7 if hold.kind == "small" else 1.0)
                canvas.create_oval(x - size, y - size, x + size, y + size,
                                   fill=DOT, outline="")
                continue
            color = ROLE_COLORS.get(code, UNKNOWN_COLOR)
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                               outline=color, width=max(2, radius * 0.25))
            canvas.create_text(x, y, text=str(hold.index), fill=color,
                               font=font)
