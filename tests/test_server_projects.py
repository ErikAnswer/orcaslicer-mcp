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
async def test_clear_plate_defaults_to_the_current_plate():
    route = respx.post(f"{BASE}/plate/clear").mock(return_value=httpx.Response(
        200, json={"cleared": True, "all_plates": False, "removed": 3, "count": 1}))
    out = await srv.clear_plate()
    assert json.loads(route.calls.last.request.read()) == {"all_plates": False}
    assert out["removed"] == 3


@respx.mock
async def test_clear_plate_can_empty_every_plate():
    route = respx.post(f"{BASE}/plate/clear").mock(return_value=httpx.Response(
        200, json={"cleared": True, "all_plates": True, "removed": 4, "count": 0}))
    await srv.clear_plate(all_plates=True)
    assert json.loads(route.calls.last.request.read()) == {"all_plates": True}


@respx.mock
async def test_list_projects_passes_the_folder_listing_through():
    listing = {"folder": "C:/Users/e/Documents/OrcaSlicer MCP", "current": "",
               "projects": [{"name": "soporte", "size_bytes": 1024, "modified": 1}]}
    respx.get(f"{BASE}/projects").mock(return_value=httpx.Response(200, json=listing))
    assert await srv.list_projects() == listing


@respx.mock
async def test_save_project_sends_name_and_keeps_overwrite_off():
    route = respx.post(f"{BASE}/project/save").mock(return_value=httpx.Response(
        200, json={"saved": True, "name": "soporte"}))
    out = await srv.save_project("soporte")
    assert json.loads(route.calls.last.request.read()) == {"name": "soporte", "overwrite": False}
    assert out["saved"] is True


@respx.mock
async def test_save_project_reports_an_existing_project():
    respx.post(f"{BASE}/project/save").mock(return_value=httpx.Response(
        409, json={"error": "exists", "detail": "pass overwrite to replace it"}))
    out = await srv.save_project("soporte")
    assert out["error"].startswith("exists")


@respx.mock
async def test_open_project_does_not_discard_by_default():
    route = respx.post(f"{BASE}/project/open").mock(return_value=httpx.Response(
        409, json={"error": "unsaved_project"}))
    out = await srv.open_project("soporte")
    assert json.loads(route.calls.last.request.read()) == {"name": "soporte", "discard_unsaved": False}
    assert out["error"] == "unsaved_project"


@respx.mock
async def test_open_project_reports_the_objects():
    respx.post(f"{BASE}/project/open").mock(return_value=httpx.Response(
        200, json={"opened": True, "plates": 2, "objects": [{"id": 9, "name": "a.stl"}]}))
    out = await srv.open_project("soporte", discard_unsaved=True)
    assert out["objects"] == [{"id": 9, "name": "a.stl"}] and out["plates"] == 2


@respx.mock
async def test_open_project_refuses_post_process_with_its_reason():
    respx.post(f"{BASE}/project/open").mock(return_value=httpx.Response(
        403, json={"error": "post_process", "detail": "the project runs a post-processing script"}))
    out = await srv.open_project("raro")
    assert "post_process" in out["error"]


@respx.mock
async def test_older_builds_say_the_feature_needs_a_newer_fork():
    respx.post(f"{BASE}/plate/clear").mock(return_value=httpx.Response(404, json={"error": "not_found"}))
    out = await srv.clear_plate()
    assert "mcp.15" in out["error"]


def test_project_tools_are_annotated():
    tools = srv.mcp._tool_manager._tools
    assert tools["list_projects"].annotations.read_only_hint is True
    for name in ("clear_plate", "save_project", "open_project"):
        assert tools[name].annotations.destructive_hint is True, name


@respx.mock
async def test_unknown_project_is_not_mistaken_for_an_old_build():
    respx.post(f"{BASE}/project/open").mock(return_value=httpx.Response(
        404, json={"error": "unknown_project", "name": "nada"}))
    out = await srv.open_project("nada")
    assert "unknown_project" in out["error"] and "mcp.15" not in out["error"]


@respx.mock
async def test_invalid_name_is_reported():
    respx.post(f"{BASE}/project/save").mock(return_value=httpx.Response(
        422, json={"error": "invalid_name"}))
    out = await srv.save_project("CON")
    assert "invalid_name" in out["error"]
