"""Own schematic Quantum Board renderer (no Walltopia image assets)."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from board_state import QuantumHoldMap
from quantum_geometry import QuantumGeometry
from render.switching import BoardBar
from selection import Selection

BACKGROUND = "#111318"
PANEL = "#252932"


class QuantumBoardGUI:
    def __init__(self, parent: tk.Misc, geometry: QuantumGeometry, ble_name: str,
                 selection: Selection | None = None,
                 on_switch: Callable[[Selection], None] | None = None,
                 multi_connect: bool = False,
                 on_connections: Callable[[bool], None] | None = None,
                 instance_count: int = 1,
                 on_instances: Callable[[int], None] | None = None) -> None:
        self._parent = parent
        self._geometry = geometry
        canvas_h = 520 if instance_count == 2 else 750
        canvas_w = max(420, int(canvas_h * geometry.aspect_ratio))
        self._status = tk.StringVar(value="Advertising...")
        status = tk.Frame(parent, bg="#0b0d10")
        status.pack(fill=tk.X)
        tk.Label(status, textvariable=self._status, bg="#0b0d10",
                 fg="#55dd88", padx=10, pady=5).pack(side=tk.LEFT)
        tk.Label(status, text=ble_name, bg="#0b0d10", fg="#80aaff",
                 padx=10, pady=5).pack(side=tk.RIGHT)
        if selection is not None and on_switch is not None:
            BoardBar(parent, selection, on_switch, multi_connect,
                     on_connections, instance_count, on_instances).pack(fill=tk.X)
        self._canvas = tk.Canvas(parent, width=canvas_w, height=canvas_h,
                                 bg=BACKGROUND, highlightthickness=0)
        self._canvas.pack()
        margin = 14
        self._canvas.create_polygon(
            margin, canvas_h - margin, margin, margin,
            canvas_w - margin, margin, canvas_w - margin, canvas_h - margin,
            fill=PANEL, outline="#596172", width=2)
        self._items: dict[int, int] = {}
        radius = 3 if len(geometry.diodes) > 500 else 4
        for diode in geometry.diodes:
            x, y = geometry.to_pixel(diode, canvas_w, canvas_h)
            item = self._canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill="#444b58", outline="#171a20", width=1)
            self._items[diode.address16] = item
            self._items[diode.address32] = item

    def update_holds(self, holds: QuantumHoldMap) -> None:
        self._parent.after(0, self._apply, holds)

    def update_status(self, text: str) -> None:
        self._parent.after(0, self._status.set, text)

    def _apply(self, holds: QuantumHoldMap) -> None:
        try:
            for item in set(self._items.values()):
                self._canvas.itemconfig(item, fill="#444b58", outline="#171a20")
            for address, light in holds.items():
                item = self._items.get(address)
                if item is not None:
                    color = f"#{light.r:02x}{light.g:02x}{light.b:02x}"
                    self._canvas.itemconfig(item, fill=color, outline="#ffffff")
        except tk.TclError:
            return
