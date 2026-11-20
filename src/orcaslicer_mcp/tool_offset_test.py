"""Test pieces for the XY offset between two tool heads: two vernier scales, one along X and
one along Y, each made of a comb printed by the reference head and a facing comb printed by
the other head.

The reference teeth sit every REFERENCE_PITCH mm and the other head's teeth every
REFERENCE_PITCH + step mm, both counted from a shared centre tooth. With no offset only the
centre pair lines up; when the other head prints `d` mm away from where it should, the pair
that lines up is the one at index -d / step. The reference comb carries a long tab behind its
centre tooth and a short one behind tooth +PLUS_TAB_TOOTH, which tells the positive direction.

Each head gets its own binary STL holding its comb of both scales, in shared coordinates.

read_tool_offset turns the pair read on each scale back into that offset.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .circle_test import Triangle, binary_stl

DEFAULT_STEP = 0.05
MIN_STEP = 0.02
MAX_STEP = 0.2
TEETH_PER_SIDE = 10
REFERENCE_PITCH = 2.0
TOOTH_WIDTH = 1.0
TOOTH_LENGTH = 5.0
BAR_WIDTH = 3.0
END_MARGIN = 2.0
TAB_WIDTH = 2.0
ZERO_TAB_LENGTH = 3.0
PLUS_TAB_LENGTH = 1.5
PLUS_TAB_TOOTH = 5
THICKNESS = 1.0
SCALE_GAP = 5.0
SCALE_DEPTH = ZERO_TAB_LENGTH + 2 * (BAR_WIDTH + TOOTH_LENGTH)

Rect = tuple[float, float, float, float]


class ToolOffsetTestError(ValueError):
    pass


@dataclass(frozen=True)
class ToolOffsetTest:
    reference_stl: bytes
    tool_stl: bytes
    reference_center: tuple[float, float]
    tool_center: tuple[float, float]
    size_mm: tuple[float, float, float]
    step: float
    teeth_per_side: int
    range_mm: float


@dataclass(frozen=True)
class AxisOffset:
    reading: float
    offset_mm: float
    correction_mm: float
    at_scale_end: bool


@dataclass(frozen=True)
class ToolOffsetReading:
    step: float
    x: AxisOffset
    y: AxisOffset

    @property
    def lined_up(self) -> bool:
        return self.x.reading == 0 and self.y.reading == 0


def checked_step(step) -> float:
    value = DEFAULT_STEP if step is None else float(step)
    if not (math.isfinite(value) and MIN_STEP <= value <= MAX_STEP):
        raise ToolOffsetTestError(f"the step must be between {MIN_STEP:g} and {MAX_STEP:g} mm")
    return value


def scale_length(step: float) -> float:
    return 2 * (TEETH_PER_SIDE * (REFERENCE_PITCH + step) + TOOTH_WIDTH / 2 + END_MARGIN)


def _centered(center: float, width: float, low: float, high: float) -> Rect:
    return (center - width / 2, center + width / 2, low, high)


def reference_comb(step: float) -> list[Rect]:
    """Rectangles (along0, along1, across0, across1) of the reference comb of one scale."""
    middle = scale_length(step) / 2
    bar = ZERO_TAB_LENGTH
    teeth = bar + BAR_WIDTH
    rects = [(0.0, 2 * middle, bar, teeth),
             _centered(middle, TAB_WIDTH, 0.0, bar),
             _centered(middle + PLUS_TAB_TOOTH * REFERENCE_PITCH, TAB_WIDTH, bar - PLUS_TAB_LENGTH, bar)]
    rects += [_centered(middle + k * REFERENCE_PITCH, TOOTH_WIDTH, teeth, teeth + TOOTH_LENGTH)
              for k in range(-TEETH_PER_SIDE, TEETH_PER_SIDE + 1)]
    return rects


def tool_comb(step: float) -> list[Rect]:
    middle = scale_length(step) / 2
    teeth = ZERO_TAB_LENGTH + BAR_WIDTH + TOOTH_LENGTH
    bar = teeth + TOOTH_LENGTH
    rects = [(0.0, 2 * middle, bar, bar + BAR_WIDTH)]
    rects += [_centered(middle + k * (REFERENCE_PITCH + step), TOOTH_WIDTH, teeth, bar)
              for k in range(-TEETH_PER_SIDE, TEETH_PER_SIDE + 1)]
    return rects


def _as_y_scale(comb: list[Rect]) -> list[Rect]:
    shift = SCALE_DEPTH + SCALE_GAP
    return [(across0, across1, along0 + shift, along1 + shift) for along0, along1, across0, across1 in comb]


def _breaks(values) -> list[float]:
    return sorted({round(v, 6) for v in values})


def _extrude(rects: list[Rect], thickness: float) -> list[Triangle]:
    """Closed mesh of the union of rectangles that only touch along whole grid edges."""
    xs = _breaks(v for r in rects for v in r[:2])
    ys = _breaks(v for r in rects for v in r[2:])

    def filled(i: int, j: int) -> bool:
        if not (0 <= i < len(xs) - 1 and 0 <= j < len(ys) - 1):
            return False
        x, y = (xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2
        return any(x0 < x < x1 and y0 < y < y1 for x0, x1, y0, y1 in rects)

    triangles: list[Triangle] = []

    def quad(a, b, c, d) -> None:
        triangles.extend([(a, b, c), (a, c, d)])

    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            if not filled(i, j):
                continue
            x0, x1, y0, y1 = xs[i], xs[i + 1], ys[j], ys[j + 1]
            quad((x0, y0, thickness), (x1, y0, thickness), (x1, y1, thickness), (x0, y1, thickness))
            quad((x0, y0, 0.0), (x0, y1, 0.0), (x1, y1, 0.0), (x1, y0, 0.0))
            for neighbour, start, end in (((i, j - 1), (x0, y0), (x1, y0)), ((i + 1, j), (x1, y0), (x1, y1)),
                                          ((i, j + 1), (x1, y1), (x0, y1)), ((i - 1, j), (x0, y1), (x0, y0))):
                if not filled(*neighbour):
                    quad((*start, 0.0), (*end, 0.0), (*end, thickness), (*start, thickness))
    return triangles


def _center(rects: list[Rect]) -> tuple[float, float]:
    return ((min(r[0] for r in rects) + max(r[1] for r in rects)) / 2,
            (min(r[2] for r in rects) + max(r[3] for r in rects)) / 2)


def _piece(comb: list[Rect], header: bytes) -> tuple[bytes, tuple[float, float]]:
    y_scale = _as_y_scale(comb)
    return binary_stl(_extrude(comb, THICKNESS) + _extrude(y_scale, THICKNESS), header), _center(comb + y_scale)


def build_tool_offset_test(step=None) -> ToolOffsetTest:
    value = checked_step(step)
    reference_stl, reference_center = _piece(reference_comb(value), b"tool offset test, reference head")
    tool_stl, tool_center = _piece(tool_comb(value), b"tool offset test, other head")
    length = scale_length(value)
    return ToolOffsetTest(
        reference_stl=reference_stl,
        tool_stl=tool_stl,
        reference_center=reference_center,
        tool_center=tool_center,
        size_mm=(length, SCALE_DEPTH + SCALE_GAP + length, THICKNESS),
        step=value,
        teeth_per_side=TEETH_PER_SIDE,
        range_mm=round(TEETH_PER_SIDE * value, 6),
    )


def _plain(value: float) -> float:
    return round(value, 6) + 0.0


def _axis_offset(axis: str, reading, step: float) -> AxisOffset:
    try:
        value = float(reading)
    except (TypeError, ValueError) as e:
        raise ToolOffsetTestError(f"the {axis} reading must be a number of teeth, got {reading!r}") from e
    if not (math.isfinite(value) and abs(value) <= TEETH_PER_SIDE):
        raise ToolOffsetTestError(
            f"the {axis} reading must be between -{TEETH_PER_SIDE} and {TEETH_PER_SIDE} teeth, the range of the "
            f"test (\u00b1{TEETH_PER_SIDE * step:g} mm at a step of {step:g} mm), got {reading!r}; when no pair "
            "lines up the offset is beyond the test: run the printer's own offset calibration, or print the "
            "test again with a larger step")
    if value * 2 != round(value * 2):
        raise ToolOffsetTestError(
            f"the {axis} reading must be a whole tooth or a half, got {reading!r}; pick the pair that lines up "
            "best, or the half between two pairs that look equally good")
    offset = _plain(-value * step)
    return AxisOffset(_plain(value), offset, _plain(-offset), abs(value) == TEETH_PER_SIDE)


def read_tool_offset(x_reading, y_reading, step=None) -> ToolOffsetReading:
    value = checked_step(step)
    return ToolOffsetReading(value, _axis_offset("X", x_reading, value), _axis_offset("Y", y_reading, value))
