"""Test piece for the circle compensation calibration: a plate with through holes and a thin
strip with pegs of the same diameters, in one binary STL made of two separate bodies.

Each feature sits in its own rectangular cell. Circles are polygons whose vertices lie on the
nominal radius, which is how OrcaSlicer measures the diameter of an approximate circle.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

DEFAULT_DIAMETERS = (3.0, 5.0, 10.0, 20.0, 30.0, 45.0)
MIN_DIAMETER = 1.0
MAX_DIAMETER = 100.0
MAX_FEATURES = 12
MAX_LENGTH = 250.0
MARGIN = 3.0
BODY_GAP = 4.0
PLATE_THICKNESS = 4.0
STRIP_THICKNESS = 1.6
PEG_HEIGHT = 6.0
SEGMENTS = 180

Point2 = tuple[float, float]
Point3 = tuple[float, float, float]
Triangle = tuple[Point3, Point3, Point3]


class CircleTestError(ValueError):
    pass


@dataclass(frozen=True)
class CircleTest:
    stl: bytes
    diameters: tuple[float, ...]
    size_mm: tuple[float, float, float]


def checked_diameters(diameters) -> tuple[float, ...]:
    values = tuple(sorted(float(d) for d in (diameters or DEFAULT_DIAMETERS)))
    if not all(math.isfinite(d) and MIN_DIAMETER <= d <= MAX_DIAMETER for d in values):
        raise CircleTestError(f"every diameter must be between {MIN_DIAMETER:g} and {MAX_DIAMETER:g} mm")
    if len(set(values)) != len(values):
        raise CircleTestError("the diameters must all be different, so each feature can be told apart")
    if len(values) > MAX_FEATURES:
        raise CircleTestError(f"at most {MAX_FEATURES} diameters fit in one test piece")
    length = sum(d + 2 * MARGIN for d in values)
    if length > MAX_LENGTH:
        raise CircleTestError(
            f"these diameters need a piece {length:g} mm long, over the {MAX_LENGTH:g} mm limit; use fewer or smaller ones")
    return values


def _circle(center: Point2, radius: float) -> list[Point2]:
    return [(center[0] + radius * math.cos(2 * math.pi * i / SEGMENTS),
             center[1] + radius * math.sin(2 * math.pi * i / SEGMENTS)) for i in range(SEGMENTS)]


def _cell_around_circle(x0: float, x1: float, y0: float, y1: float, circle: list[Point2]) -> list[tuple[Point2, ...]]:
    """Counter-clockwise triangles covering the rectangle minus the circle, using only the four
    corners on the outside so that neighbouring cells share whole edges."""
    corners = [(x1, y1), (x0, y1), (x0, y0), (x1, y0)]
    quarter = SEGMENTS // 4
    triangles = []
    for k, corner in enumerate(corners):
        for i in range(k * quarter, (k + 1) * quarter):
            triangles.append((corner, circle[(i + 1) % SEGMENTS], circle[i]))
        triangles.append((corner, corners[(k + 1) % 4], circle[((k + 1) * quarter) % SEGMENTS]))
    return triangles


def _flat(triangles, z: float, up: bool) -> list[Triangle]:
    lifted = [tuple((x, y, z) for x, y in triangle) for triangle in triangles]
    return lifted if up else [(a, c, b) for a, b, c in lifted]


def _wall(loop: list[Point2], z0: float, z1: float, outward: bool) -> list[Triangle]:
    """Vertical wall along a counter-clockwise loop, facing away from its inside when outward."""
    triangles = []
    for i, (ax, ay) in enumerate(loop):
        bx, by = loop[(i + 1) % len(loop)]
        quad = [((ax, ay, z0), (bx, by, z0), (bx, by, z1)), ((ax, ay, z0), (bx, by, z1), (ax, ay, z1))]
        triangles += quad if outward else [(a, c, b) for a, b, c in quad]
    return triangles


def _body(edges: list[float], y0: float, y1: float, thickness: float, diameters, pegs: bool) -> list[Triangle]:
    triangles = []
    for x0, x1, diameter in zip(edges, edges[1:], diameters):
        center = ((x0 + x1) / 2, (y0 + y1) / 2)
        circle = _circle(center, diameter / 2)
        cell = _cell_around_circle(x0, x1, y0, y1, circle)
        triangles += _flat(cell, thickness, up=True)
        if pegs:
            top = thickness + PEG_HEIGHT
            triangles += _flat([((x0, y0), (x1, y0), (x1, y1)), ((x0, y0), (x1, y1), (x0, y1))], 0.0, up=False)
            triangles += _wall(circle, thickness, top, outward=True)
            triangles += _flat([(center, a, b) for a, b in zip(circle, circle[1:] + circle[:1])], top, up=True)
        else:
            triangles += _flat(cell, 0.0, up=False)
            triangles += _wall(circle, 0.0, thickness, outward=False)
    outline = [(x, y0) for x in edges] + [(x, y1) for x in reversed(edges)]
    return triangles + _wall(outline, 0.0, thickness, outward=True)


def _normal(a: Point3, b: Point3, c: Point3) -> Point3:
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    length = math.sqrt(sum(x * x for x in n))
    return (n[0] / length, n[1] / length, n[2] / length) if length else (0.0, 0.0, 0.0)


def _binary_stl(triangles: list[Triangle]) -> bytes:
    out = bytearray(b"circle compensation test".ljust(80, b" "))
    out += struct.pack("<I", len(triangles))
    for a, b, c in triangles:
        out += struct.pack("<12fH", *_normal(a, b, c), *a, *b, *c, 0)
    return bytes(out)


def build_circle_test(diameters=None) -> CircleTest:
    values = checked_diameters(diameters)
    edges = [0.0]
    for diameter in values:
        edges.append(edges[-1] + diameter + 2 * MARGIN)
    depth = values[-1] + 2 * MARGIN
    triangles = _body(edges, 0.0, depth, PLATE_THICKNESS, values, pegs=False)
    triangles += _body(edges, depth + BODY_GAP, 2 * depth + BODY_GAP, STRIP_THICKNESS, values, pegs=True)
    size = (edges[-1], 2 * depth + BODY_GAP, STRIP_THICKNESS + PEG_HEIGHT)
    return CircleTest(_binary_stl(triangles), values, size)
