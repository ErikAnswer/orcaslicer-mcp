import json

import httpx
import pytest
import respx

import orcaslicer_mcp.server as srv
from orcaslicer_mcp import client as cl

BASE = "http://x:13130"
MODEL = f"{BASE}/api/v1/model"
STATUS = f"{BASE}/api/v1/status"
PART_CONFIG = f"{BASE}/api/v1/objects/7/part_config"
FILES = ["http://impresora3d.casa/api/download?root=out&path=t/body.stl", "C:/models/gold.stl"]
LOADED = {"loaded": True, "single_object": True,
          "objects": [{"id": 7, "name": "body", "parts": [{"id": 11, "name": "body", "extruder": 0},
                                                          {"id": 12, "name": "gold", "extruder": 0}]}]}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ORCA_API_TOKEN", "tok")
    monkeypatch.setenv("ORCA_API_URL", BASE)
    monkeypatch.setattr(cl, "_BACKENDS", {})


def _status(capabilities):
    respx.get(STATUS).mock(return_value=httpx.Response(200, json={"capabilities": capabilities}))


@respx.mock
async def test_load_parts_sends_every_file_in_one_request():
    _status(["model", "object_parts"])
    route = respx.post(MODEL).mock(return_value=httpx.Response(200, json=LOADED))
    out = await srv.load_parts(FILES)
    assert [p["id"] for p in out["objects"][0]["parts"]] == [11, 12]
    assert json.loads(route.calls.last.request.read()) == {"parts": FILES}


@respx.mock
async def test_load_parts_on_an_older_build_loads_nothing():
    _status(["model"])
    route = respx.post(MODEL).mock(return_value=httpx.Response(400, json={"error": "missing_path"}))
    out = await srv.load_parts(FILES)
    assert "erik-v2.5.1-mcp.23" in out["error"]
    assert not route.called


@pytest.mark.parametrize("paths", [[], ["only.stl"], ["a.stl", ""], ["a.stl", 3], [f"{i}.stl" for i in range(33)]])
@respx.mock
async def test_load_parts_refuses_a_bad_list_before_anything_is_sent(paths):
    route = respx.post(MODEL).mock(return_value=httpx.Response(200, json=LOADED))
    out = await srv.load_parts(paths)
    assert out["error"] == "bad_parts"
    assert not route.called


@respx.mock
async def test_load_parts_relays_the_file_that_failed():
    _status(["model", "object_parts"])
    respx.post(MODEL).mock(return_value=httpx.Response(422, json={
        "error": "unsupported_format", "part": "C:/models/gold.3mf"}))
    out = await srv.load_parts(["C:/models/body.stl", "C:/models/gold.3mf"])
    assert "unsupported_format" in out["error"]
    assert out["part"] == "C:/models/gold.3mf"


@respx.mock
async def test_load_parts_takes_the_list_under_another_name():
    _status(["model", "object_parts"])
    route = respx.post(MODEL).mock(return_value=httpx.Response(200, json=LOADED))
    for name in ("paths", "parts", "files"):
        await srv.mcp.call_tool("load_parts", {name: FILES})
        assert json.loads(route.calls.last.request.read()) == {"parts": FILES}, name


@respx.mock
async def test_set_part_config_sends_the_part_and_its_settings():
    route = respx.put(PART_CONFIG).mock(return_value=httpx.Response(200, json={
        "id": 7, "applied": ["extruder"], "part": {"id": 12, "extruder": 2}}))
    out = await srv.set_part_config(7, 12, {"extruder": 2})
    assert out["part"]["extruder"] == 2
    assert json.loads(route.calls.last.request.read()) == {"part_id": 12, "config": {"extruder": 2}}


@respx.mock
async def test_set_part_config_on_an_older_build_says_which_build_it_needs():
    respx.put(PART_CONFIG).mock(return_value=httpx.Response(404, json={"error": "not_found"}))
    out = await srv.set_part_config(7, 12, {"extruder": 2})
    assert "erik-v2.5.1-mcp.23" in out["error"]


@respx.mock
async def test_set_part_config_keeps_the_message_of_an_unknown_part():
    respx.put(PART_CONFIG).mock(return_value=httpx.Response(404, json={"error": "unknown_part"}))
    out = await srv.set_part_config(7, 99, {"extruder": 2})
    assert "unknown_part" in out["error"]


@respx.mock
async def test_set_part_config_refuses_no_settings_before_anything_is_sent():
    route = respx.put(PART_CONFIG).mock(return_value=httpx.Response(200, json={}))
    out = await srv.set_part_config(7, 12, {})
    assert out["error"] == "config_must_be_object"
    assert not route.called


@respx.mock
async def test_set_part_config_refuses_an_out_of_range_setting_before_anything_is_sent():
    route = respx.put(PART_CONFIG).mock(return_value=httpx.Response(200, json={}))
    out = await srv.set_part_config(7, 12, {"sparse_infill_density": "250%"})
    assert "error" in out
    assert not route.called
