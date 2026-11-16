import json
import httpx, pytest, respx
import orcaslicer_mcp.server as srv
from orcaslicer_mcp.client import OrcaClient
from orcaslicer_mcp.config import load_config


def _env(m):
    m.setenv("ORCA_API_TOKEN", "tok"); m.setenv("ORCA_API_URL", "http://x:13130")


_PROJECT = {"name": "float_spinner", "path": "/u/float_spinner.3mf", "saved": True,
            "dirty": False, "plates": 4, "objects": 9}


def _body(route):
    return json.loads(route.calls.last.request.content)


# --- get_status: project folded into {name, path, dirty} -------------------------------

@respx.mock
async def test_get_status_folds_project_fields(monkeypatch):
    _env(monkeypatch)
    respx.get("http://x:13130/api/v1/status").mock(return_value=httpx.Response(200, json={
        "app": "OrcaSlicer", "project": "/u/float_spinner.3mf", "project_name": "float_spinner",
        "project_dirty": True, "presets": {"filaments": ["A"], "print": "P", "printer": "M"}}))
    out = await srv.get_status()
    assert out["project"] == {"name": "float_spinner", "path": "/u/float_spinner.3mf", "dirty": True}
    assert "project_name" not in out and "project_dirty" not in out


@respx.mock
async def test_get_status_old_build_keeps_project_string(monkeypatch):
    _env(monkeypatch)
    respx.get("http://x:13130/api/v1/status").mock(return_value=httpx.Response(200, json={
        "app": "OrcaSlicer", "project": "", "presets": {"filaments": [], "print": "P", "printer": "M"}}))
    out = await srv.get_status()
    assert out["project"] == ""


@respx.mock
async def test_get_status_unsaved_project_has_empty_path(monkeypatch):
    _env(monkeypatch)
    respx.get("http://x:13130/api/v1/status").mock(return_value=httpx.Response(200, json={
        "app": "OrcaSlicer", "project": "", "project_name": "Untitled", "project_dirty": False}))
    async with OrcaClient(load_config()) as c:
        out = await c.get_status()
    assert out["project"] == {"name": "Untitled", "path": "", "dirty": False}


@pytest.mark.parametrize("sent, name, expected", [
    ("C:\\Users\\e\\Documents\\OrcaSlicer MCP\\prueba-484", "prueba-484",
     "C:\\Users\\e\\Documents\\OrcaSlicer MCP\\prueba-484.3mf"),
    ("/u/prueba-484", "prueba-484", "/u/prueba-484.3mf"),
    ("/u/prueba-484.3mf", "prueba-484", "/u/prueba-484.3mf"),
    ("/u/part.3mf", "part.3mf", "/u/part.3mf.3mf"),
    ("/u/part.3mf.3mf", "part.3mf", "/u/part.3mf.3mf"),
    ("", "Untitled", ""),
])
@respx.mock
async def test_get_status_project_path_is_the_3mf_file(monkeypatch, sent, name, expected):
    _env(monkeypatch)
    respx.get("http://x:13130/api/v1/status").mock(return_value=httpx.Response(200, json={
        "app": "OrcaSlicer", "project": sent, "project_name": name, "project_dirty": False}))
    async with OrcaClient(load_config()) as c:
        out = await c.get_status()
    assert out["project"]["path"] == expected


# --- get_project -----------------------------------------------------------------------

@respx.mock
async def test_get_project(monkeypatch):
    _env(monkeypatch)
    respx.get("http://x:13130/api/v1/project").mock(return_value=httpx.Response(200, json=dict(
        _PROJECT, modified=["print"], recent_count=2)))
    out = await srv.get_project()
    assert out["dirty"] is False and out["modified"] == ["print"] and out["recent_count"] == 2


@respx.mock
async def test_get_project_needs_fork_15(monkeypatch):
    _env(monkeypatch)
    respx.get("http://x:13130/api/v1/project").mock(return_value=httpx.Response(404, json={"error": "not_found"}))
    out = await srv.get_project()
    assert "fork v2.4.2-mcp.15" in out["error"]


# --- new_project -----------------------------------------------------------------------

@respx.mock
async def test_new_project_default_fails_on_unsaved(monkeypatch):
    _env(monkeypatch)
    route = respx.post("http://x:13130/api/v1/project/new").mock(return_value=httpx.Response(200, json={
        "ok": True, "had_unsaved_changes": False, "kept_changes": [],
        "project": {"name": "Untitled", "path": "", "saved": False, "dirty": False, "plates": 1, "objects": 0}}))
    out = await srv.new_project()
    assert out["ok"] is True and out["project"]["name"] == "Untitled"
    assert _body(route) == {"if_unsaved": "fail"}


@respx.mock
async def test_new_project_with_name(monkeypatch):
    _env(monkeypatch)
    route = respx.post("http://x:13130/api/v1/project/new").mock(return_value=httpx.Response(200, json={
        "ok": True, "project": {"name": "bracket v2", "path": ""}}))
    out = await srv.new_project(if_unsaved="discard", name="bracket v2")
    assert out["project"]["name"] == "bracket v2"
    assert _body(route) == {"if_unsaved": "discard", "name": "bracket v2"}


def test_project_tool_annotations():
    tools = srv.mcp._tool_manager._tools
    for name in ("get_project", "list_projects"):
        assert tools[name].annotations.read_only_hint is True, name
    for name in ("open_project", "new_project", "save_project", "clear_plate"):
        ann = tools[name].annotations
        assert ann.read_only_hint is False and ann.destructive_hint is True, name
    for name in ("open_recent_project", "list_recent_projects", "forget_recent_project",
                 "browse_projects", "move_project_file", "delete_project_file"):
        assert name not in tools, name
