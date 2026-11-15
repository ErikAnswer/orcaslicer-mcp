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
