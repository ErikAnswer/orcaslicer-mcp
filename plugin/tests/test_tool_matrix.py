"""Every orcaslicer-mcp tool, called through the MCP server against the plugin (fake host).

Each tool must land in one of three buckets: it works, it says plainly that it needs the
OrcaSlicer MCP build, or it does not use OrcaSlicer at all (printer / knowledge tools). A tool that
answers with the generic "not available on this OrcaSlicer build (needs M4a)" means orcaslicer-mcp
has a path that does not know about the plugin. Run with ORCASLICER_MCP_SRC pointing at a checkout
that has the plugin-aware errors (orcaslicer-mcp branch plugin-update); skipped otherwise.
"""
import asyncio
import http.server
import json
import os
import pathlib
import sys
import threading
import urllib.parse

import pytest

try:
    from orcaslicer_mcp import errors as _errors
    HAS_PLUGIN_ERRORS = hasattr(_errors, "PLUGIN_LIMITS")
except ImportError:
    HAS_PLUGIN_ERRORS = False

pytestmark = pytest.mark.skipif(not HAS_PLUGIN_ERRORS, reason="orcaslicer-mcp without plugin-aware errors")

# Arguments for tools whose required parameters need a real-looking value.
ARGS = {
    "set_config": {"changes": {"layer_height": 0.24}},
    "select_preset": {"type": "print", "name": "My Fast"},
    "set_filament_slot_preset": {"slot": 1, "name": "Generic PLA"},
    "get_preset_config": {"type": "printer", "name": "Fake Printer 0.4"},
    "describe_setting": {"key": "layer_height"},
    "search_settings": {"query": "layer height"},
    "wait_for_printer": {"until": "heated"},
    "edit_preset": {"type": "print", "name": "My Fast", "changes": {"wall_loops": 3}},
    "rename_preset": {"type": "print", "old_name": "My Fast", "new_name": "My Faster"},
    "compare_slices": {"variants": [{"name": "a", "changes": {}}, {"name": "b", "changes": {"layer_height": 0.3}}]},
    "apply_calibration": {"filament": "Fake PLA", "test": "pa_pattern", "value": 0.04},
    "compare_settings": {"key": "layer_height", "values": [0.2, 0.3]},
    "create_tool_offset_calibration": {"reference_tool": 1, "tool": 2},
    "read_tool_offset_calibration": {"reference_tool": 1, "tool": 2, "x_reading": 2, "y_reading": -1.5},
    "add_modifier": {"object_id": 1, "shape": "cylinder", "size_mm": [10, 5]},
    "load_parts": {"paths": ["C:/models/body.stl", "C:/models/inlay.stl"]},
    "set_part_config": {"object_id": 1, "part_id": 2, "changes": {"extruder": 2}},
}
# Tools that talk to the printer, not OrcaSlicer (the fake printer address is unreachable).
PRINTER_TOOLS = {"check_printer_match", "get_printer_status", "list_print_history", "wait_for_printer"}
# Tools that upload to the Gestor 3D print queue before they reach OrcaSlicer (the fake Gestor refuses writes).
GESTOR_TOOLS = {"create_circle_calibration", "create_tool_offset_calibration"}
GESTOR_PRESET = {"kind": "process", "name": "Gestor Only", "path": "process/Gestor Only.json"}
GESTOR_PRESET_FILE = {"name": "Gestor Only", "type": "process", "inherits": "", "layer_height": "0.2"}
EXPECT_WORKS = {"get_status", "get_config", "set_config", "list_presets", "get_preset_config",
                "select_preset", "list_objects", "get_project", "sync_presets",
                # after the user pressed Slice plate (plugin 0.2.0)
                "get_slice_status", "get_slice_warnings", "get_gcode", "describe_plate", "save_gcode"}
READS_THE_SLICE = {"get_slice_status", "get_slice_warnings", "get_slice_breakdown", "get_gcode",
                   "describe_plate", "save_gcode"}
CUBE = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "cube_flat.gcode.xz"


def _fill(schema: dict) -> dict:
    out = {}
    props = schema.get("properties", {})
    for name in schema.get("required", []):
        t = props.get(name, {}).get("type")
        out[name] = {"string": "x", "integer": 0, "number": 0.2, "boolean": False, "object": {},
                     "array": []}.get(t, "x")
    return out


def _text(result) -> str:
    content = getattr(result, "content", None)
    if content is None:  # older SDKs return (content, structured) or a list
        content = result[0] if isinstance(result, tuple) else result
    return " ".join(getattr(c, "text", None) or json.dumps(c, default=str) for c in content)


class _FakeGestor(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        self.server.seen.append(("GET", url.path))
        wanted = urllib.parse.parse_qs(url.query).get("path")
        if url.path == "/api/state":
            self._answer(200, {"presets": [GESTOR_PRESET]})
        elif url.path == "/api/download" and wanted == [GESTOR_PRESET["path"]]:
            self._answer(200, GESTOR_PRESET_FILE)
        else:
            self._answer(404, {"error": "not found"})

    def do_POST(self):
        self.server.seen.append(("POST", urllib.parse.urlsplit(self.path).path))
        self._answer(404, {"error": "not found"})

    def _answer(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def gestor():
    """A Gestor 3D that only lists and serves one preset: no mutation token, every write refused."""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeGestor)
    httpd.seen = []
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    httpd.base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def test_every_tool_is_honest_about_the_plugin(folder, plugin, gestor, monkeypatch, capsys, tmp_path):
    import lzma
    from conftest import gcode_file, slice_now
    gcode = gcode_file(folder, lzma.open(CUBE, "rt").read())
    monkeypatch.setenv("PRINT_OUTCOMES_DIR", str(tmp_path / "outcomes"))
    monkeypatch.setenv("ORCA_API_URL", plugin.base)
    monkeypatch.setenv("ORCA_API_TOKEN", plugin._token)
    monkeypatch.setenv("ORCA_API_TIMEOUT", "15")
    monkeypatch.setenv("ORCA_GESTOR_URL", gestor.base)
    for k in ("ORCA_PRINTER_URL", "ORCA_PRINTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    from orcaslicer_mcp import server

    async def go():
        rows = {}
        for tool in await server.mcp.list_tools():
            args = ARGS.get(tool.name) or _fill(tool.input_schema)
            if tool.name in READS_THE_SLICE:  # earlier tools change settings: the user slices again first
                slice_now(plugin, gcode)
            try:
                text = _text(await server.mcp.call_tool(tool.name, args))
            except Exception as e:  # validation of made-up args, etc.
                text = f"EXCEPTION {type(e).__name__}: {e}"
            rows[tool.name] = text
        return rows

    rows = asyncio.run(go())
    buckets = {"works": [], "needs_build": [], "printer": [], "gestor": [], "other": []}
    for name, text in sorted(rows.items()):
        if name in PRINTER_TOOLS:
            buckets["printer"].append(name)
        elif name in GESTOR_TOOLS:
            assert "gestor_error" in text, (name, text)
            buckets["gestor"].append(name)
        elif '"error"' in text and "OrcaSlicer MCP plugin" in text:
            buckets["needs_build"].append(name)
        elif '"error"' in text or "EXCEPTION" in text:
            buckets["other"].append(name)
        else:
            buckets["works"].append(name)
        assert "needs M4" not in text and "not available on this OrcaSlicer build" not in text, (name, text)
    with capsys.disabled():
        for b, names in buckets.items():
            print(f"\n{b} ({len(names)}): {', '.join(names)}")
        for name in buckets["other"]:
            print(f"  {name}: {rows[name][:220]!r}")
    assert EXPECT_WORKS <= set(buckets["works"]), EXPECT_WORKS - set(buckets["works"])
    assert ("GET", "/api/download") in gestor.seen, gestor.seen
    synced = {row["name"]: row["status"] for row in json.loads(rows["sync_presets"])["presets"]}
    assert synced[GESTOR_PRESET["name"]] == "only_gestor", synced
    assert synced["My Fast"] == "only_pc", synced
    assert buckets["other"] == [], buckets["other"]
