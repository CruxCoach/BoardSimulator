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


class QuantumLight:
    """Rendered Quantum diode, kept dependency-free for renderer callbacks."""

    __slots__ = ("r", "g", "b", "role", "route_id", "user_id")

    def __init__(self, r: int, g: int, b: int, role: str = "route",
                 route_id: str = "", user_id: str = "") -> None:
        self.r, self.g, self.b = r, g, b
        self.role = role
        self.route_id = route_id
        self.user_id = user_id

    def __eq__(self, other: object) -> bool:
        return isinstance(other, QuantumLight) and (
            self.r, self.g, self.b, self.role, self.route_id, self.user_id
        ) == (other.r, other.g, other.b, other.role,
              other.route_id, other.user_id)


QuantumHoldMap = dict[int, QuantumLight]


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


class QuantumBoardState(_BaseBoardState):
    """Controller-like persistent state for Quantum commands.

    Active routes are isolated by route/user identity, while editor layers
    (start/step/finish) are independent.  Reconnects reset transport parsing,
    not this controller state.
    """

    def __init__(self, all_addresses: tuple[int, ...]) -> None:
        super().__init__()
        self._all_addresses = all_addresses
        self._routes: dict[str, dict[int, QuantumLight]] = {}
        self._route_users: dict[str, str] = {}
        self._editor: dict[str, dict[int, QuantumLight]] = {}
        self._all_on: dict[int, QuantumLight] = {}

    def update(self, holds: list) -> None:
        self._replace(dict(holds))

    def clear(self) -> None:
        self._routes.clear()
        self._route_users.clear()
        self._editor.clear()
        self._all_on.clear()
        self._replace({})

    def _publish(self) -> None:
        merged: QuantumHoldMap = dict(self._all_on)
        for layer in self._routes.values():
            merged.update(layer)
        for layer in self._editor.values():
            merged.update(layer)
        self._replace(merged)

    def apply(self, action) -> None:
        """Apply a :class:`protocols.quantum.QuantumAction`."""
        name = action.command.name
        color = action.color
        if name in ("ACTIVATE_WALL", "ACTIVATE_WALL_LED_ID", "BOARD_SWIPE"):
            if name == "BOARD_SWIPE":
                for route in [r for r, user in self._route_users.items()
                              if user == action.user_id and r != action.route_id]:
                    self._routes.pop(route, None)
                    self._route_users.pop(route, None)
            if action.replace or action.route_id not in self._routes:
                self._routes[action.route_id] = {}
            layer = self._routes[action.route_id]
            for address in action.diodes:
                layer[address] = QuantumLight(*color, "route",
                                              action.route_id, action.user_id)
            self._route_users[action.route_id] = action.user_id
        elif name == "TURN_OFF_BY_ROUTE":
            self._routes.pop(action.route_id, None)
            self._route_users.pop(action.route_id, None)
        elif name == "TURN_OFF_BY_USER":
            for route in [r for r, user in self._route_users.items()
                          if user == action.user_id]:
                self._routes.pop(route, None)
                self._route_users.pop(route, None)
        elif name == "TURN_OFF_ALL":
            self._routes.clear()
            self._route_users.clear()
            self._editor.clear()
            self._all_on.clear()
        elif name == "CHANGE_ROUTE_PARAMS":
            layer = self._routes.get(action.route_id, {})
            for address, light in list(layer.items()):
                layer[address] = QuantumLight(*color, light.role,
                                              light.route_id, light.user_id)
        elif name == "TURN_ON_ALL":
            self._all_on = {
                address: QuantumLight(*color, "all")
                for address in self._all_addresses
            }
        elif name.startswith("SET_"):
            role = {"SET_START_HOLDS": "start", "SET_STEP_HOLDS": "step",
                    "SET_FINISH_HOLDS": "finish"}[name]
            if action.replace or role not in self._editor:
                self._editor[role] = {}
            self._editor[role].update({
                address: QuantumLight(*color, role)
                for address in action.diodes
            })
        # REQUEST_USER_ROUTE_LIST is intentionally state-neutral.
        self._publish()
