"""Tests for the resizable-panel scaling math."""

import pytest

from render.layout import MIN_BOARD_HEIGHT, REFERENCE_HEIGHT, fit_board, scaled

# Aspect ratios of two real boards: Kilter 12x12-with-kickboard and a
# MoonBoard photo (portrait).
KILTER_ASPECT = 144 / 156
MOON_ASPECT = 0.62

SIZES = [(720, 750), (1400, 1200), (1920, 1080), (2560, 1440), (400, 320)]


@pytest.mark.parametrize("aspect", [KILTER_ASPECT, MOON_ASPECT])
@pytest.mark.parametrize("avail_w,avail_h", SIZES)
def test_board_fits_inside_available_area(aspect, avail_w, avail_h):
    w, h, off_x, off_y = fit_board(avail_w, avail_h, aspect)
    assert 0 < w <= avail_w
    assert 0 < h <= avail_h
    assert off_x >= 0 and off_y >= 0
    assert off_x + w <= avail_w
    assert off_y + h <= avail_h


@pytest.mark.parametrize("aspect", [KILTER_ASPECT, MOON_ASPECT])
@pytest.mark.parametrize("avail_w,avail_h", SIZES)
def test_aspect_ratio_is_preserved(aspect, avail_w, avail_h):
    w, h, _, _ = fit_board(avail_w, avail_h, aspect)
    assert w / h == pytest.approx(aspect, abs=0.01)


def test_board_is_centered():
    w, h, off_x, off_y = fit_board(1000, 600, KILTER_ASPECT)
    assert off_x == (1000 - w) // 2
    assert off_y == (600 - h) // 2


def test_wide_window_letterboxes_horizontally():
    """A window wider than the board keeps full height and pads the sides."""
    _, h, off_x, off_y = fit_board(2000, 800, KILTER_ASPECT)
    assert h == 800
    assert off_y == 0
    assert off_x > 0


def test_tall_window_letterboxes_vertically():
    """A window taller than the board keeps full width and pads top/bottom."""
    w, _, off_x, off_y = fit_board(600, 2000, KILTER_ASPECT)
    assert w == 600
    assert off_x == 0
    assert off_y > 0


def test_bigger_window_yields_a_bigger_board():
    _, small_h, _, _ = fit_board(720, 750, KILTER_ASPECT)
    _, large_h, _, _ = fit_board(1920, 1440, KILTER_ASPECT)
    assert large_h > small_h


def test_relative_hold_positions_are_size_independent():
    """A hold at 30% of the board stays at 30% at any window size."""
    small_w, _, small_x, _ = fit_board(720, 750, KILTER_ASPECT)
    large_w, _, large_x, _ = fit_board(1920, 1080, KILTER_ASPECT)
    small_rel = ((small_x + 0.3 * small_w) - small_x) / small_w
    large_rel = ((large_x + 0.3 * large_w) - large_x) / large_w
    assert small_rel == pytest.approx(large_rel)


def test_degenerate_sizes_do_not_crash():
    assert fit_board(0, 0, KILTER_ASPECT) == (0, 0, 0, 0)
    assert fit_board(-10, -10, KILTER_ASPECT) == (0, 0, 0, 0)
    w, h, _, _ = fit_board(100, 100, 0)
    assert (w, h) == (100, 100)


class TestScaledMetrics:
    def test_reference_height_is_identity(self):
        assert scaled(14, REFERENCE_HEIGHT) == pytest.approx(14)

    def test_metrics_grow_with_the_board(self):
        assert scaled(14, 1440) > scaled(14, REFERENCE_HEIGHT)

    def test_metrics_shrink_but_stay_visible(self):
        assert scaled(14, MIN_BOARD_HEIGHT) < 14
        assert scaled(3, MIN_BOARD_HEIGHT, minimum=1.0) >= 1.0
