from boards import board_for
from quantum_geometry import QuantumGeometry
from render.quantum_headless import render_holds_text
from board_state import QuantumLight


def test_all_models_have_independent_schematic_geometry() -> None:
    board = board_for("quantum")
    counts = {}
    for variant in board.variants:
        geometry = QuantumGeometry(variant)
        counts[variant.key] = len(geometry.diodes)
        assert geometry.aspect_ratio == variant.columns / variant.rows
        assert len(geometry.by_address16) == len(geometry.diodes)
        assert len(geometry.by_address32) == len(geometry.diodes)
    assert counts == {"xl": 657, "l": 657, "m": 657,
                      "s": 432, "belay": 657}


def test_known_diode_has_both_controller_address_forms() -> None:
    geometry = QuantumGeometry(board_for("quantum").variant_for("xl"))
    diode = geometry.by_address16[1001]
    assert diode.address32 == 0x01010001
    assert geometry.diode(diode.address32) == diode


def test_schematic_headless_renderer_uses_hold_roles() -> None:
    geometry = QuantumGeometry(board_for("quantum").variant_for("s"))
    address = geometry.diodes[0].address16
    text = render_holds_text(geometry, {
        address: QuantumLight(0, 255, 0, "start")})
    assert "S" in text
