import json

import httpx
import pytest
import respx

import orcaslicer_mcp.server as srv
from orcaslicer_mcp.calibration_apply import CalibrationValueError
from orcaslicer_mcp.circle_fit import CircleMeasurement, circle_compensation_changes
from orcaslicer_mcp.client import OrcaClient
from orcaslicer_mcp.config import Config

BASE = "http://x:13130/api/v1"
STORED = {
    "circle_compensation_speed": "200", "diameter_limit": "50",
    "hole_coef_1": "0", "hole_coef_2": "0", "hole_coef_3": "0",
    "counter_coef_1": "0", "counter_coef_2": "0", "counter_coef_3": "0",
    "hole_limit_min": "-0.5", "hole_limit_max": "0.5",
    "counter_limit_min": "-0.5", "counter_limit_max": "0.5",
}
DIAMETERS = (3, 5, 10, 20, 30, 45)


def _measured(kind, diameter, speed_coef=0.0, diameter_coef=0.0, constant=0.0, speed=None):
    correction = speed_coef * (speed or 0) + diameter_coef * diameter + constant
    return CircleMeasurement(kind, diameter, diameter - 2 * correction, speed)


def _coefs(plan, prefix):
    return [float(plan.changes[f"{prefix}_coef_{i}"]) for i in (1, 2, 3)]


def test_small_hole_asks_for_a_positive_correction_and_big_peg_for_a_negative_one():
    plan = circle_compensation_changes(
        [CircleMeasurement("hole", 10, 9.8), CircleMeasurement("peg", 10, 10.1)], STORED)
    assert plan.changes == {
        "hole_coef_1": "0", "hole_coef_2": "0", "hole_coef_3": "0.1",
        "counter_coef_1": "0", "counter_coef_2": "0", "counter_coef_3": "-0.05"}
    assert plan.previous["hole_coef_3"] == "0"


def test_several_diameters_fit_the_diameter_term_and_the_constant():
    holes = [_measured("hole", d, diameter_coef=0.002, constant=0.06) for d in DIAMETERS]
    pegs = [_measured("peg", d, diameter_coef=-0.001, constant=-0.02) for d in DIAMETERS]
    plan = circle_compensation_changes(holes + pegs, STORED)
    assert _coefs(plan, "hole") == pytest.approx([0, 0.002, 0.06], abs=1e-6)
    assert _coefs(plan, "counter") == pytest.approx([0, -0.001, -0.02], abs=1e-6)
    assert plan.fits["hole"]["max_residual_mm"] == 0
    assert plan.fits["hole"]["radial_correction_mm"][-1] == {"diameter_mm": 45, "correction_mm": 0.15}
    assert plan.warnings == []


def test_several_speeds_fit_the_speed_term():
    holes = [_measured("hole", d, speed_coef=0.0002, diameter_coef=0.001, constant=0.01, speed=v)
             for d in DIAMETERS for v in (100, 200, 300)]
    plan = circle_compensation_changes(holes, STORED)
    assert _coefs(plan, "hole") == pytest.approx([0.0002, 0.001, 0.01], abs=1e-6)
    assert plan.fits["hole"]["radial_correction_mm"][0] == {"diameter_mm": 3, "correction_mm": 0.053}
    assert "counter_coef_1" not in plan.changes


def test_noisy_measurements_get_the_least_squares_line():
    holes = [CircleMeasurement("hole", 10, 9.9), CircleMeasurement("hole", 10, 9.86),
             CircleMeasurement("hole", 20, 19.8), CircleMeasurement("hole", 20, 19.84)]
    plan = circle_compensation_changes(holes, STORED)
    assert _coefs(plan, "hole") == pytest.approx([0, 0.003, 0.03], abs=1e-6)
    assert plan.fits["hole"]["max_residual_mm"] == pytest.approx(0.01)


def test_speed_tied_to_diameter_leaves_the_speed_term_at_zero_with_a_warning():
    holes = [_measured("hole", d, diameter_coef=0.002, constant=0.05, speed=10 * d) for d in DIAMETERS]
    plan = circle_compensation_changes(holes, {})
    assert _coefs(plan, "hole") == pytest.approx([0, 0.002, 0.05], abs=1e-6)
    assert any("cannot be told apart" in w for w in plan.warnings)


def test_add_to_stored_sums_the_coefficients_the_filament_had():
    stored = STORED | {"hole_coef_1": "0.0001", "hole_coef_2": "0.001", "hole_coef_3": "0.05"}
    holes = [_measured("hole", d, diameter_coef=0.0005, constant=0.02) for d in DIAMETERS]
    assert _coefs(circle_compensation_changes(holes, stored), "hole") == pytest.approx([0, 0.0005, 0.02])
    plan = circle_compensation_changes(holes, stored, add_to_stored=True)
    assert _coefs(plan, "hole") == pytest.approx([0.0001, 0.0015, 0.07])
    assert plan.fits["hole"]["fitted"]["hole_coef_3"] == "0.02"


def test_limits_widen_only_when_the_correction_passes_them():
    inside = circle_compensation_changes([CircleMeasurement("hole", 10, 9.8)], STORED)
    assert not any("limit" in key for key in inside.changes)
    plan = circle_compensation_changes(
        [CircleMeasurement("hole", 10, 8.9), CircleMeasurement("peg", 10, 11.3)], STORED)
    assert plan.changes["hole_limit_max"] == "0.55" and "hole_limit_min" not in plan.changes
    assert plan.changes["counter_limit_min"] == "-0.65" and "counter_limit_max" not in plan.changes
    assert plan.previous["hole_limit_max"] == "0.5"


def test_limits_use_the_stored_speed_for_the_speed_term():
    stored = STORED | {"hole_coef_1": "0.003"}
    plan = circle_compensation_changes([CircleMeasurement("hole", 10, 10)], stored, add_to_stored=True)
    assert plan.changes["hole_limit_max"] == "0.6"


def test_a_stored_vector_keeps_its_length():
    stored = STORED | {"hole_coef_3": "0,0", "hole_limit_max": "0.5,0.5"}
    plan = circle_compensation_changes([CircleMeasurement("hole", 10, 8.9)], stored)
    assert plan.changes["hole_coef_3"] == "0.55,0.55" and plan.changes["hole_limit_max"] == "0.55,0.55"


def test_warnings_for_a_poor_fit_a_diameter_over_the_limit_and_another_speed():
    holes = [CircleMeasurement("hole", 10, 9.8), CircleMeasurement("hole", 20, 20.2),
             CircleMeasurement("hole", 30, 29.6), CircleMeasurement("hole", 60, 59.9)]
    plan = circle_compensation_changes(holes, STORED)
    assert any("misses a measurement" in w for w in plan.warnings)
    assert any("diameter_limit" in w and "60" in w for w in plan.warnings)
    at_speed = circle_compensation_changes([CircleMeasurement("peg", 10, 10.1, speed=60)], STORED)
    assert any("circle_compensation_speed = 200" in w for w in at_speed.warnings)
    assert at_speed.changes["counter_coef_1"] == "0"


@pytest.mark.parametrize("measurements,match", [
    ([], "at least one"),
    ([CircleMeasurement("slot", 10, 10)], "kind"),
    ([CircleMeasurement("hole", 0, 10)], "positive"),
    ([CircleMeasurement("hole", 10, float("nan"))], "positive"),
    ([CircleMeasurement("hole", 10, 10, speed=-5)], "positive"),
    ([CircleMeasurement("hole", 10, 7.5)], "check the measurement"),
    ([CircleMeasurement("hole", 10, 9.9, speed=100), CircleMeasurement("hole", 20, 19.9)], "every hole"),
])
def test_refused_measurements(measurements, match):
    with pytest.raises(CalibrationValueError, match=match):
        circle_compensation_changes(measurements, STORED)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(srv, "_client", lambda: OrcaClient(Config(base_url="http://x:13130", token="t", timeout=5)))


def _mock_stored(stored):
    respx.get(f"{BASE}/status").mock(return_value=httpx.Response(200, json={}))
    return respx.post(f"{BASE}/preset/config").mock(return_value=httpx.Response(200, json={"config": stored}))


MEASUREMENTS = [srv.CircleMeasurementInput(kind="hole", nominal=10, measured=9.8),
                srv.CircleMeasurementInput(kind="peg", nominal=10, measured=10.1)]


@respx.mock
async def test_dry_run_returns_the_fit_and_saves_nothing(client):
    _mock_stored(STORED)
    select = respx.put(f"{BASE}/preset").mock(return_value=httpx.Response(200, json={"selected": "PLA"}))
    out = await srv.apply_calibration("PLA", "circle_compensation", measurements=MEASUREMENTS, dry_run=True)
    assert out["dry_run"] is True and select.call_count == 0
    assert out["changes"]["hole_coef_3"] == "0.1" and out["changes"]["counter_coef_3"] == "-0.05"
    assert out["fit"]["peg"]["radial_correction_mm"] == [{"diameter_mm": 10, "correction_mm": -0.05}]
    assert "enable_circle_compensation" in out["enable"] and "circle_compensation" not in out


@respx.mock
async def test_saving_writes_the_coefficients_through_edit_preset(client):
    _mock_stored(STORED)
    respx.put(f"{BASE}/preset").mock(return_value=httpx.Response(200, json={"selected": "PLA"}))
    respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"config": STORED}))
    put_cfg = respx.put(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"applied": ["hole_coef_3"]}))
    save = respx.post(f"{BASE}/preset/save").mock(return_value=httpx.Response(200, json={"saved": True}))
    out = await srv.apply_calibration("PLA", "circle_compensation", measurements=MEASUREMENTS)
    assert json.loads(put_cfg.calls.last.request.read()) == {
        "hole_coef_1": "0", "hole_coef_2": "0", "hole_coef_3": "0.1",
        "counter_coef_1": "0", "counter_coef_2": "0", "counter_coef_3": "-0.05"}
    assert save.call_count == 1 and out["saved"] == {"saved": True} and out["fit"]["hole"]["measurements"] == 1


@respx.mock
async def test_a_build_without_the_settings_computes_on_dry_run_and_refuses_to_save_untouched(client):
    _mock_stored({"filament_flow_ratio": "0.98"})
    select = respx.put(f"{BASE}/preset").mock(return_value=httpx.Response(200, json={"selected": "PLA"}))
    dry = await srv.apply_calibration("PLA", "circle_compensation", measurements=MEASUREMENTS, dry_run=True)
    assert dry["changes"]["hole_coef_3"] == "0.1" and dry["circle_compensation"] == "not_in_this_build"
    assert "no circle compensation settings" in dry["note"]
    out = await srv.apply_calibration("PLA", "circle_compensation", measurements=MEASUREMENTS)
    assert out["error"] == "circle_compensation_not_in_this_build" and "update OrcaSlicer" in out["detail"]
    assert out["changes"]["hole_coef_3"] == "0.1" and select.call_count == 0


@respx.mock
async def test_missing_measurements_and_missing_value_are_refused(client):
    _mock_stored(STORED)
    out = await srv.apply_calibration("PLA", "circle_compensation", dry_run=True)
    assert out["error"] == "invalid_calibration_value" and "measurement" in out["detail"]
    out = await srv.apply_calibration("PLA", "pa_pattern", dry_run=True)
    assert out["error"] == "invalid_calibration_value" and "needs a value" in out["detail"]
