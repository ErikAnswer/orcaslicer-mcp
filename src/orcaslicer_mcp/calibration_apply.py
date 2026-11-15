"""Turn the value picked from a calibration test into filament preset changes.

The flow formulas match OrcaSlicer's own test generator (Plater.cpp,
adjust_settings_for_flowrate_calib): the classic test labels each block with a
percent modifier, the linear (YOLO) test with an absolute flow ratio offset.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

PA_TESTS = frozenset({"pa_line", "pa_pattern", "pa_tower"})
DIRECT_KEYS = {
    "temp_tower": "nozzle_temperature",
    "max_volumetric_speed": "filament_max_volumetric_speed",
    "retraction": "filament_retraction_length",
}
TESTS = PA_TESTS | {"flow_rate"} | DIRECT_KEYS.keys()
FLOW_RATIO_RANGE = (0.5, 1.5)


class CalibrationValueError(ValueError):
    pass


@dataclass
class CalibrationChanges:
    changes: dict[str, str]
    previous: dict[str, str | None]
    warnings: list[str] = field(default_factory=list)


def _fmt(value: float) -> str:
    return f"{round(value, 4):g}"


def _floats(raw: str | None) -> list[float]:
    if raw is None or str(raw).strip() in ("", "nil"):
        return []
    try:
        return [float(part) for part in str(raw).split(",")]
    except ValueError as e:
        raise CalibrationValueError(f"cannot read the stored value {raw!r}") from e


def _flow_ratio(stored: dict, value: float, linear: bool) -> str:
    current = _floats(stored.get("filament_flow_ratio"))
    if not current:
        raise CalibrationValueError("the preset has no filament_flow_ratio to adjust")
    scaled = [c + value if linear else c * (100 + value) / 100 for c in current]
    low, high = FLOW_RATIO_RANGE
    if any(not low <= s <= high for s in scaled):
        raise CalibrationValueError(
            f"flow ratio would become {', '.join(map(_fmt, scaled))}, outside {low}-{high}; "
            "check the modifier and the linear flag")
    return ",".join(_fmt(s) for s in scaled)


def _temperature_warnings(stored: dict, value: float) -> list[str]:
    low = _floats(stored.get("nozzle_temperature_range_low"))
    high = _floats(stored.get("nozzle_temperature_range_high"))
    if low and high and not low[0] <= value <= high[0]:
        return [f"{_fmt(value)} °C is outside the filament's recommended range "
                f"{_fmt(low[0])}-{_fmt(high[0])} °C"]
    return []


def calibration_changes(test: str, value: float, stored: dict, linear: bool = False) -> CalibrationChanges:
    if test == "vfa":
        raise CalibrationValueError(
            "vfa has no preset key to write: it shows which speeds to avoid, so lower the print "
            "preset's wall speeds below the band that shows artifacts instead")
    if not math.isfinite(value):
        raise CalibrationValueError(f"value must be a finite number, got {value!r}")
    if test not in TESTS:
        raise CalibrationValueError(f"unknown test {test!r}; expected one of {', '.join(sorted(TESTS))}")
    if test != "flow_rate" and value < 0:
        raise CalibrationValueError(f"{test} value must not be negative")

    warnings: list[str] = []
    if test in PA_TESTS:
        changes = {"pressure_advance": _fmt(value), "enable_pressure_advance": "1"}
    elif test == "flow_rate":
        changes = {"filament_flow_ratio": _flow_ratio(stored, value, linear)}
    else:
        key = DIRECT_KEYS[test]
        if test != "retraction" and value <= 0:
            raise CalibrationValueError(f"{test} value must be positive")
        changes = {key: _fmt(value)}
        if test == "temp_tower":
            warnings = _temperature_warnings(stored, value)
            warnings.append("nozzle_temperature_initial_layer is left as it is")
    if linear and test != "flow_rate":
        warnings.append("linear only applies to flow_rate and was ignored")
    previous = {k: stored.get(k) for k in changes}
    return CalibrationChanges(changes, previous, warnings)
