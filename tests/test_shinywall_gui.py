"""Tk-free test of the Shiny Wall panel's drawing (no display in CI)."""

import importlib
import sys
from types import ModuleType

from boards import board_for
from shinywall_geometry import ShinyWallGeometry


def load_gui(monkeypatch):
    tkinter = ModuleType("tkinter")
    tkinter.TclError = RuntimeError
    monkeypatch.setitem(sys.modules, "tkinter", tkinter)
    switching = ModuleType("render.switching")
    switching.BoardBar = object
    monkeypatch.setitem(sys.modules, "render.switching", switching)
    monkeypatch.delitem(sys.modules, "render.shinywall_gui", raising=False)
    return importlib.import_module("render.shinywall_gui")


class Canvas:
    def __init__(self) -> None:
        self.ovals, self.texts = [], []

    def winfo_width(self) -> int:
        return 340

    def winfo_height(self) -> int:
        return 750

    def delete(self, _tag) -> None:
        self.ovals.clear()
        self.texts.clear()

    def create_oval(self, *coords, **kwargs) -> None:
        self.ovals.append((coords, kwargs))

    def create_text(self, x, y, **kwargs) -> None:
        self.texts.append((x, y, kwargs))


def test_lit_holds_are_role_rings_with_their_index(monkeypatch) -> None:
    gui = load_gui(monkeypatch)
    panel = object.__new__(gui.ShinyWallGUI)
    panel._geometry = ShinyWallGeometry(board_for("shinywall").variant_for("st1"))
    panel._canvas = Canvas()
    panel._holds = {0: 2, 40: 4, 150: 6, 300: 8, 7: 5}
    panel._draw()

    rings = [kw["outline"] for _c, kw in panel._canvas.ovals if kw.get("outline")]
    assert sorted(rings) == sorted([gui.ROLE_COLORS[2], gui.ROLE_COLORS[4],
                                    gui.ROLE_COLORS[6], gui.ROLE_COLORS[8],
                                    gui.UNKNOWN_COLOR])
    dots = [kw for _c, kw in panel._canvas.ovals if kw.get("fill") == gui.DOT]
    assert len(dots) == 311 - 5
    assert sorted(kw["text"] for _x, _y, kw in panel._canvas.texts) == sorted(
        ["0", "40", "150", "300", "7"])
    # Index 0 sits bottom left, 300 near the top.
    by_index = {kw["text"]: (x, y) for x, y, kw in panel._canvas.texts}
    assert by_index["0"][1] > by_index["300"][1]
    assert by_index["0"][0] < 340 * 0.2


def test_ring_radius_scales_with_the_board() -> None:
    import render.shinywall_gui as gui  # real tkinter is importable headless
    assert gui.ring_radius(340, 12) > gui.ring_radius(170, 12)
    assert gui.ring_radius(10, 18) == 3.0
