"""Live board switcher widgets shared by both GUIs.

:class:`BoardBar` is the dropdown row (Board / Layout / Size) that lets the
user re-target the simulator at runtime. :class:`SwitchableWindow` is the
small mixin that lets a Tk window end its main loop with either a *switch
request* or a plain *quit* — main.py's rebuild loop reads ``switch_request``
after ``run()`` returns and rebuilds the session + BLE peripheral for the
new board (a board change alters the BLE name, GATT shape and renderer, so
an in-place swap is not possible; a clean soft-restart is).

Pure UI glue — the selection arithmetic lives in ``selection.py``.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Callable

import selection as sel
from selection import Selection

logger = logging.getLogger(__name__)

BAR_BG = "#202024"
LABEL_FG = "#9aa0aa"
LABEL_FONT = ("Helvetica", 9)


class BoardBar:
    """Board / Layout / Size dropdowns.

    Every pick is turned into a complete, valid :class:`Selection` (see
    ``selection.change_*``) and handed to ``on_switch``. The bar itself is
    stateless beyond the current selection: after a switch the window is
    rebuilt, so a fresh bar is created with the new board's options — no
    cascading-combobox bookkeeping here.
    """

    def __init__(self, parent: tk.Misc, current: Selection,
                 on_switch: Callable[[Selection], None]) -> None:
        self._current = current
        self._on_switch = on_switch
        self._frame = tk.Frame(parent, bg=BAR_BG)

        self._board_to_key = {dn: k for k, dn in sel.board_choices()}
        self._layout_to_key = {
            dn: k for k, dn in sel.layout_choices(current.board_key)}
        self._size_to_id = {
            name: sid for sid, name
            in sel.size_choices(current.board_key, current.layout_key)}

        self._board_var = self._add_combo(
            "Board", list(self._board_to_key),
            self._display_for(self._board_to_key, current.board_key),
            self._on_board)
        self._layout_var = self._add_combo(
            "Layout", list(self._layout_to_key),
            self._display_for(self._layout_to_key, current.layout_key),
            self._on_layout, width=24)
        self._size_var = None
        if self._size_to_id:
            self._size_var = self._add_combo(
                "Size", list(self._size_to_id),
                self._display_for(self._size_to_id,
                                  sel.effective_size_id(current)),
                self._on_size, width=22)

    def pack(self, **kwargs) -> "BoardBar":
        self._frame.pack(**kwargs)
        return self

    # -- internals --------------------------------------------------

    @staticmethod
    def _display_for(mapping: dict, value) -> str:
        for display, mapped in mapping.items():
            if mapped == value:
                return display
        return next(iter(mapping), "")

    def _add_combo(self, label: str, values: list[str], current: str,
                   handler: Callable, width: int = 18) -> tk.StringVar:
        tk.Label(self._frame, text=f"{label}:", bg=BAR_BG, fg=LABEL_FG,
                 font=LABEL_FONT).pack(side=tk.LEFT, padx=(10, 2), pady=6)
        var = tk.StringVar(value=current)
        combo = ttk.Combobox(self._frame, textvariable=var, values=values,
                             state="readonly", width=width)
        combo.pack(side=tk.LEFT, padx=(0, 4))
        combo.bind("<<ComboboxSelected>>", handler)
        return var

    def _on_board(self, _event=None) -> None:
        key = self._board_to_key.get(self._board_var.get())
        if key and key != self._current.board_key:
            self._on_switch(sel.change_board(key))

    def _on_layout(self, _event=None) -> None:
        key = self._layout_to_key.get(self._layout_var.get())
        if key and key != self._current.layout_key:
            self._on_switch(sel.change_layout(self._current.board_key, key))

    def _on_size(self, _event=None) -> None:
        if self._size_var is None:
            return
        size_id = self._size_to_id.get(self._size_var.get())
        if size_id is not None and size_id != sel.effective_size_id(self._current):
            self._on_switch(sel.change_size(
                self._current.board_key, self._current.layout_key, size_id))


class SwitchableWindow:
    """Mixin for a Tk window whose ``run()`` returns to switch or to quit.

    The host must set ``self._root`` (a ``tk.Tk``). After ``run()`` returns,
    main.py inspects :attr:`switch_request` (a :class:`Selection` to rebuild
    for, or ``None`` to quit) and :attr:`fatal` (BLE died → exit non-zero).
    """

    switch_request: Selection | None = None
    fatal: bool = False

    def request_switch(self, new: Selection) -> None:
        """BoardBar callback (GUI thread): record target, end the main loop."""
        logger.info("Board switch requested → %s / %s%s",
                    new.board_key, new.layout_key,
                    f" / size {new.size_id}" if new.size_id is not None else "")
        self.switch_request = new
        self._root.quit()  # type: ignore[attr-defined]

    def request_quit(self, fatal: bool = False) -> None:
        """Thread-safe quit (e.g. from the BLE thread on a fatal error)."""
        if fatal:
            self.fatal = True
        try:
            self._root.after(0, self._root.quit)  # type: ignore[attr-defined]
        except Exception:
            pass

    def close_window(self) -> None:
        try:
            self._root.destroy()  # type: ignore[attr-defined]
        except Exception:
            pass
