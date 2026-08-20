"""Tests for the unified board registry (all seven boards, two families).

Identity fixtures (advertised names, scanner prefixes, layout/product
ids) are verified against CruxCoach 0.2.0's BoardBleScanner /
BoardConstants / MoonBoardVariant and the official apps'
led_kit_name_substring resources.
"""

import os

import pytest

import config
from boards import (
    BOARDS,
    PROTOCOL_AURORA,
    PROTOCOL_MOONBOARD,
    PROTOCOL_QUANTUM,
    AuroraVariant,
    MoonVariant,
    QuantumVariant,
    board_for,
)


def cruxcoach_normalise(name: str) -> str:
    """CruxCoach's BoardBleScanner name normalisation."""
    return name.lower().replace(" ", "").replace("-", "")


class TestRegistry:
    def test_all_eight_boards_present(self) -> None:
        assert sorted(BOARDS) == sorted([
            "kilter", "tension", "grasshopper", "decoy", "soill",
            "touchstone", "moonboard", "quantum",
        ])

    def test_protocol_families(self) -> None:
        aurora = {k for k, b in BOARDS.items() if b.protocol == PROTOCOL_AURORA}
        moon = {k for k, b in BOARDS.items() if b.protocol == PROTOCOL_MOONBOARD}
        quantum = {k for k, b in BOARDS.items() if b.protocol == PROTOCOL_QUANTUM}
        assert aurora == {"kilter", "tension", "grasshopper", "decoy",
                          "soill", "touchstone"}
        assert moon == {"moonboard"}
        assert quantum == {"quantum"}

    def test_variant_types_match_protocol(self) -> None:
        for board in BOARDS.values():
            expected = {PROTOCOL_AURORA: AuroraVariant,
                        PROTOCOL_MOONBOARD: MoonVariant,
                        PROTOCOL_QUANTUM: QuantumVariant}[board.protocol]
            for variant in board.variants:
                assert isinstance(variant, expected), (board.key, variant)

    def test_board_keys_are_unique_and_lowercase(self) -> None:
        for key, board in BOARDS.items():
            assert key == board.key == key.lower()

    def test_variant_keys_unique_per_board(self) -> None:
        for board in BOARDS.values():
            keys = [v.key for v in board.variants]
            assert len(keys) == len(set(keys)), board.key

    def test_aurora_boards_have_bundled_db(self) -> None:
        for board in BOARDS.values():
            if board.protocol == PROTOCOL_AURORA:
                assert os.path.isfile(board.db_path), board.key

    def test_moonboard_variants_have_bundled_assets(self) -> None:
        board = board_for("moonboard")
        for variant in board.variants:
            base = os.path.join(board.assets_dir, variant.asset_base)
            assert os.path.isfile(base + ".webp"), variant.key
            assert os.path.isfile(base + ".json"), variant.key

    def test_quantum_has_clean_geometry_fixture(self) -> None:
        assert os.path.isfile(os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "data", "quantum_geometry.json"))

    def test_only_kilter_has_two_leds_per_hold(self) -> None:
        # leds_per_hold resource in the official apps: Kilter alone has 2.
        for board in BOARDS.values():
            expected = 2 if board.key == "kilter" else 1
            assert board.leds_per_hold == expected, board.key


class TestSelection:
    def test_board_for_is_case_insensitive(self) -> None:
        assert board_for(" Kilter ").key == "kilter"

    def test_unknown_board_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown board"):
            board_for("spire")

    def test_default_variant_is_first(self) -> None:
        assert board_for("kilter").variant_for(None).key == "original"
        assert board_for("tension").variant_for(None).key == "tb1"
        assert board_for("moonboard").variant_for(None).key == "2016"

    def test_unknown_layout_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown layout"):
            board_for("moonboard").variant_for("2015")


class TestKilterIdentity:
    """Kilter constants mirror CruxCoach's BoardConstants.KILTER_*."""

    def test_original(self) -> None:
        v = board_for("kilter").variant_for("original")
        assert (v.layout_id, v.product_id, v.default_size_id) == (1, 1, 10)

    def test_homewall(self) -> None:
        v = board_for("kilter").variant_for("homewall")
        assert (v.layout_id, v.product_id, v.default_size_id) == (8, 7, 21)


class TestMoonVariants:
    """MoonBoard variants mirror CruxCoach's MoonBoardVariant."""

    def test_grid_rows(self) -> None:
        board = board_for("moonboard")
        rows = {v.key: v.grid_rows for v in board.variants}
        assert rows == {"2016": 18, "masters-2017": 18,
                        "masters-2019": 18, "2024": 18,
                        "mini-2020": 12}


class TestScannerCompatibility:
    """The advertised names must satisfy both scanner implementations."""

    @pytest.mark.parametrize("board_key", [
        k for k, b in BOARDS.items() if b.protocol == PROTOCOL_AURORA])
    def test_official_app_substring_match(self, board_key: str) -> None:
        # Official brand apps: name CONTAINS led_kit_name_substring
        # (case-sensitive).
        board = BOARDS[board_key]
        name = config.aurora_ble_name(board.display_name)
        assert board.official_filter in name

    @pytest.mark.parametrize("board_key", [
        k for k, b in BOARDS.items() if b.protocol == PROTOCOL_AURORA])
    def test_cruxcoach_prefix_match(self, board_key: str) -> None:
        # CruxCoach: parse Name#serial@apiLevel, normalise, prefix-match.
        board = BOARDS[board_key]
        name = config.aurora_ble_name(board.display_name)
        display, rest = name.split("#")
        serial, api = rest.split("@")
        assert cruxcoach_normalise(display).startswith(board.cruxcoach_prefix)
        assert serial == config.BOARD_SERIAL
        assert int(api) == config.API_LEVEL

    def test_aurora_prefixes_unambiguous(self) -> None:
        # No Aurora board's normalised name may prefix-match another
        # brand — CruxCoach checks tension/grasshopper/decoy/soill/
        # touchstone before falling back to kilter.
        for board in BOARDS.values():
            if board.protocol != PROTOCOL_AURORA:
                continue
            normalised = cruxcoach_normalise(board.display_name)
            for other in BOARDS.values():
                if other.protocol != PROTOCOL_AURORA or other is board:
                    continue
                if other.cruxcoach_prefix == "kilter":
                    continue  # kilter is the fallback, not a prefix rule
                assert not normalised.startswith(other.cruxcoach_prefix), (
                    board.key, other.key)

    def test_moonboard_name_is_bare_prefix(self) -> None:
        # MoonBoard scanners (official app + CruxCoach isMoonBoardName)
        # match the bare "MoonBoard" name prefix; an Aurora-style suffix
        # would still match but is not what real hardware advertises.
        assert config.MOONBOARD_BLE_NAME.startswith("MoonBoard")
        assert "#" not in config.MOONBOARD_BLE_NAME
        assert "@" not in config.MOONBOARD_BLE_NAME

    def test_quantum_name_has_mac_compatible_second_segment(self) -> None:
        name = config.quantum_ble_name("xl")
        prefix, identity = name.split("_")
        assert prefix == "QB"
        assert len(identity) == 12
        int(identity, 16)
        assert config.quantum_ble_name("belay").startswith("QBB_")

    def test_ble_names_fit_advertising_budget(self) -> None:
        # Complete Local Name AD structure: 2-byte header + name must fit
        # the 31-byte scan-response budget.
        for board in BOARDS.values():
            if board.protocol == PROTOCOL_AURORA:
                name = config.aurora_ble_name(board.display_name)
            elif board.protocol == PROTOCOL_MOONBOARD:
                name = config.MOONBOARD_BLE_NAME
            else:
                name = config.quantum_ble_name(board.default_variant.key)
            assert len(name.encode("utf-8")) <= 29, name
