"""Fit the circle compensation coefficients of a filament to caliper measurements.

The model is the one of CircleCompensation.cpp in the fork: radial correction =
coef_1 x speed + coef_2 x diameter + coef_3, positive = bigger, for holes
(hole_coef_*) and for contours (counter_coef_*). A feature measured smaller than
nominal needs (nominal - measured) / 2 more radius; one measured bigger, less.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .calibration_apply import CalibrationChanges, CalibrationValueError, _floats

PREFIXES = {"hole": "hole", "peg": "counter"}
SPEED_KEY = "circle_compensation_speed"
DIAMETER_LIMIT_KEY = "diameter_limit"
DEFAULT_LIMITS = (-0.5, 0.5)
MAX_RADIAL_ERROR_MM = 1.0
POOR_FIT_MM = 0.05
_SINGULAR = 1e-9


@dataclass(frozen=True)
class CircleMeasurement:
    kind: str
    nominal: float
    measured: float
    speed: float | None = None


def coefficient_keys(kind: str) -> tuple[str, str, str]:
    prefix = PREFIXES[kind]
    return f"{prefix}_coef_1", f"{prefix}_coef_2", f"{prefix}_coef_3"


def limit_keys(kind: str) -> tuple[str, str]:
    prefix = PREFIXES[kind]
    return f"{prefix}_limit_min", f"{prefix}_limit_max"


def _fmt(value: float, decimals: int = 6) -> str:
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _check(measurements: list[CircleMeasurement]) -> None:
    if not measurements:
        raise CalibrationValueError("circle_compensation needs at least one measurement")
    for m in measurements:
        if m.kind not in PREFIXES:
            raise CalibrationValueError(f"unknown kind {m.kind!r}; expected hole or peg")
        numbers = [m.nominal, m.measured] + ([] if m.speed is None else [m.speed])
        if any(not math.isfinite(n) or n <= 0 for n in numbers):
            raise CalibrationValueError(
                f"nominal, measured and speed must be positive numbers, got {m.kind} {m.nominal!r} "
                f"measured {m.measured!r}" + ("" if m.speed is None else f" at {m.speed!r} mm/s"))
        if abs(m.nominal - m.measured) / 2 > MAX_RADIAL_ERROR_MM:
            raise CalibrationValueError(
                f"{m.kind} {_fmt(m.nominal)} mm measured {_fmt(m.measured)} mm is more than "
                f"{_fmt(2 * MAX_RADIAL_ERROR_MM)} mm off; check the measurement")
    for kind in PREFIXES:
        with_speed = {m.speed is not None for m in measurements if m.kind == kind}
        if len(with_speed) > 1:
            raise CalibrationValueError(
                f"give the speed for every {kind} measurement or for none of them")


def _solve(rows: list[list[float]], targets: list[float]) -> list[float] | None:
    size = len(rows[0])
    scales = [max(abs(row[i]) for row in rows) or 1.0 for i in range(size)]
    scaled = [[row[i] / scales[i] for i in range(size)] for row in rows]
    matrix = [[sum(r[i] * r[j] for r in scaled) for j in range(size)]
              + [sum(r[i] * t for r, t in zip(scaled, targets))] for i in range(size)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda r: abs(matrix[r][col]))
        if abs(matrix[pivot][col]) < _SINGULAR * len(rows):
            return None
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        for row in range(size):
            if row != col:
                factor = matrix[row][col] / matrix[col][col]
                matrix[row] = [a - factor * b for a, b in zip(matrix[row], matrix[col])]
    return [matrix[i][size] / matrix[i][i] / scales[i] for i in range(size)]


def _fit(points: list[CircleMeasurement], warnings: list[str]) -> tuple[float, float, float]:
    kind = points[0].kind
    targets = [(m.nominal - m.measured) / 2 for m in points]
    by_speed = len({m.speed for m in points}) > 1
    by_diameter = len({m.nominal for m in points}) > 1

    def solve(with_speed: bool) -> list[float] | None:
        rows = [([m.speed] if with_speed else []) + ([m.nominal] if by_diameter else []) + [1.0]
                for m in points]
        return _solve(rows, targets)

    solution = solve(by_speed)
    if solution is None:
        warnings.append(
            f"{kind}: speed and diameter change together in the measurements, so their effects cannot "
            "be told apart; the speed term was left at 0. Measure several diameters at each speed to fit it")
        by_speed = False
        solution = solve(False)
    terms = iter(solution)
    speed_coef = next(terms) if by_speed else 0.0
    diameter_coef = next(terms) if by_diameter else 0.0
    return speed_coef, diameter_coef, next(terms)


def _first(stored: dict, key: str, default: float) -> float:
    values = _floats(stored.get(key))
    return values[0] if values else default


def _write(stored: dict, key: str, text: str) -> str:
    return ",".join([text] * max(1, len(_floats(stored.get(key)))))


def _model(coefs: tuple[float, float, float], speed: float, diameter: float) -> float:
    return coefs[0] * speed + coefs[1] * diameter + coefs[2]


def _speed_warnings(kind: str, points: list[CircleMeasurement], stored: dict) -> list[str]:
    speeds = [m.speed for m in points if m.speed is not None]
    slicer_speed = _first(stored, SPEED_KEY, 0.0)
    if not speeds or slicer_speed <= 0 or min(speeds) <= slicer_speed <= max(speeds):
        return []
    measured = _fmt(speeds[0]) if min(speeds) == max(speeds) else f"{_fmt(min(speeds))}-{_fmt(max(speeds))}"
    return [f"{kind}: measured at {measured} mm/s, but compensated circles print at {SPEED_KEY} = "
            f"{_fmt(slicer_speed)} mm/s; set it to the speed of the test or print the test at that speed"]


def circle_compensation_changes(measurements: list[CircleMeasurement], stored: dict,
                                add_to_stored: bool = False) -> CalibrationChanges:
    _check(measurements)
    changes: dict[str, str] = {}
    warnings: list[str] = []
    fits: dict[str, dict] = {}
    slicer_speed = _first(stored, SPEED_KEY, 0.0)
    diameter_limit = _first(stored, DIAMETER_LIMIT_KEY, math.inf)
    for kind in PREFIXES:
        points = [m for m in measurements if m.kind == kind]
        if not points:
            continue
        fitted = _fit(points, warnings)
        residuals = [(m.nominal - m.measured) / 2 - _model(fitted, m.speed or 0.0, m.nominal) for m in points]
        keys = coefficient_keys(kind)
        base = tuple(_first(stored, key, 0.0) for key in keys) if add_to_stored else (0.0, 0.0, 0.0)
        texts = [_fmt(f + b) for f, b in zip(fitted, base)]
        final = tuple(float(t) for t in texts)
        changes.update({key: _write(stored, key, text) for key, text in zip(keys, texts)})

        diameters = sorted({m.nominal for m in points})
        corrections = {d: _model(final, slicer_speed, d) for d in diameters}
        for key, default, bound, outward in zip(limit_keys(kind), DEFAULT_LIMITS, (min, max),
                                                (math.floor, math.ceil)):
            limit = _first(stored, key, default)
            reach = bound(corrections.values())
            if bound(limit, reach) != limit:
                changes[key] = _write(stored, key, _fmt(outward(round(reach * 100, 6)) / 100))

        worst = max(abs(r) for r in residuals)
        if worst > POOR_FIT_MM:
            warnings.append(f"{kind}: the fit misses a measurement by {_fmt(worst, 3)} mm of radius; "
                            "check the measurements before trusting it")
        beyond = [d for d in diameters if d > diameter_limit]
        if beyond:
            warnings.append(f"{kind}: {', '.join(map(_fmt, beyond))} mm is above {DIAMETER_LIMIT_KEY} = "
                            f"{_fmt(diameter_limit)} mm, so circles that big are not compensated")
        warnings.extend(_speed_warnings(kind, points, stored))
        uneven = [key for key in (*keys, *limit_keys(kind)) if len(set(_floats(stored.get(key)))) > 1]
        if uneven:
            warnings.append(f"{kind}: {', '.join(uneven)} hold several different values in the preset; only "
                            "the first one was read and every value is written the same")
        fits[kind] = {
            "measurements": len(points),
            "fitted": dict(zip(keys, map(_fmt, fitted))),
            "max_residual_mm": float(_fmt(worst, 4)),
            "radial_correction_mm": [{"diameter_mm": d, "correction_mm": float(_fmt(c, 4))}
                                     for d, c in corrections.items()],
        }
    previous = {key: stored.get(key) for key in changes}
    return CalibrationChanges(changes, previous, warnings, fits)
