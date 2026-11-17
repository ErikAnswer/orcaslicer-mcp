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


def _status(*modified: dict):
    replies = [httpx.Response(200, json={"modified": {"print": [], "printer": [], "filament": []} | m})
               for m in (modified or ({},))]
    return respx.get(f"{BASE}/status").mock(side_effect=replies + [replies[-1]] * 3)


def _filaments(tab_preset: str | None, tab_slot: int | None, *presets: str):
    rows = [{"index": i, "preset": name} for i, name in enumerate(presets)]
    return respx.get(f"{BASE}/filaments").mock(return_value=httpx.Response(
        200, json={"count": len(rows), "filaments": rows, "tab": {"preset": tab_preset, "slot": tab_slot}}))


MARBLE = "SM PLA Marble - White"


@respx.mock
async def test_range_calibration_sends_range_and_assigns_tool():
    _status()
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
    _status()
    calib = respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": []}))
    await srv.create_calibration("flow_rate", flow_pass=2, linear=True)
    assert json.loads(calib.calls.last.request.read()) == {
        "type": "flow_rate", "discard_unsaved": False, "pass": 2, "linear": True}


@respx.mock
async def test_unsaved_project_is_reported_not_discarded():
    _status()
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        409, json={"error": "unsaved_project"}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["error"] == "unsaved_project"


@respx.mock
async def test_not_created_says_the_project_was_replaced():
    _status()
    detail = "the calibration test created no objects and the previous project was already replaced; the MCP panel has a restore point"
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        500, json={"error": "not_created", "project_replaced": True, "detail": detail}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002, discard_unsaved=True)
    assert out["error"] == f"not_created: {detail}"
    assert out["project_replaced"] is True
    assert out["detail"] == detail


@respx.mock
async def test_not_created_without_replacing_the_project():
    _status()
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        500, json={"error": "not_created", "project_replaced": False,
                   "detail": "the calibration test created no objects"}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["project_replaced"] is False
    assert out["detail"] == "the calibration test created no objects"


@respx.mock
async def test_not_created_from_an_older_build_counts_the_project_as_replaced():
    _status()
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(422, json={"error": "not_created"}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["error"] == "not_created"
    assert out["project_replaced"] is True
    assert out["detail"] == srv._CALIBRATION_REPLACED


@respx.mock
async def test_not_created_from_an_older_build_adds_the_replacement_to_its_detail():
    _status()
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        422, json={"error": "not_created", "detail": "no objects"}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["project_replaced"] is True
    assert out["detail"] == f"no objects; {srv._CALIBRATION_REPLACED}"


@respx.mock
async def test_not_created_replaced_without_detail_gets_the_restore_point_text():
    _status()
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        500, json={"error": "not_created", "project_replaced": True}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002, discard_unsaved=True)
    assert out["project_replaced"] is True
    assert out["detail"] == srv._CALIBRATION_REPLACED


@respx.mock
async def test_unsupported_on_belt_keeps_its_detail_and_no_replacement_flag():
    _status()
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        422, json={"error": "unsupported_on_belt", "detail": "use pa_tower on a belt printer"}))
    out = await srv.create_calibration("pa_pattern", 0, 0.08, 0.002)
    assert out["error"] == "unsupported_on_belt: use pa_tower on a belt printer"
    assert out["detail"] == "use pa_tower on a belt printer"
    assert "project_replaced" not in out


@pytest.mark.parametrize("error", ["slicing", "busy", "job_running"])
@respx.mock
async def test_busy_refusals_are_reported_as_they_come(error):
    _status()
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(409, json={"error": error}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["error"] == error
    assert "project_replaced" not in out


@respx.mock
async def test_calibration_lists_the_preset_edits_it_left():
    _status({"print": ["layer_height"], "printer": ["extruder_nozzle_stats"]},
            {"print": ["layer_height", "inner_wall_speed", "outer_wall_speed"],
             "filament": ["slow_down_layer_time#0", "slow_down_layer_time#1"],
             "printer": ["extruder_nozzle_stats"]})
    respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"config": {"layer_height": "0.2"}}))
    _filaments("Generic PLA", 0, "Generic PLA")
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["preset_edits"] == {"print": ["inner_wall_speed", "outer_wall_speed"],
                                   "filament": ["slow_down_layer_time"]}
    assert out["filament_preset"] == "Generic PLA" and out["filament_slots"] == [1]
    assert out["preset_edits_detail"] == srv._CALIBRATION_EDITS_DETAIL + (
        " The filament keys went to 'Generic PLA', the preset open in the Filament tab: "
        "put them back with set_config and filament_slot=1.")
    assert "preset_edits_overwritten" not in out


@respx.mock
async def test_calibration_reports_earlier_edits_it_overwrote():
    _status({"print": ["outer_wall_speed", "layer_height"]},
            {"print": ["outer_wall_speed", "layer_height", "inner_wall_speed"]})
    respx.get(f"{BASE}/config").mock(side_effect=[
        httpx.Response(200, json={"config": {"outer_wall_speed": "120", "layer_height": "0.2"}}),
        httpx.Response(200, json={"config": {"outer_wall_speed": "200", "layer_height": "0.2",
                                             "inner_wall_speed": "200"}})])
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["preset_edits"] == {"print": ["inner_wall_speed"]}
    assert out["preset_edits_overwritten"] == {"print": ["outer_wall_speed"]}
    assert out["preset_edits_detail"] == srv._CALIBRATION_EDITS_DETAIL + srv._CALIBRATION_OVERWRITTEN_DETAIL


@respx.mock
async def test_calibration_flags_earlier_edits_as_maybe_overwritten_when_config_fails():
    _status({"print": ["outer_wall_speed"], "filament": ["slow_down_layer_time#0"]},
            {"print": ["outer_wall_speed", "inner_wall_speed"], "filament": ["slow_down_layer_time#0"]})
    respx.get(f"{BASE}/config").mock(return_value=httpx.Response(500, json={"error": "boom"}))
    respx.get(f"{BASE}/filaments").mock(return_value=httpx.Response(404, json={"error": "not_found"}))
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["preset_edits"] == {"print": ["inner_wall_speed"]}
    assert out["preset_edits_overwritten"] == {"print": ["outer_wall_speed"], "filament": ["slow_down_layer_time"]}
    assert "filament_preset" not in out and "filament_slots" not in out
    assert out["preset_edits_detail"] == (srv._CALIBRATION_EDITS_DETAIL + srv._CALIBRATION_MAYBE_OVERWRITTEN_DETAIL
                                          + srv._CALIBRATION_FILAMENT_PRESET_UNKNOWN)


@respx.mock
async def test_calibration_reads_values_after_only_for_keys_edited_before():
    _status({"print": ["layer_height"]}, {"print": ["inner_wall_speed"]})
    config = respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"config": {"layer_height": "0.2"}}))
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert config.call_count == 1
    assert out["preset_edits"] == {"print": ["inner_wall_speed"]}


@respx.mock
async def test_calibration_reads_no_values_without_earlier_edits():
    _status({}, {"print": ["inner_wall_speed"]})
    config = respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"config": {}}))
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert config.call_count == 0
    assert out["preset_edits"] == {"print": ["inner_wall_speed"]}


@respx.mock
async def test_calibration_skips_the_later_read_when_the_earlier_one_failed():
    _status({"print": ["outer_wall_speed"]}, {"print": ["outer_wall_speed"]})
    config = respx.get(f"{BASE}/config").mock(return_value=httpx.Response(500, json={"error": "boom"}))
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert config.call_count == 1
    assert out["preset_edits_overwritten"] == {"print": ["outer_wall_speed"]}
    assert out["preset_edits_detail"] == srv._CALIBRATION_EDITS_DETAIL + srv._CALIBRATION_MAYBE_OVERWRITTEN_DETAIL


@respx.mock
async def test_calibration_flags_maybe_overwritten_when_only_the_later_read_fails():
    _status({"print": ["outer_wall_speed"]}, {"print": ["outer_wall_speed", "inner_wall_speed"]})
    respx.get(f"{BASE}/config").mock(side_effect=[
        httpx.Response(200, json={"config": {"outer_wall_speed": "120"}}),
        httpx.Response(500, json={"error": "boom"})])
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["preset_edits"] == {"print": ["inner_wall_speed"]}
    assert out["preset_edits_overwritten"] == {"print": ["outer_wall_speed"]}
    assert out["preset_edits_detail"] == srv._CALIBRATION_EDITS_DETAIL + srv._CALIBRATION_MAYBE_OVERWRITTEN_DETAIL


@respx.mock
async def test_calibration_reads_no_later_values_when_earlier_edits_are_gone():
    _status({"print": ["outer_wall_speed"]}, {"print": ["inner_wall_speed"]})
    config = respx.get(f"{BASE}/config").mock(return_value=httpx.Response(
        200, json={"config": {"outer_wall_speed": "120"}}))
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert config.call_count == 1
    assert out["preset_edits"] == {"print": ["inner_wall_speed"]}
    assert "preset_edits_overwritten" not in out


@respx.mock
async def test_calibration_says_overwritten_filament_keys_are_compared_across_slots():
    _status({"filament": ["slow_down_layer_time#0"]}, {"filament": ["slow_down_layer_time#0"]})
    respx.get(f"{BASE}/config").mock(side_effect=[
        httpx.Response(200, json={"config": {"slow_down_layer_time": "8,8"}}),
        httpx.Response(200, json={"config": {"slow_down_layer_time": "1,8"}})])
    _filaments("Generic PLA", 0, "Generic PLA", "Generic PLA")
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["preset_edits_overwritten"] == {"filament": ["slow_down_layer_time"]}
    assert out["filament_preset"] == "Generic PLA" and out["filament_slots"] == [1, 2]
    assert out["preset_edits_detail"] == (srv._CALIBRATION_EDITS_DETAIL + srv._CALIBRATION_OVERWRITTEN_DETAIL
                                          + srv._CALIBRATION_FILAMENT_WHOLE_VALUE
                                          + " The filament keys are in 'Generic PLA', the preset open in the Filament "
                                          "tab: once the user says which value they want, set it with set_config and "
                                          "filament_slot=1 (slots 1, 2 share it, so it reaches all of them).")


@respx.mock
async def test_calibration_keeps_the_preset_edits_when_assigning_the_tool_fails():
    _status({}, {"print": ["inner_wall_speed"]})
    respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"config": {}}))
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}, {"id": 8, "name": "pa_tower"}]}))
    respx.put(f"{BASE}/objects/7/config").mock(return_value=httpx.Response(
        200, json={"applied": ["extruder"], "errors": {}}))
    respx.put(f"{BASE}/objects/8/config").mock(return_value=httpx.Response(500, json={"error": "boom"}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002, tool=2)
    assert out["error"].startswith("boom")
    assert out["assigned_objects"] == [7] and out["tool"] == 2
    assert out["preset_edits"] == {"print": ["inner_wall_speed"]}
    assert out["preset_edits_detail"] == srv._CALIBRATION_EDITS_DETAIL


@respx.mock
async def test_calibration_does_not_claim_edits_when_the_earlier_status_failed():
    respx.get(f"{BASE}/status").mock(side_effect=[
        httpx.Response(500, json={"error": "boom"}),
        httpx.Response(200, json={"modified": {"print": ["layer_height", "inner_wall_speed"], "filament": []}})])
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"config": {}}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert "preset_edits" not in out
    assert out["modified_presets"] == {"print": ["layer_height", "inner_wall_speed"]}
    assert out["preset_edits_detail"] == srv._CALIBRATION_BEFORE_UNKNOWN


@respx.mock
async def test_calibration_without_preset_edits_says_nothing_about_them():
    _status()
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_line"}]}))
    out = await srv.create_calibration("pa_line", 0, 0.08, 0.002)
    assert "preset_edits" not in out and "preset_edits_detail" not in out


@respx.mock
async def test_calibration_says_when_its_preset_edits_could_not_be_read():
    respx.get(f"{BASE}/status").mock(side_effect=[
        httpx.Response(200, json={"modified": {"print": []}}), httpx.Response(500, json={"error": "boom"})])
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["created"] is True and "preset_edits" not in out
    assert out["preset_edits_detail"] == srv._CALIBRATION_EDITS_UNCHECKED


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


@respx.mock
async def test_calibration_names_the_open_filament_preset_when_it_is_not_in_slot_one():
    _status({}, {"print": ["inner_wall_speed"], "filament": ["slow_down_layer_time#1"]})
    _filaments(MARBLE, 1, "Generic PLA", MARBLE)
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["preset_edits"] == {"print": ["inner_wall_speed"], "filament": ["slow_down_layer_time"]}
    assert out["filament_preset"] == MARBLE and out["filament_slots"] == [2]
    assert out["preset_edits_detail"].endswith(
        f" The filament keys went to {MARBLE!r}, the preset open in the Filament tab: "
        "put them back with set_config and filament_slot=2.")


@respx.mock
async def test_calibration_puts_the_tab_slot_first_among_the_slots_sharing_the_preset():
    _status({}, {"filament": ["slow_down_layer_time#2"]})
    _filaments(MARBLE, 2, MARBLE, "Generic PLA", MARBLE)
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["filament_slots"] == [3, 1]
    assert "filament_slot=3 (slots 3, 1 share it" in out["preset_edits_detail"]


@respx.mock
async def test_calibration_says_when_no_slot_uses_the_open_filament_preset():
    _status({}, {"filament": ["slow_down_layer_time#0"]})
    _filaments(MARBLE, None, "Generic PLA")
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["filament_preset"] == MARBLE and out["filament_slots"] == []
    assert out["preset_edits_detail"] == (srv._CALIBRATION_EDITS_DETAIL
                                          + srv._CALIBRATION_FILAMENT_PRESET_UNUSED.format(preset=MARBLE))


@respx.mock
async def test_calibration_with_print_edits_only_does_not_read_the_filament_slots():
    _status({}, {"print": ["inner_wall_speed"]})
    filaments = _filaments(MARBLE, 1, "Generic PLA", MARBLE)
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert filaments.call_count == 0
    assert "filament_preset" not in out and out["preset_edits_detail"] == srv._CALIBRATION_EDITS_DETAIL


@respx.mock
async def test_calibration_keeps_the_filament_preset_when_assigning_the_tool_fails():
    _status({}, {"filament": ["slow_down_layer_time#1"]})
    _filaments(MARBLE, 1, "Generic PLA", MARBLE)
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    respx.put(f"{BASE}/objects/7/config").mock(return_value=httpx.Response(500, json={"error": "boom"}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002, tool=2)
    assert out["error"].startswith("boom")
    assert out["filament_preset"] == MARBLE and out["filament_slots"] == [2]
    assert "filament_slot=2" in out["preset_edits_detail"]


@respx.mock
async def test_calibration_with_unknown_earlier_edits_asks_before_reverting_filament_keys():
    respx.get(f"{BASE}/status").mock(side_effect=[
        httpx.Response(500, json={"error": "boom"}),
        httpx.Response(200, json={"modified": {"print": [], "filament": ["slow_down_layer_time#1"]}})])
    _filaments(MARBLE, 1, "Generic PLA", MARBLE)
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["modified_presets"] == {"filament": ["slow_down_layer_time"]}
    assert out["filament_slots"] == [2]
    assert out["preset_edits_detail"].endswith(
        f" The filament keys are in {MARBLE!r}, the preset open in the Filament tab: once the user says which "
        "value they want, set it with set_config and filament_slot=2.")


@respx.mock
async def test_calibration_treats_an_unexpected_filaments_body_as_unknown():
    _status({}, {"filament": ["slow_down_layer_time#1"]})
    respx.get(f"{BASE}/filaments").mock(return_value=httpx.Response(200, json=["not", "a", "dict"]))
    respx.post(f"{BASE}/calibration").mock(return_value=httpx.Response(
        200, json={"created": True, "objects": [{"id": 7, "name": "pa_tower"}]}))
    out = await srv.create_calibration("pa_tower", 0, 0.08, 0.002)
    assert out["created"] is True and out["preset_edits"] == {"filament": ["slow_down_layer_time"]}
    assert "filament_preset" not in out
    assert out["preset_edits_detail"] == srv._CALIBRATION_EDITS_DETAIL + srv._CALIBRATION_FILAMENT_PRESET_UNKNOWN
