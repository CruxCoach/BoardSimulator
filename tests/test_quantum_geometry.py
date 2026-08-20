import pytest

from boards import board_for
from quantum_geometry import QuantumGeometry
from render.quantum_headless import render_holds_text
from board_state import QuantumLight


def test_all_models_have_independent_image_geometry() -> None:
    board = board_for("quantum")
    counts = {}
    for variant in board.variants:
        geometry = QuantumGeometry(variant)
        counts[variant.key] = len(geometry.diodes)
        assert geometry.aspect_ratio == 1.0
        assert len(geometry.by_address16) == len(geometry.diodes)
        assert len(geometry.by_address32) == len(geometry.diodes)
    assert counts == {"xl": 657, "l": 549, "m": 432,
                      "s": 252, "belay": 1264}
    assert {v.key: v.catalog_type for v in board.variants} == {
        "xl": "big", "l": "medium", "m": "small",
        "s": "xsmall", "belay": "belay"}
    assert not any(v.hardware_verified for v in board.variants)


def test_known_diode_has_both_controller_address_forms() -> None:
    geometry = QuantumGeometry(board_for("quantum").variant_for("xl"))
    diode = geometry.by_address16[1001]
    assert diode.address32 == 0x01010001
    assert geometry.diode(diode.address32) == diode


def test_pixel_mapping_matches_ewalls_2014_renderer() -> None:
    expected = {
        "xl": (210.39186, 183.37204),
        "l": (210.24920, 183.10310),
        "m": (210.23896, 183.09776),
        "s": (167.43132, 200.47113),
        "belay": (213.24794, 389.51393),
    }
    for model, point in expected.items():
        geometry = QuantumGeometry(board_for("quantum").variant_for(model))
        diode = type(geometry.diodes[0])(
            address16=1, address32=1, kind=model, x=50.0, y=50.0)
        actual = geometry.to_pixel(diode, 400, 400)
        assert actual == pytest.approx(point, abs=.001)


def test_belay_tablet_and_right_middle_corrections() -> None:
    geometry = QuantumGeometry(board_for("quantum").variant_for("belay"))
    diode_type = type(geometry.diodes[0])
    center = diode_type(1, 1, "belay", 50.0, 50.0)
    right = diode_type(2, 2, "belay", 68.0, 50.0)
    assert geometry.to_pixel(center, 400, 400, tablet=True) == pytest.approx(
        (212.38509, 189.68692), abs=.001)
    assert geometry.to_pixel(right, 400, 400) == pytest.approx(
        (287.80563, 395.63393), abs=.001)


def test_headless_renderer_uses_hold_roles() -> None:
    geometry = QuantumGeometry(board_for("quantum").variant_for("s"))
    address = geometry.diodes[0].address16
    text = render_holds_text(geometry, {
        address: QuantumLight(0, 255, 0, "start")})
    assert "S" in text
