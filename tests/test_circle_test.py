import math
import struct
from collections import Counter

import pytest

from orcaslicer_mcp import circle_test as ct


def _triangles(stl: bytes):
    (count,) = struct.unpack_from("<I", stl, 80)
    assert len(stl) == 84 + 50 * count
    rows = [struct.unpack_from("<12f", stl, 84 + 50 * i) for i in range(count)]
    return [(row[:3], (row[3:6], row[6:9], row[9:12])) for row in rows]


def _volume(triangles) -> float:
    total = 0.0
    for _, (a, b, c) in triangles:
        total += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                  + a[2] * (b[0] * c[1] - b[1] * c[0]))
    return total / 6


def _polygon_area(diameter: float) -> float:
    return ct.SEGMENTS / 2 * (diameter / 2) ** 2 * math.sin(2 * math.pi / ct.SEGMENTS)


def test_default_piece_is_two_closed_bodies_with_outward_faces():
    test = ct.build_circle_test()
    triangles = _triangles(test.stl)
    edges = Counter()
    for _, (a, b, c) in triangles:
        edges.update([(a, b), (b, c), (c, a)])
    assert all(count == 1 and edges[(end, start)] == 1 for (start, end), count in edges.items())

    length, depth = 113 + 6 * 2 * ct.MARGIN, 45 + 2 * ct.MARGIN
    holes = sum(_polygon_area(d) for d in ct.DEFAULT_DIAMETERS)
    expected = ((length * depth - holes) * ct.PLATE_THICKNESS
                + length * depth * ct.STRIP_THICKNESS + holes * ct.PEG_HEIGHT)
    assert _volume(triangles) == pytest.approx(expected, rel=1e-5)
    assert test.size_mm == (length, 2 * depth + ct.BODY_GAP, ct.STRIP_THICKNESS + ct.PEG_HEIGHT)
    for normal, (a, b, c) in triangles:
        assert math.sqrt(sum(x * x for x in normal)) == pytest.approx(1, abs=1e-5)


def test_circle_vertices_lie_on_the_nominal_radius():
    test = ct.build_circle_test([10])
    center = (5 + ct.MARGIN, 5 + ct.MARGIN)
    wall = {v for _, tri in _triangles(test.stl) for v in tri
            if 0 < v[2] < ct.PLATE_THICKNESS or math.hypot(v[0] - center[0], v[1] - center[1]) < 5.01}
    on_hole = [v for v in wall if v[1] < 10 + 2 * ct.MARGIN and v[2] in (0.0, ct.PLATE_THICKNESS)]
    assert len(on_hole) == 2 * ct.SEGMENTS
    assert all(math.hypot(x - center[0], y - center[1]) == pytest.approx(5, abs=1e-4) for x, y, _ in on_hole)


def test_diameters_are_sorted_and_default_when_omitted():
    assert ct.checked_diameters([20, 3.5, 8]) == (3.5, 8.0, 20.0)
    assert ct.checked_diameters(None) == ct.DEFAULT_DIAMETERS
    assert ct.checked_diameters([]) == ct.DEFAULT_DIAMETERS


@pytest.mark.parametrize("diameters,message", [
    ([0.5, 10], "between"), ([10, 120], "between"), ([10, float("nan")], "between"),
    ([5, 5], "different"), (list(range(1, 14)), "at most"), ([60, 70, 80, 90], "long")])
def test_unusable_diameters_are_refused(diameters, message):
    with pytest.raises(ct.CircleTestError, match=message):
        ct.build_circle_test(diameters)
