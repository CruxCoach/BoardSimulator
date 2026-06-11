"""Thread-safe board state, one flavour per protocol family.

Both families replace the whole state on every decoded message and notify
registered observers; they differ only in the shape of a hold:

- Aurora: keyed by LED position, value is the wire (r, g, b).
- MoonBoard: keyed by (column, row) grid cell, value is
  (role_code, r, g, b) — the role travels in the ASCII token, not in a
  colour, so it is part of the state.
"""

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

# Aurora: position -> (r, g, b)
AuroraHoldMap = dict[int, tuple[int, int, int]]
# MoonBoard: (column, row) -> (role_code, r, g, b)
MoonHoldMap = dict[tuple[int, int], tuple[int, int, int, int]]


class _BaseBoardState:
    """Lock, observer list and replace-all semantics shared by both
    families. Subclasses translate their decoder's message shape into the
    state dict via :meth:`update`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holds: dict = {}
        self._callbacks: list[Callable] = []

    def register_callback(self, callback: Callable) -> None:
        """Register a callback to be notified on state changes."""
        self._callbacks.append(callback)

    def _replace(self, new_state: dict) -> None:
        with self._lock:
            self._holds = new_state
        logger.info("Board state updated: %d holds active", len(new_state))
        for callback in self._callbacks:
            try:
                callback(new_state)
            except Exception:
                logger.exception("Error in board state callback")

    def get_holds(self) -> dict:
        """Return a copy of the current holds dictionary."""
        with self._lock:
            return dict(self._holds)

    def clear(self) -> None:
        """Clear all holds."""
        self.update([])

    def update(self, holds: list) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class AuroraBoardState(_BaseBoardState):
    """State of an Aurora-protocol board: lit LED positions + colours."""

    def update(self, holds: list[tuple[int, int, int, int]]) -> None:
        """Replace the entire board state with new hold data.

        Args:
            holds: List of (position, r, g, b) tuples from the Aurora
                protocol decoder.
        """
        self._replace({position: (r, g, b) for position, r, g, b in holds})


class MoonBoardState(_BaseBoardState):
    """State of a MoonBoard: lit grid cells + role + colour."""

    def update(self, holds: list[tuple[int, int, int, int, int, int]]) -> None:
        """Replace the entire board state with new hold data.

        Args:
            holds: List of (role_code, column, row, r, g, b) tuples from
                the MoonBoard protocol decoder. Each frame is a complete
                climb, so the previous state is fully replaced.
        """
        self._replace({
            (column, row): (role_code, r, g, b)
            for role_code, column, row, r, g, b in holds
        })
