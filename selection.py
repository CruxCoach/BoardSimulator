"""Tk-free board-selection model for the live board switcher.

The GUI board bar (``render/switching.py``) and main's rebuild loop share
this. A :class:`Selection` is a fully-specified (board, layout, size)
choice; the ``change_*`` helpers turn one combobox pick into the next
*complete, valid* Selection:

- changing the board resets to that board's default layout + default size,
- changing the layout resets to that layout's default size,
- a MoonBoard never carries a size (no product sizes).

Pure registry logic — no Tkinter, no BLE — so it is unit-tested directly
(``tests/test_selection.py``) without a display or a Bluetooth adapter.
"""

from __future__ import annotations

from dataclasses import dataclass

from boards import PROTOCOL_AURORA, board_for


@dataclass(frozen=True)
class Selection:
    """A fully-specified board choice.

    ``size_id`` is ``None`` for MoonBoard (no product sizes) and, for an
    Aurora board, means "the layout's default size".
    """

    board_key: str
    layout_key: str
    size_id: int | None = None


def is_aurora(board_key: str) -> bool:
    return board_for(board_key).protocol == PROTOCOL_AURORA


def board_choices() -> list[tuple[str, str]]:
    """(key, display_name) for every simulated board, registry order."""
    from boards import BOARDS

    return [(b.key, b.display_name) for b in BOARDS.values()]


def layout_choices(board_key: str) -> list[tuple[str, str]]:
    """(layout key, display_name) for one board's variants."""
    return [(v.key, v.display_name) for v in board_for(board_key).variants]


def size_choices(board_key: str, layout_key: str) -> list[tuple[int, str]]:
    """(size_id, name) for an Aurora layout; empty for MoonBoard."""
    board = board_for(board_key)
    if board.protocol != PROTOCOL_AURORA:
        return []
    from board_geometry import list_sizes

    variant = board.variant_for(layout_key)
    return [(s.id, s.name) for s in list_sizes(board, variant.product_id)]


def default_size_id(board_key: str, layout_key: str) -> int | None:
    """The size a Selection with ``size_id=None`` resolves to (Aurora only)."""
    board = board_for(board_key)
    if board.protocol != PROTOCOL_AURORA:
        return None
    return board.variant_for(layout_key).default_size_id


def effective_size_id(sel: Selection) -> int | None:
    """The concrete size the bar should show as selected."""
    if sel.size_id is not None:
        return sel.size_id
    return default_size_id(sel.board_key, sel.layout_key)


def change_board(new_board_key: str) -> Selection:
    """Switch board → its default layout + default size."""
    board = board_for(new_board_key)
    return Selection(new_board_key, board.default_variant.key, None)


def change_layout(board_key: str, new_layout_key: str) -> Selection:
    """Switch layout within a board → the layout's default size."""
    return Selection(board_key, new_layout_key, None)


def change_size(board_key: str, layout_key: str, new_size_id: int) -> Selection:
    """Pick an explicit Aurora product size within the current layout."""
    return Selection(board_key, layout_key, new_size_id)
