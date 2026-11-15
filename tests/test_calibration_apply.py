import json

import httpx
import pytest
import respx

import orcaslicer_mcp.server as srv
from orcaslicer_mcp.calibration_apply import CalibrationValueError, calibration_changes
from orcaslicer_mcp.client import OrcaClient
from orcaslicer_mcp.config import Config

BASE = "http://x:13130/api/v1"
STORED = {
    "filament_flow_ratio": "0.98", "pressure_advance": "0.02", "enable_pressure_advance": "0",
    "nozzle_temperature": "220", "nozzle_temperature_range_low": "190",
    "nozzle_temperature_range_high": "230", "filament_max_volumetric_speed": "21",
    "filament_retraction_length": "nil",
}


@pytest.mark.parametrize("test", ["pa_line", "pa_pattern", "pa_tower"])
def test_pa_sets_value_and_enables_it(test):
    plan = calibration_changes(test, 0.032, STORED)
    assert plan.changes == {"pressure_advance": "0.032", "enable_pressure_advance": "1"}
    assert plan.previous == {"pressure_advance": "0.02", "enable_pressure_advance": "0"}


@pytest.mark.parametrize("modifier,expected", [(5, "1.029"), (-10, "0.882"), (0, "0.98")])
def test_flow_percent_modifier_scales_current_ratio(modifier, expected):
    assert calibration_changes("flow_rate", modifier, STORED).changes == {"filament_flow_ratio": expected}


def test_flow_linear_modifier_is_added():
    assert calibration_changes("flow_rate", -0.035, STORED, linear=True).changes == {
        "filament_flow_ratio": "0.945"}


def test_flow_scales_every_value_of_a_list():
    plan = calibration_changes("flow_rate", 2, {"filament_flow_ratio": "1,0.95"})
    assert plan.changes == {"filament_flow_ratio": "1.02,0.969"}


def test_flow_refuses_an_implausible_result():
    with pytest.raises(CalibrationValueError, match="linear"):
        calibration_changes("flow_rate", 5, STORED, linear=True)


@pytest.mark.parametrize("stored,modifier,linear,expected", [
    ("1", -50, False, "0.5"), ("1", 50, False, "1.5"), ("1", -0.5, True, "0.5"), ("1", 0.5, True, "1.5")])
def test_flow_range_limits_are_accepted(stored, modifier, linear, expected):
    plan = calibration_changes("flow_rate", modifier, {"filament_flow_ratio": stored}, linear)
    assert plan.changes == {"filament_flow_ratio": expected}


@pytest.mark.parametrize("modifier", [-60, 60])
def test_flow_refuses_an_extreme_percent(modifier):
    with pytest.raises(CalibrationValueError, match="outside"):
        calibration_changes("flow_rate", modifier, STORED)


@pytest.mark.parametrize("test", ["pa_line", "flow_rate", "temp_tower", "max_volumetric_speed"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_are_refused(test, value):
    with pytest.raises(CalibrationValueError, match="finite"):
        calibration_changes(test, value, STORED)


def test_flow_needs_a_current_ratio():
    with pytest.raises(CalibrationValueError, match="filament_flow_ratio"):
        calibration_changes("flow_rate", 5, {})


def test_temperature_warns_outside_range_and_keeps_first_layer():
    plan = calibration_changes("temp_tower", 240, STORED)
    assert plan.changes == {"nozzle_temperature": "240"}
    assert any("outside" in w for w in plan.warnings)
    assert any("initial_layer" in w for w in plan.warnings)


def test_volumetric_speed_and_retraction_are_written_as_given():
    assert calibration_changes("max_volumetric_speed", 18.5, STORED).changes == {
        "filament_max_volumetric_speed": "18.5"}
    plan = calibration_changes("retraction", 0.8, STORED)
    assert plan.changes == {"filament_retraction_length": "0.8"}
    assert plan.previous == {"filament_retraction_length": "nil"}


@pytest.mark.parametrize("test,value", [("vfa", 100), ("bogus", 1), ("pa_line", -0.01),
                                        ("temp_tower", 0), ("max_volumetric_speed", 0)])
def test_refused_inputs(test, value):
    with pytest.raises(CalibrationValueError):
        calibration_changes(test, value, STORED)


def test_linear_is_ignored_with_a_warning_outside_flow():
    plan = calibration_changes("pa_pattern", 0.04, STORED, linear=True)
    assert any("linear" in w for w in plan.warnings)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(srv, "_client", lambda: OrcaClient(Config(base_url="http://x:13130", token="t", timeout=5)))


def _mock_stored():
    return respx.post(f"{BASE}/preset/config").mock(return_value=httpx.Response(200, json={"config": STORED}))


@respx.mock
async def test_dry_run_saves_nothing(client):
    _mock_stored()
    select = respx.put(f"{BASE}/preset").mock(return_value=httpx.Response(200, json={"selected": "PLA"}))
    out = await srv.apply_calibration("PLA", "flow_rate", 5, dry_run=True)
    assert out["dry_run"] is True and "physics" in out["note"]
    assert out["changes"] == {"filament_flow_ratio": "1.029"}
    assert out["previous"] == {"filament_flow_ratio": "0.98"}
    assert select.call_count == 0


@respx.mock
async def test_apply_saves_through_edit_preset(client):
    _mock_stored()
    select = respx.put(f"{BASE}/preset").mock(return_value=httpx.Response(200, json={"selected": "PLA"}))
    respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"config": STORED}))
    put_cfg = respx.put(f"{BASE}/config").mock(return_value=httpx.Response(
        200, json={"applied": ["pressure_advance", "enable_pressure_advance"]}))
    save = respx.post(f"{BASE}/preset/save").mock(return_value=httpx.Response(200, json={"saved": True}))
    out = await srv.apply_calibration("PLA", "pa_pattern", 0.036)
    assert json.loads(select.calls.last.request.read()) == {"type": "filament", "name": "PLA"}
    assert json.loads(put_cfg.calls.last.request.read()) == {
        "pressure_advance": "0.036", "enable_pressure_advance": "1"}
    assert save.call_count == 1
    assert out["saved"] == {"saved": True} and out["previous"]["pressure_advance"] == "0.02"


@respx.mock
async def test_physics_block_is_returned_with_the_changes(client):
    _mock_stored()
    respx.put(f"{BASE}/preset").mock(return_value=httpx.Response(200, json={"selected": "PLA"}))
    respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"config": {
        "retraction_length": "0.8", "filament_retraction_length": "nil"}}))
    save = respx.post(f"{BASE}/preset/save").mock(return_value=httpx.Response(200, json={"saved": True}))
    out = await srv.apply_calibration("PLA", "retraction", 9)
    assert out["error"] == "physics_blocked"
    assert out["changes"] == {"filament_retraction_length": "9"}
    assert save.call_count == 0


@respx.mock
async def test_invalid_value_is_refused_before_selecting(client):
    _mock_stored()
    select = respx.put(f"{BASE}/preset").mock(return_value=httpx.Response(200, json={"selected": "PLA"}))
    out = await srv.apply_calibration("PLA", "vfa", 120)
    assert out["error"] == "invalid_calibration_value"
    assert select.call_count == 0
