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


FOLDER = "C:\\Users\\e\\Documents\\OrcaSlicer MCP"
LISTING = {"folder": FOLDER, "current": "",
           "projects": [{"name": "soporte", "size_bytes": 1024, "modified": 1}]}


def _project(objects=1, modified=()):
    return httpx.Response(200, json={"name": "p", "path": "", "saved": False, "dirty": True,
                                     "plates": 1, "objects": objects, "modified": list(modified)})


@respx.mock
async def test_save_project_sends_the_path_in_the_projects_folder_and_keeps_overwrite_off():
    respx.get(f"{BASE}/project").mock(return_value=_project())
    respx.get(f"{BASE}/projects").mock(return_value=httpx.Response(200, json=LISTING))
    route = respx.post(f"{BASE}/project/save").mock(return_value=httpx.Response(
        200, json={"saved": FOLDER + "\\soporte.3mf", "bytes": 10}))
    out = await srv.save_project("soporte.3mf")
    assert json.loads(route.calls.last.request.read()) == {
        "path": FOLDER + "\\soporte.3mf", "overwrite": False}
    assert out["bytes"] == 10


@respx.mock
async def test_save_project_passes_overwrite():
    respx.get(f"{BASE}/project").mock(return_value=_project())
    respx.get(f"{BASE}/projects").mock(return_value=httpx.Response(200, json=LISTING))
    route = respx.post(f"{BASE}/project/save").mock(return_value=httpx.Response(200, json={"saved": "x"}))
    await srv.save_project("soporte", overwrite=True)
    assert json.loads(route.calls.last.request.read())["overwrite"] is True


@respx.mock
async def test_save_project_reports_an_existing_project():
    respx.get(f"{BASE}/project").mock(return_value=_project())
    respx.get(f"{BASE}/projects").mock(return_value=httpx.Response(200, json=LISTING))
    respx.post(f"{BASE}/project/save").mock(return_value=httpx.Response(
        409, json={"error": "exists", "detail": "a file is already there; pass overwrite:true to replace it"}))
    out = await srv.save_project("soporte")
    assert out["error"].startswith("exists")


@respx.mock
async def test_save_project_refuses_an_empty_project_without_saving():
    respx.get(f"{BASE}/project").mock(return_value=_project(objects=0))
    route = respx.post(f"{BASE}/project/save")
    out = await srv.save_project("soporte")
    assert out["error"] == "empty" and not route.called


@pytest.mark.parametrize("name", ["", "a/b", "..\\x", "C:\\x", "CON", "com1.3mf", "lpt9.bak",
                                  " x", "x.", "a" * 101, "x\ty", "q?", ".3mf"])
@respx.mock
async def test_save_and_open_accept_only_plain_names(name):
    save = respx.post(f"{BASE}/project/save")
    open_ = respx.post(f"{BASE}/project/open")
    assert (await srv.save_project(name))["error"] == "invalid_name"
    assert (await srv.open_project(name))["error"] == "invalid_name"
    assert not save.called and not open_.called


@respx.mock
async def test_open_project_does_not_discard_by_default():
    respx.get(f"{BASE}/projects").mock(return_value=httpx.Response(200, json=LISTING))
    respx.get(f"{BASE}/project").mock(return_value=_project())
    route = respx.post(f"{BASE}/project/open").mock(return_value=httpx.Response(
        409, json={"error": "unsaved_changes", "detail": "pass if_unsaved"}))
    out = await srv.open_project("Soporte")
    assert json.loads(route.calls.last.request.read()) == {
        "path": FOLDER + "\\Soporte.3mf", "if_unsaved": "fail", "presets": "project"}
    assert out["error"].startswith("unsaved_changes")


@respx.mock
async def test_open_project_discards_when_asked_and_reports_the_project():
    respx.get(f"{BASE}/projects").mock(return_value=httpx.Response(200, json=LISTING))
    respx.get(f"{BASE}/project").mock(return_value=_project())
    route = respx.post(f"{BASE}/project/open").mock(return_value=httpx.Response(
        200, json={"opened": FOLDER + "\\soporte.3mf", "plates": 2, "objects": 3}))
    out = await srv.open_project("soporte", discard_unsaved=True)
    assert json.loads(route.calls.last.request.read())["if_unsaved"] == "discard"
    assert out["plates"] == 2 and out["objects"] == 3


@respx.mock
async def test_open_project_refuses_unsaved_preset_edits_even_when_discarding():
    respx.get(f"{BASE}/projects").mock(return_value=httpx.Response(200, json=LISTING))
    respx.get(f"{BASE}/project").mock(return_value=_project(modified=["filament"]))
    route = respx.post(f"{BASE}/project/open")
    out = await srv.open_project("soporte", discard_unsaved=True)
    assert out["error"] == "unsaved_presets" and out["presets"] == ["filament"]
    assert not route.called


@respx.mock
async def test_open_project_refuses_a_project_outside_the_listing():
    respx.get(f"{BASE}/projects").mock(return_value=httpx.Response(200, json=LISTING))
    route = respx.post(f"{BASE}/project/open")
    out = await srv.open_project("nada")
    assert out == {"error": "unknown_project", "name": "nada"} and not route.called


@respx.mock
async def test_open_project_reports_the_policy_refusal():
    respx.get(f"{BASE}/projects").mock(return_value=httpx.Response(200, json=LISTING))
    respx.get(f"{BASE}/project").mock(return_value=_project())
    respx.post(f"{BASE}/project/open").mock(return_value=httpx.Response(
        403, json={"error": "blocked_by_remote_api_policy", "keys": ["post_process"],
                   "detail": "the project brings its own script"}))
    out = await srv.open_project("soporte")
    assert "blocked_by_remote_api_policy" in out["error"]


@respx.mock
async def test_older_builds_say_the_feature_needs_a_newer_fork():
    respx.post(f"{BASE}/plate/clear").mock(return_value=httpx.Response(404, json={"error": "not_found"}))
    out = await srv.clear_plate()
    assert "mcp.16" in out["error"]


@respx.mock
async def test_a_2_4_build_without_get_project_says_it_needs_2_5():
    respx.get(f"{BASE}/project").mock(return_value=httpx.Response(404, json={"error": "not_found"}))
    route = respx.post(f"{BASE}/project/save")
    out = await srv.save_project("soporte")
    assert "erik-v2.5.0-mcp.16" in out["error"] and not route.called


def test_project_tools_are_annotated():
    tools = srv.mcp._tool_manager._tools
    assert tools["list_projects"].annotations.read_only_hint is True
    for name in ("clear_plate", "save_project", "open_project"):
        assert tools[name].annotations.destructive_hint is True, name


def test_project_path_keeps_the_folder_separator():
    from orcaslicer_mcp.projects import project_path
    assert project_path("C:\\D\\OrcaSlicer MCP\\", "a") == "C:\\D\\OrcaSlicer MCP\\a.3mf"
    assert project_path("C:/D/OrcaSlicer MCP", "a") == "C:/D/OrcaSlicer MCP/a.3mf"
    assert project_path("/home/e/OrcaSlicer MCP", "a") == "/home/e/OrcaSlicer MCP/a.3mf"
