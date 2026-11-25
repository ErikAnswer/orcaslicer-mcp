import json

import httpx
import pytest
import respx

import orcaslicer_mcp.server as srv
from orcaslicer_mcp import client as cl
from orcaslicer_mcp.client import OrcaClient
from orcaslicer_mcp.config import Config

BASE = "http://x:13130"
ROUTE = f"{BASE}/api/v1/objects/7/modifier"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ORCA_API_TOKEN", "tok")
    monkeypatch.setenv("ORCA_API_URL", BASE)
    monkeypatch.setattr(cl, "_BACKENDS", {})


@respx.mock
async def test_body_carries_only_what_was_given():
    route = respx.post(ROUTE).mock(return_value=httpx.Response(200, json={"id": 7, "modifier": {"id": 9}}))
    async with OrcaClient(Config(base_url=BASE, token="tok", timeout=5)) as c:
        await c.add_modifier(7, "cylinder", [10, 5])
    assert json.loads(route.calls.last.request.read()) == {"shape": "cylinder", "size_mm": [10, 5]}


@respx.mock
async def test_body_with_placement_and_settings():
    route = respx.post(ROUTE).mock(return_value=httpx.Response(200, json={"id": 7, "modifier": {"id": 9}}))
    out = await srv.add_modifier(7, "box", [4, 4, 2], {"extruder": 2}, [3, -1], 1.5)
    assert out["modifier"]["id"] == 9
    assert json.loads(route.calls.last.request.read()) == {
        "shape": "box", "size_mm": [4, 4, 2], "center_mm": [3, -1], "bottom_mm": 1.5, "config": {"extruder": 2}}


@respx.mock
async def test_older_build_says_which_build_it_needs():
    respx.post(ROUTE).mock(return_value=httpx.Response(404, json={"error": "not_found"}))
    out = await srv.add_modifier(7, "cylinder", [10, 5])
    assert "not available on this OrcaSlicer build" in out["error"]
    assert "erik-v2.5.1-mcp.22" in out["error"]


@respx.mock
async def test_unknown_object_keeps_its_message():
    respx.post(ROUTE).mock(return_value=httpx.Response(404, json={"error": "unknown_object"}))
    out = await srv.add_modifier(7, "cylinder", [10, 5])
    assert "unknown_object" in out["error"]


@respx.mock
async def test_refused_settings_are_relayed():
    respx.post(ROUTE).mock(return_value=httpx.Response(422, json={
        "error": "invalid_config", "errors": {"layer_height": "not_a_modifier_setting"}}))
    out = await srv.add_modifier(7, "cylinder", [10, 5], {"wall_loops": 3})
    assert out["errors"] == {"layer_height": "not_a_modifier_setting"}


@respx.mock
async def test_out_of_range_setting_is_refused_before_anything_is_sent():
    route = respx.post(ROUTE).mock(return_value=httpx.Response(200, json={"id": 7, "modifier": {"id": 9}}))
    out = await srv.add_modifier(7, "cylinder", [10, 5], {"sparse_infill_density": "250%"})
    assert "error" in out
    assert not route.called


@respx.mock
async def test_protected_key_is_refused_before_anything_is_sent(monkeypatch):
    monkeypatch.delenv("ORCA_MCP_ALLOW_KEYS", raising=False)
    respx.get(f"{BASE}/api/v1/status").mock(return_value=httpx.Response(200, json={"sensitive_keys_allowed": False}))
    respx.post(f"{BASE}/api/v1/activity/note").mock(return_value=httpx.Response(200, json={}))
    route = respx.post(ROUTE).mock(return_value=httpx.Response(200, json={"id": 7, "modifier": {"id": 9}}))
    out = await srv.add_modifier(7, "cylinder", [10, 5], {"post_process": "evil.exe"})
    assert "error" in out
    assert not route.called


@respx.mock
async def test_redacted_value_is_refused_before_anything_is_sent():
    route = respx.post(ROUTE).mock(return_value=httpx.Response(200, json={"id": 7, "modifier": {"id": 9}}))
    out = await srv.add_modifier(7, "cylinder", [10, 5], {"wall_loops": "<redacted>"})
    assert "error" in out
    assert not route.called


@respx.mock
async def test_settings_sent_under_another_name_still_reach_the_modifier():
    route = respx.post(ROUTE).mock(return_value=httpx.Response(200, json={"id": 7, "modifier": {"id": 9}}))
    for name in ("changes", "config", "settings"):
        await srv.mcp.call_tool("add_modifier", {"object_id": 7, "shape": "cylinder", "size_mm": [10, 5], name: {"extruder": 2}})
        assert json.loads(route.calls.last.request.read())["config"] == {"extruder": 2}, name
