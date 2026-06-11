"""Tests for the thread-safe board states of both protocol families."""

import threading

from board_state import AuroraBoardState, MoonBoardState
from protocols.moonboard import ROLE_FINISH, ROLE_HAND, ROLE_START


class TestAuroraBoardState:
    def test_update_replaces_state(self) -> None:
        state = AuroraBoardState()
        state.update([(1, 0, 255, 0), (2, 255, 0, 0)])
        assert state.get_holds() == {1: (0, 255, 0), 2: (255, 0, 0)}

        state.update([(3, 0, 0, 255)])
        assert state.get_holds() == {3: (0, 0, 255)}

    def test_clear(self) -> None:
        state = AuroraBoardState()
        state.update([(1, 0, 255, 0)])
        state.clear()
        assert state.get_holds() == {}

    def test_callbacks_notified(self) -> None:
        state = AuroraBoardState()
        seen: list[dict] = []
        state.register_callback(seen.append)

        state.update([(1, 0, 255, 0)])
        state.clear()

        assert seen == [{1: (0, 255, 0)}, {}]

    def test_callback_exception_does_not_break_update(self) -> None:
        state = AuroraBoardState()
        state.register_callback(lambda holds: 1 / 0)
        seen: list[dict] = []
        state.register_callback(seen.append)

        state.update([(1, 0, 255, 0)])

        assert seen == [{1: (0, 255, 0)}]

    def test_get_holds_returns_copy(self) -> None:
        state = AuroraBoardState()
        state.update([(1, 0, 255, 0)])
        holds = state.get_holds()
        holds[99] = (1, 2, 3)
        assert 99 not in state.get_holds()


class TestMoonBoardState:
    def test_update_stores_holds_keyed_by_grid_cell(self) -> None:
        state = MoonBoardState()
        state.update([
            (ROLE_START, 0, 0, 0, 255, 0),
            (ROLE_HAND, 5, 9, 0, 0, 255),
        ])
        holds = state.get_holds()
        assert holds[(0, 0)] == (ROLE_START, 0, 255, 0)
        assert holds[(5, 9)] == (ROLE_HAND, 0, 0, 255)

    def test_update_replaces_previous_state(self) -> None:
        """Each frame is a complete climb — old holds are dropped."""
        state = MoonBoardState()
        state.update([(ROLE_START, 0, 0, 0, 255, 0)])
        state.update([(ROLE_FINISH, 10, 17, 255, 0, 0)])
        holds = state.get_holds()
        assert (0, 0) not in holds
        assert holds[(10, 17)] == (ROLE_FINISH, 255, 0, 0)

    def test_clear_empties_state(self) -> None:
        state = MoonBoardState()
        state.update([(ROLE_START, 0, 0, 0, 255, 0)])
        state.clear()
        assert state.get_holds() == {}

    def test_callbacks_fire_on_update(self) -> None:
        state = MoonBoardState()
        seen: list = []
        state.register_callback(lambda holds: seen.append(holds))
        state.update([(ROLE_HAND, 1, 1, 0, 0, 255)])
        assert len(seen) == 1
        assert seen[0] == {(1, 1): (ROLE_HAND, 0, 0, 255)}

    def test_callback_exception_does_not_break_others(self) -> None:
        state = MoonBoardState()
        seen: list = []

        def bad(_holds) -> None:
            raise RuntimeError("boom")

        state.register_callback(bad)
        state.register_callback(lambda holds: seen.append(holds))
        state.update([(ROLE_HAND, 1, 1, 0, 0, 255)])
        # The second callback still ran despite the first raising.
        assert len(seen) == 1

    def test_get_holds_returns_a_copy(self) -> None:
        state = MoonBoardState()
        state.update([(ROLE_HAND, 1, 1, 0, 0, 255)])
        holds = state.get_holds()
        holds[(9, 9)] = (ROLE_START, 0, 0, 0)
        # Mutating the returned dict must not affect internal state.
        assert (9, 9) not in state.get_holds()

    def test_concurrent_updates_are_safe(self) -> None:
        """Hammer update() from many threads — no crash, consistent state."""
        state = MoonBoardState()

        def worker(col: int) -> None:
            for _ in range(200):
                state.update([(ROLE_HAND, col, 0, 0, 0, 255)])

        threads = [threading.Thread(target=worker, args=(c,)) for c in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Exactly one hold survives (last writer wins); state is intact.
        assert len(state.get_holds()) == 1
