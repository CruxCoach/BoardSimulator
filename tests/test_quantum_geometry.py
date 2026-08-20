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
    geometry = QuantumGeometry(board_for("quantum").variant_for("xl"))
    diode = geometry.diodes[0]
    assert geometry.to_pixel(diode, 1000, 1000) == (
        diode.x * 9.321401938851603,
        (100.0 - diode.y) * 9.29368029739777,
    )


def test_headless_renderer_uses_hold_roles() -> None:
    geometry = QuantumGeometry(board_for("quantum").variant_for("s"))
    address = geometry.diodes[0].address16
    text = render_holds_text(geometry, {
        address: QuantumLight(0, 255, 0, "start")})
    assert "S" in text
