"""Quantum Board renderer using the five original eWalls 2.0.14 images."""

from __future__ import annotations

import tkinter as tk
import logging
import os
from typing import Callable

from PIL import Image as PILImage, ImageTk

from board_state import QuantumHoldMap
from quantum_geometry import QuantumGeometry
from render.switching import BoardBar
from selection import Selection

BACKGROUND = "#111318"
PANEL = "#252932"
logger = logging.getLogger(__name__)


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
        canvas_w = int(canvas_h * geometry.aspect_ratio)
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
        self._photo_ref: ImageTk.PhotoImage | None = None
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "assets", "quantum", geometry.variant.asset_file)
        try:
            image = PILImage.open(path).convert("RGB")
            image = image.resize((canvas_w, canvas_h), PILImage.LANCZOS)
            self._photo_ref = ImageTk.PhotoImage(image)
            self._canvas.create_image(0, 0, anchor=tk.NW,
                                      image=self._photo_ref)
            logger.info("Loaded original Quantum image: %s",
                        geometry.variant.asset_file)
        except Exception:
            logger.exception("Failed to load Quantum image: %s", path)
            self._canvas.create_rectangle(0, 0, canvas_w, canvas_h,
                                          fill=PANEL, outline="#596172")
        self._items: dict[int, int] = {}
        # eWalls draws a 20dp diode with a 4dp border on a phone board view
        # of roughly 390dp. Scale that visual contract to this desktop canvas
        # instead of shrinking dense models to near-invisible 6px dots.
        radius = max(4, round(canvas_w * 10 / 390))
        self._active_outline_width = max(2, round(canvas_w * 4 / 390))
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
                self._canvas.itemconfig(
                    item, fill="#444b58", outline="#171a20", width=1)
            for address, light in holds.items():
                item = self._items.get(address)
                if item is not None:
                    color = f"#{light.r:02x}{light.g:02x}{light.b:02x}"
                    self._canvas.itemconfig(
                        item, fill=color, outline="#ffffff",
                        width=self._active_outline_width)
        except tk.TclError:
            return
