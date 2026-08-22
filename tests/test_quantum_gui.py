"""Tk-free regression tests for the Quantum image overlay."""

import importlib
import sys
from types import ModuleType, SimpleNamespace


def test_active_diodes_are_transparent_colored_rings(monkeypatch) -> None:
    tkinter = ModuleType("tkinter")
    tkinter.HIDDEN = "hidden"
    tkinter.NORMAL = "normal"
    tkinter.TclError = RuntimeError
    monkeypatch.setitem(sys.modules, "tkinter", tkinter)

    switching = ModuleType("render.switching")
    switching.BoardBar = object
    monkeypatch.setitem(sys.modules, "render.switching", switching)
    monkeypatch.delitem(sys.modules, "render.quantum_gui", raising=False)

    quantum_gui = importlib.import_module("render.quantum_gui")

    class Canvas:
        def __init__(self) -> None:
            self.calls = []

        def itemconfig(self, item, **kwargs) -> None:
            self.calls.append((item, kwargs))

    panel = object.__new__(quantum_gui.QuantumBoardGUI)
    panel._canvas = Canvas()
    panel._items = {1001: 7, 1002: 8}
    panel._active_outline_width = 4
    panel._apply({1001: SimpleNamespace(r=1, g=2, b=3)})

    assert panel._canvas.calls[-1] == (
        7,
        {"fill": "", "outline": "#010203", "width": 4, "state": "normal"},
    )
    assert all(call[1].get("outline") != "#ffffff"
               for call in panel._canvas.calls)


def test_ring_is_thinned_from_the_outside(monkeypatch) -> None:
    tkinter = ModuleType("tkinter")
    monkeypatch.setitem(sys.modules, "tkinter", tkinter)
    switching = ModuleType("render.switching")
    switching.BoardBar = object
    monkeypatch.setitem(sys.modules, "render.switching", switching)
    monkeypatch.delitem(sys.modules, "render.quantum_gui", raising=False)

    quantum_gui = importlib.import_module("render.quantum_gui")
    radius, width = quantum_gui.ring_metrics(390)

    assert (radius, width) == (8, 2)
    assert radius - width == 10 - 4  # original inner radius is unchanged
