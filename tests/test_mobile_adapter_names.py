"""Board names the Android app must never take for the phone's own name.

Android renames the whole phone to a board's scan name. The app records the
phone's own name first and refuses to record a board name as that original;
``tools/export_mobile.py`` derives the list of board names from the registry.
These tests pin that every name a session (or the mobile profile builders)
can advertise is covered and that ordinary phone names are not.
"""

import importlib.util
import os

import pytest

import config
from board_geometry import list_sizes
from boards import BOARDS
from protocols.session import create_session

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _exporter():
    spec = importlib.util.spec_from_file_location(
        "export_mobile", os.path.join(REPO_ROOT, "tools", "export_mobile.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rules():
    return _exporter().adapter_name_rules()


def is_board_name(rules, name):
    """Same matching as AdapterNameRules.isBoardName on Android."""
    name = name.strip()
    return any(name == r[1:] if r[0] == "=" else name.startswith(r[1:])
               for r in rules)


def all_session_names():
    for board in BOARDS.values():
        for variant in board.variants:
            sizes = (list_sizes(board, variant.product_id)
                     if board.protocol == "aurora" else [None])
            for size in sizes:
                yield create_session(board, variant, size.id if size else None,
                                     None, None).ble_name


def test_every_registry_name_is_covered(rules):
    names = set(all_session_names())
    assert len(names) >= 10
    for name in names:
        assert is_board_name(rules, name), name


@pytest.mark.parametrize("name", [
    "Kilter Board#0001@3", "Kilter Board#AB12CD34@2", "Kilter Board#p1@3",
    "Tension Board#p2@3", "Kilter Board", "So iLL Board", "MoonBoard",
    "QB_020000000001", "QBB_020000000001", "ShinyWall_STP_0001",
    "ShinyWall_STP_Climbbox1", "Shiny Wall ST0 (ShinyMini)",
])
def test_profile_variants_are_covered(rules, name):
    # Serial/API fields, Kilter-built boxes, typed Shiny Wall names and the
    # routing probe's names all come from the mobile profile builders.
    assert is_board_name(rules, name)


@pytest.mark.parametrize("name", [
    "Nokia 6.1", "Pixel 8", "Galaxy S24", "Kilter", "MoonBoard fan",
    "My Kilter Board", "Shiny Wall", "QB",
])
def test_phone_names_are_not_board_names(rules, name):
    assert not is_board_name(rules, name)


def test_rule_format(rules):
    assert rules and all(r[0] in "=^" and r[1:] for r in rules)
    assert f"^{config.SHINYWALL_NAME_PREFIX}" in rules
    assert config.shinywall_ble_name("STP").startswith(config.SHINYWALL_NAME_PREFIX)


def test_export_writes_the_rules(tmp_path, rules):
    _exporter().export(tmp_path)
    lines = (tmp_path / "adapter_names.txt").read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line and not line.startswith("#")] == rules
