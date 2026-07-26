"""The advertising rule behind the single/multi connection toggle.

A peripheral is connectable only WHILE it advertises, so this one predicate
decides which controller the simulator impersonates: an exclusive one like
real Aurora hardware (the mode CruxRelay exists for), or one that keeps a
slot open for the next app.
"""

from ble.peripheral import should_advertise


def test_idle_board_always_advertises():
    assert should_advertise(link_count=0, multi_connect=False)
    assert should_advertise(link_count=0, multi_connect=True)


def test_exclusive_board_goes_quiet_once_a_client_is_on():
    # This is what makes the app classify it as "one device at a time", and
    # what a real Kilter controller looks like from the outside.
    assert not should_advertise(link_count=1, multi_connect=False)
    assert not should_advertise(link_count=3, multi_connect=False)


def test_multi_connect_board_keeps_advertising_for_the_next_client():
    assert should_advertise(link_count=1, multi_connect=True)
    assert should_advertise(link_count=3, multi_connect=True)
