import json

import httpx
import pytest
import respx

import orcaslicer_mcp.server as srv
from orcaslicer_mcp import tool_offset_test as tt
from orcaslicer_mcp.client import OrcaClient
from orcaslicer_mcp.config import Config

BASE = "http://x:13130/api/v1"
GESTOR = "http://192.168.1.17"
REFERENCE_ID, TOOL_ID = 11, 12


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(srv, "_client", lambda: OrcaClient(Config(base_url="http://x:13130", token="t", timeout=5)))
    monkeypatch.setenv("ORCA_GESTOR_URL", GESTOR)


def _gestor():
    respx.get(f"{GESTOR}/api/state").mock(return_value=httpx.Response(200, json={"mutationToken": "tok"}))
    return respx.post(f"{GESTOR}/api/upload").mock(side_effect=lambda request: httpx.Response(
        201, json={"file": {"path": request.url.params["filename"], "root": "scratch"}}))


class Slicer:
    def __init__(self, nozzles="0.4,0.4,0.4,0.4", moves=True):
        self.offsets = {4: [128.0, 128.0, 5.0]}
        self.moves = moves
        self.extruders = {}
        self.translations = []
        respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"config": {"nozzle_diameter": nozzles}}))
        self.model = respx.post(f"{BASE}/model").mock(side_effect=self._load)
        respx.get(f"{BASE}/objects").mock(side_effect=self._objects)
        for obj_id in (REFERENCE_ID, TOOL_ID):
            respx.put(f"{BASE}/objects/{obj_id}/config").mock(side_effect=self._configure(obj_id))
        respx.post(f"{BASE}/objects/{TOOL_ID}/transform").mock(side_effect=self._transform)

    def _load(self, request):
        obj_id = REFERENCE_ID if REFERENCE_ID not in self.offsets else TOOL_ID
        self.offsets[obj_id] = [60.0, 70.0, 0.5] if obj_id == REFERENCE_ID else [180.0, 40.0, 0.5]
        return httpx.Response(200, json={"loaded": True, "objects": [{"index": len(self.offsets) - 1}]})

    def _objects(self, request):
        objects = [{"id": obj_id, "transform": {"offset": offset, "rotation": [0, 0, 0], "scale": [1, 1, 1]}}
                   for obj_id, offset in self.offsets.items()]
        return httpx.Response(200, json={"count": len(objects), "objects": objects})

    def _configure(self, obj_id):
        def handler(request):
            self.extruders[obj_id] = json.loads(request.read())
            return httpx.Response(200, json={"applied": ["extruder"], "errors": {}})
        return handler

    def _transform(self, request):
        translate = json.loads(request.read())["translate"]
        self.translations.append(translate)
        if self.moves:
            self.offsets[TOOL_ID] = [a + b for a, b in zip(self.offsets[TOOL_ID], translate)]
        return httpx.Response(200, json={"id": TOOL_ID})


@respx.mock
async def test_pieces_are_uploaded_assigned_to_their_heads_and_placed_against_each_other():
    upload = _gestor()
    slicer = Slicer()
    out = await srv.create_tool_offset_calibration(reference_tool=1, tool=3)

    assert [dict(call.request.url.params) for call in upload.calls] == [
        {"filename": "tool-offset-reference-T1-0.05.stl", "scratch": "1"},
        {"filename": "tool-offset-tool-T3-0.05.stl", "scratch": "1"}]
    test = tt.build_tool_offset_test()
    assert [call.request.read() for call in upload.calls] == [test.reference_stl, test.tool_stl]
    assert [json.loads(call.request.read())["url"] for call in slicer.model.calls] == [
        f"{GESTOR}/api/download?root=scratch&path=tool-offset-reference-T1-0.05.stl",
        f"{GESTOR}/api/download?root=scratch&path=tool-offset-tool-T3-0.05.stl"]
    assert slicer.extruders == {REFERENCE_ID: {"extruder": 1}, TOOL_ID: {"extruder": 3}}
    delta = (test.tool_center[0] - test.reference_center[0], test.tool_center[1] - test.reference_center[1])
    assert slicer.offsets[TOOL_ID] == pytest.approx([60 + delta[0], 70 + delta[1], 0.5])
    assert slicer.offsets[4] == [128.0, 128.0, 5.0] and len(slicer.translations) == 1
    assert out["objects"] == {"reference": REFERENCE_ID, "tool": TOOL_ID} and out["aligned"] is True
    assert (out["reference_tool"], out["tool"], out["step_mm"], out["range_mm"]) == (1, 3, 0.05, 0.5)
    assert "long tab" in out["read"] and "arrange_plate" in out["keep_together"] and "error" not in out


@respx.mock
async def test_a_piece_that_did_not_move_is_reported_as_not_aligned():
    _gestor()
    Slicer(moves=False)
    out = await srv.create_tool_offset_calibration(reference_tool=2, tool=1, step=0.1)
    assert out["error"] == "not_aligned" and out["aligned"] is False and "read" not in out
    assert out["objects"] == {"reference": REFERENCE_ID, "tool": TOOL_ID} and "delete_object" in out["detail"]


@respx.mock
async def test_a_head_the_printer_does_not_have_loads_nothing():
    _gestor()
    slicer = Slicer(nozzles="0.4,0.4")
    out = await srv.create_tool_offset_calibration(reference_tool=1, tool=3)
    assert out["error"] == "no_such_tool" and out["heads"] == 2 and not slicer.model.called


async def test_same_head_or_bad_step_touch_nothing():
    assert (await srv.create_tool_offset_calibration(reference_tool=2, tool=2))["error"] == "same_tool"
    out = await srv.create_tool_offset_calibration(reference_tool=1, tool=2, step=0.5)
    assert out["error"] == "invalid_step" and "0.02" in out["detail"]


@respx.mock
async def test_gestor_failure_loads_nothing():
    respx.get(f"{GESTOR}/api/state").mock(side_effect=httpx.ConnectError("down"))
    model = respx.post(f"{BASE}/model").mock(return_value=httpx.Response(200, json={}))
    out = await srv.create_tool_offset_calibration(reference_tool=1, tool=2)
    assert out["error"] == "gestor_error" and "nothing was loaded" in out["detail"] and not model.called


@respx.mock
async def test_slicer_refusal_before_loading_reports_only_the_error():
    _gestor()
    Slicer().model.mock(return_value=httpx.Response(422, json={"error": "url_not_allowed"}))
    out = await srv.create_tool_offset_calibration(reference_tool=1, tool=2)
    assert out["error"] and "objects" not in out


@respx.mock
async def test_failure_after_the_first_piece_says_what_to_delete():
    _gestor()
    Slicer()
    respx.put(f"{BASE}/objects/{TOOL_ID}/config").mock(return_value=httpx.Response(409, json={"error": "busy"}))
    out = await srv.create_tool_offset_calibration(reference_tool=1, tool=2)
    assert out["error"] and out["objects"] == {"reference": REFERENCE_ID}
    assert "half made" in out["detail"] and "delete_object" in out["detail"]


@respx.mock
async def test_piece_whose_object_id_cannot_be_read_is_reported():
    _gestor()
    Slicer().model.mock(return_value=httpx.Response(200, json={"loaded": True, "objects": []}))
    out = await srv.create_tool_offset_calibration(reference_tool=1, tool=2)
    assert out["error"] == "object_not_found" and out["objects"] == {} and "list_objects" in out["detail"]
