import json

import httpx
import pytest
import respx

import orcaslicer_mcp.server as srv
from orcaslicer_mcp.config import Config
from orcaslicer_mcp.client import OrcaClient

BASE = "http://x:13130/api/v1"


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(srv, "_client", lambda: OrcaClient(Config(base_url="http://x:13130", token="t", timeout=5)))


@respx.mock
async def test_range_calibration_sends_range_and_assigns_tool():
    calib = respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    obj = respx.put(f"{BASE}/objects/7/config").mock(return_value=httpx.Response(
        200, json={"applied": ["extruder"], "errors": {}}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002, tool=2)
    assert json.loads(calib.calls.last.request.read()) == {
        "type": "pa_tower", "discard_unsaved": False, "start": 0, "end": 0.08, "step": 0.002}
    assert json.loads(obj.calls.last.request.read()) == {"extruder": 2}
    assert out["assigned_objects"] == [7] and out["tool"] == 2


@respx.mock
async def test_flow_calibration_sends_pass_not_range():
    calib = respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": []}))
    await srv.create_calibration("flow_rate", flow_pass=2, linear=True)
    assert json.loads(calib.calls.last.request.read()) == {
        "type": "flow_rate", "discard_unsaved": False, "pass": 2, "linear": True}


@respx.mock
async def test_unsaved_project_is_reported_not_discarded():
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        409, json={"error": "unsaved_project"}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["error"] == "unsaved_project"


@respx.mock
async def test_not_created_says_the_project_was_replaced():
    detail = "the calibration test created no objects and the previous project was already replaced; the MCP panel has a restore point"
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        500, json={"error": "not_created", "project_replaced": True, "detail": detail}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002, discard_unsaved=True)
    assert out["error"] == f"not_created: {detail}"
    assert out["project_replaced"] is True
    assert out["detail"] == detail


@respx.mock
async def test_not_created_without_replacing_the_project():
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        500, json={"error": "not_created", "project_replaced": False,
                   "detail": "the calibration test created no objects"}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["project_replaced"] is False
    assert out["detail"] == "the calibration test created no objects"


@respx.mock
async def test_not_created_from_an_older_build_does_not_claim_nothing_changed():
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(422, json={"error": "not_created"}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["error"] == "not_created"
    assert out["project_replaced"] == "unknown"
    assert "get_project" in out["detail"]


@respx.mock
async def test_unsupported_on_belt_keeps_its_detail_and_no_replacement_flag():
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        422, json={"error": "unsupported_on_belt", "detail": "use pa_tower on a belt printer"}))
    out = await srv.create_calibration("pa_pattern", 0, 0.08, 0.002)
    assert out["error"] == "unsupported_on_belt: use pa_tower on a belt printer"
    assert out["detail"] == "use pa_tower on a belt printer"
    assert "project_replaced" not in out


@pytest.mark.parametrize("error", ["slicing", "busy", "job_running"])
@respx.mock
async def test_busy_refusals_are_reported_as_they_come(error):
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(409, json={"error": error}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["error"] == error
    assert "project_replaced" not in out


@respx.mock
async def test_send_to_queue_reports_upload_and_errors_keep_detail(monkeypatch):
    monkeypatch.setenv("ORCA_GESTOR_URL", "http://g")
    monkeypatch.setattr(srv, "QUEUE_CONFIRM_ATTEMPTS", 1)
    respx.get("http://g/api/state").mock(return_value=httpx.Response(200, json={"queue": {"jobs": []}}))
    route = respx.post(f"{BASE}/print/queue")
    route.mock(return_value=httpx.Response(202, json={"sent": True, "upload": {"gcodeToolCnt": "1"}}))
    assert (await srv.send_to_queue())["sent"] is True
    route.mock(return_value=httpx.Response(
        502, json={"error": "send_failed", "detail": "not every filament of the plate matches a loaded printer slot"}))
    out = await srv.send_to_queue()
    assert out["error"] == "send_failed: not every filament of the plate matches a loaded printer slot"
