import json

import httpx
import pytest
import respx

import orcaslicer_mcp.server as srv
from orcaslicer_mcp.client import OrcaClient
from orcaslicer_mcp.config import Config

BASE = "http://x:13130/api/v1"
GESTOR = "http://192.168.1.17"
URL = f"{GESTOR}/api/download?root=scratch&path=circle-test-5-10%20%282%29.stl"


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(srv, "_client", lambda: OrcaClient(Config(base_url="http://x:13130", token="t", timeout=5)))
    monkeypatch.setenv("ORCA_GESTOR_URL", GESTOR)


def _gestor():
    respx.get(f"{GESTOR}/api/state").mock(return_value=httpx.Response(200, json={"mutationToken": "tok"}))
    return respx.post(f"{GESTOR}/api/upload").mock(return_value=httpx.Response(
        201, json={"file": {"path": "circle-test-5-10 (2).stl", "root": "scratch"}}))


def _slicer(config: dict):
    model = respx.post(f"{BASE}/model").mock(return_value=httpx.Response(
        200, json={"loaded": True, "objects": [{"index": 1, "name": "circle-test-5-10 (2).stl"}]}))
    respx.get(f"{BASE}/objects").mock(return_value=httpx.Response(
        200, json={"count": 2, "objects": [{"id": 4, "name": "cube"}, {"id": 9, "name": "circle-test-5-10 (2).stl"}]}))
    respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"config": config}))
    obj = respx.put(f"{BASE}/objects/9/config").mock(return_value=httpx.Response(200, json={"applied": [], "errors": {}}))
    return model, obj


@respx.mock
async def test_piece_is_uploaded_loaded_and_printed_uncorrected():
    upload = _gestor()
    model, obj = _slicer({"enable_circle_compensation": "1", "xy_hole_compensation": "0.05",
                          "xy_contour_compensation": "0", "layer_height": "0.2"})
    out = await srv.create_circle_calibration([10, 5], tool=2)

    request = upload.calls.last.request
    assert dict(request.url.params) == {"filename": "circle-test-5-10.stl", "scratch": "1"}
    assert request.headers["X-Cad3d-Token"] == "tok" and request.read()[:24] == b"circle compensation test"
    assert json.loads(model.calls.last.request.read()) == {"url": URL}
    assert json.loads(obj.calls.last.request.read()) == {"enable_circle_compensation": 0, "extruder": 2}
    assert out["objects"] == [9] and out["tool"] == 2 and out["url"] == URL
    assert out["circle_compensation"] == "off_for_this_object"
    assert out["compensation"]["xy_hole_compensation"] == "0.05" and "adds" in out["compensation"]["note"]
    assert out["features"] == [{"kind": "hole", "diameter_mm": 5.0}, {"kind": "hole", "diameter_mm": 10.0},
                               {"kind": "peg", "diameter_mm": 5.0}, {"kind": "peg", "diameter_mm": 10.0}]
    assert "caliper" in out["measure"]


@respx.mock
async def test_build_without_the_setting_writes_no_object_setting():
    _gestor()
    _, obj = _slicer({"xy_hole_compensation": "0", "xy_contour_compensation": "0"})
    out = await srv.create_circle_calibration([5, 10])
    assert out["circle_compensation"] == "not_in_this_build" and not obj.called
    assert out["compensation"] == {"xy_hole_compensation": "0", "xy_contour_compensation": "0"}
    assert "error" not in out


@respx.mock
async def test_refused_object_setting_keeps_the_piece_and_says_what_is_missing():
    _gestor()
    _, obj = _slicer({"enable_circle_compensation": "0"})
    obj.mock(return_value=httpx.Response(409, json={"error": "busy"}))
    out = await srv.create_circle_calibration([5, 10])
    assert out["error"] and out["objects"] == [9] and out["configured_objects"] == []
    assert "set_object_config" in out["detail"]


@respx.mock
async def test_gestor_failure_loads_nothing():
    respx.get(f"{GESTOR}/api/state").mock(side_effect=httpx.ConnectError("down"))
    model = respx.post(f"{BASE}/model").mock(return_value=httpx.Response(200, json={}))
    out = await srv.create_circle_calibration()
    assert out["error"] == "gestor_error" and "nothing was loaded" in out["detail"] and not model.called


@respx.mock
async def test_slicer_refusal_returns_the_url_for_a_retry():
    _gestor()
    respx.post(f"{BASE}/model").mock(return_value=httpx.Response(422, json={"error": "url_not_allowed"}))
    out = await srv.create_circle_calibration([5, 10])
    assert out["error"] and out["url"] == URL


async def test_bad_diameters_touch_nothing():
    out = await srv.create_circle_calibration([5, 5])
    assert out["error"] == "invalid_diameters" and "different" in out["detail"]


@respx.mock
async def test_piece_whose_object_id_cannot_be_read_is_reported():
    _gestor()
    _slicer({"enable_circle_compensation": "0"})
    respx.get(f"{BASE}/objects").mock(return_value=httpx.Response(200, json={"count": 0, "objects": []}))
    out = await srv.create_circle_calibration([5, 10])
    assert out["error"] == "object_not_found" and out["objects"] == [] and "list_objects" in out["detail"]
