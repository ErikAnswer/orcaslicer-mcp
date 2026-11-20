import struct
from collections import Counter

import pytest

from orcaslicer_mcp import tool_offset_test as tt


def _triangles(stl: bytes):
    (count,) = struct.unpack_from("<I", stl, 80)
    assert len(stl) == 84 + 50 * count
    rows = [struct.unpack_from("<12f", stl, 84 + 50 * i) for i in range(count)]
    return [(row[3:6], row[6:9], row[9:12]) for row in rows]


def _volume(triangles) -> float:
    return sum(a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
               + a[2] * (b[0] * c[1] - b[1] * c[0]) for a, b, c in triangles) / 6


def _area(rects) -> float:
    return sum((x1 - x0) * (y1 - y0) for x0, x1, y0, y1 in rects)


def _tooth_centers(rects, across: float) -> list[float]:
    return sorted((along0 + along1) / 2 for along0, along1, across0, across1 in rects
                  if across0 < across < across1 and along1 - along0 == pytest.approx(tt.TOOTH_WIDTH))


@pytest.mark.parametrize("step", [None, 0.02, 0.2])
def test_both_pieces_are_closed_bodies_with_outward_faces(step):
    test = tt.build_tool_offset_test(step)
    for stl, comb in ((test.reference_stl, tt.reference_comb(test.step)), (test.tool_stl, tt.tool_comb(test.step))):
        triangles = _triangles(stl)
        edges = Counter()
        for a, b, c in triangles:
            edges.update([(a, b), (b, c), (c, a)])
        assert all(count == 1 and edges[(end, start)] == 1 for (start, end), count in edges.items())
        assert _volume(triangles) == pytest.approx(2 * _area(comb) * tt.THICKNESS, rel=1e-5)


def test_teeth_follow_the_vernier_pitches_from_a_shared_centre():
    step = 0.05
    middle = tt.scale_length(step) / 2
    tips = tt.ZERO_TAB_LENGTH + tt.BAR_WIDTH + tt.TOOTH_LENGTH
    reference = _tooth_centers(tt.reference_comb(step), tips - 1)
    tool = _tooth_centers(tt.tool_comb(step), tips + 1)
    assert len(reference) == len(tool) == 2 * tt.TEETH_PER_SIDE + 1
    for k, (ref, other) in enumerate(zip(reference, tool), start=-tt.TEETH_PER_SIDE):
        assert ref == pytest.approx(middle + k * tt.REFERENCE_PITCH)
        assert other - ref == pytest.approx(k * step)


def test_a_shifted_tool_lines_up_at_minus_shift_over_step():
    step, shift = 0.05, 0.15
    reference = _tooth_centers(tt.reference_comb(step), tt.ZERO_TAB_LENGTH + tt.BAR_WIDTH + 1)
    tool = [x + shift for x in _tooth_centers(tt.tool_comb(step), tt.SCALE_DEPTH - tt.BAR_WIDTH - 1)]
    gaps = [abs(other - ref) for ref, other in zip(reference, tool)]
    assert gaps.index(min(gaps)) - tt.TEETH_PER_SIDE == round(-shift / step)


def test_teeth_meet_tip_to_tip_and_tabs_mark_zero_and_plus():
    reference, tool = tt.reference_comb(0.05), tt.tool_comb(0.05)
    assert max(r[3] for r in reference) == min(r[2] for r in tool)
    middle = tt.scale_length(0.05) / 2
    tabs = sorted(((r[0] + r[1]) / 2, r[3] - r[2]) for r in reference if r[3] <= tt.ZERO_TAB_LENGTH)
    assert tabs == [(pytest.approx(middle), tt.ZERO_TAB_LENGTH),
                    (pytest.approx(middle + tt.PLUS_TAB_TOOTH * tt.REFERENCE_PITCH), tt.PLUS_TAB_LENGTH)]


def test_pieces_share_coordinates_and_report_their_centres():
    test = tt.build_tool_offset_test(0.05)
    length = tt.scale_length(0.05)
    assert test.size_mm == (length, tt.SCALE_DEPTH + tt.SCALE_GAP + length, tt.THICKNESS)
    assert test.step == 0.05 and test.teeth_per_side == 10 and test.range_mm == 0.5
    for stl, center in ((test.reference_stl, test.reference_center), (test.tool_stl, test.tool_center)):
        points = [v for triangle in _triangles(stl) for v in triangle]
        for axis in (0, 1):
            low, high = min(p[axis] for p in points), max(p[axis] for p in points)
            assert (low + high) / 2 == pytest.approx(center[axis], abs=1e-4)
        assert {p[2] for p in points} == {0.0, tt.THICKNESS}
    assert test.reference_stl[:16] == b"tool offset test" and test.tool_stl != test.reference_stl


def test_y_scale_is_the_x_scale_turned_onto_the_other_axis():
    test = tt.build_tool_offset_test(0.05)
    points = {v[:2] for triangle in _triangles(test.tool_stl) for v in triangle}
    shift = tt.SCALE_DEPTH + tt.SCALE_GAP
    x_scale = {p for p in points if p[1] <= tt.SCALE_DEPTH}
    y_scale = {(round(y - shift, 3), round(x, 3)) for x, y in points if y >= shift}
    assert {(round(x, 3), round(y, 3)) for x, y in x_scale} == y_scale


@pytest.mark.parametrize("step", [0.01, 0.25, float("nan")])
def test_steps_out_of_range_are_refused(step):
    with pytest.raises(tt.ToolOffsetTestError, match="between 0.02 and 0.2"):
        tt.build_tool_offset_test(step)
