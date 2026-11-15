import json

import httpx
import pytest
import respx

import orcaslicer_mcp.server as srv
from orcaslicer_mcp.config import Config
from orcaslicer_mcp.client import OrcaClient

BASE = "http://x:13130/api/v1"
GESTOR = "http://g"
OLD = {"id": "old", "filename": "a.gcode.3mf", "status": "queued", "position": 0}
DONE = {"id": "done", "filename": "b.gcode.3mf", "status": "completed", "position": 0}
NEW = {"id": "new1", "filename": "cube.gcode.3mf", "status": "queued", "position": 1,
       "createdAt": "2026-11-15T06:00:00Z", "sha256": "x"}


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(srv, "_client", lambda: OrcaClient(Config(base_url="http://x:13130", token="t", timeout=5)))
    monkeypatch.setenv("ORCA_GESTOR_URL", GESTOR)
    monkeypatch.setattr(srv, "QUEUE_CONFIRM_DELAY_S", 0)


def _state(jobs, history=(DONE,), current=None):
    return httpx.Response(200, json={"mutationToken": "tok", "queue": {
        "jobs": list(jobs), "history": list(history), "current": current}})


def _orca_ok():
    return respx.post(f"{BASE}/print/queue").mock(return_value=httpx.Response(
        202, json={"sent": True, "upload": {"gcodeToolCnt": "1"}}))


@respx.mock
async def test_returns_the_new_job_id():
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[_state([OLD]), _state([OLD, NEW])])
    _orca_ok()
    out = await srv.send_to_queue()
    assert out["sent"] is True and out["queue_confirmed"] is True
    assert out["job"] == {"id": "new1", "filename": "cube.gcode.3mf", "status": "queued",
                          "position": 1, "createdAt": "2026-11-15T06:00:00Z"}
    assert "held" not in out


@respx.mock
async def test_waits_for_the_job_to_show_up():
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[_state([]), _state([]), _state([NEW])])
    _orca_ok()
    out = await srv.send_to_queue()
    assert out["queue_confirmed"] is True and out["job"]["id"] == "new1"


@respx.mock
async def test_job_never_appears_is_reported_without_failing(monkeypatch):
    monkeypatch.setattr(srv, "QUEUE_CONFIRM_ATTEMPTS", 3)
    state = respx.get(f"{GESTOR}/api/state").mock(return_value=_state([OLD]))
    _orca_ok()
    out = await srv.send_to_queue()
    assert out["sent"] is True and out["queue_confirmed"] is False
    assert "did not show up" in out["queue_detail"]
    assert state.call_count == 4


@respx.mock
async def test_several_new_jobs_are_not_guessed_nor_held():
    other = NEW | {"id": "new2", "filename": "other.gcode.3mf"}
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[_state([]), _state([NEW, other])])
    hold = respx.post(f"{GESTOR}/api/queue/action")
    _orca_ok()
    out = await srv.send_to_queue(hold=True)
    assert out["queue_confirmed"] is False and out["held"] is False
    assert [c["id"] for c in out["candidates"]] == ["new1", "new2"]
    assert not hold.called


@respx.mock
async def test_hold_puts_the_new_job_on_hold():
    held = NEW | {"status": "held"}
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[_state([OLD]), _state([OLD, NEW]), _state([OLD, held])])
    action = respx.post(f"{GESTOR}/api/queue/action").mock(return_value=httpx.Response(200, json={}))
    _orca_ok()
    out = await srv.send_to_queue(hold=True)
    assert out["held"] is True and out["job"]["status"] == "held"
    assert json.loads(action.calls.last.request.read()) == {"action": "toggleHold", "id": "new1"}
    assert action.calls.last.request.headers["X-Cad3d-Token"] == "tok"


@respx.mock
async def test_hold_reports_when_the_job_did_not_end_up_held():
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[_state([]), _state([NEW]), _state([NEW])])
    respx.post(f"{GESTOR}/api/queue/action").mock(return_value=httpx.Response(200, json={}))
    _orca_ok()
    out = await srv.send_to_queue(hold=True)
    assert out["queue_confirmed"] is True and out["held"] is False
    assert "instead of held" in out["hold_error"]


@respx.mock
async def test_hold_rejected_by_the_gestor_keeps_the_job():
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[_state([]), _state([NEW])])
    respx.post(f"{GESTOR}/api/queue/action").mock(return_value=httpx.Response(
        409, json={"error": "Este trabajo ya no se puede pausar en la cola."}))
    _orca_ok()
    out = await srv.send_to_queue(hold=True)
    assert out["job"]["id"] == "new1" and out["held"] is False
    assert "pausar" in out["hold_error"]


@respx.mock
async def test_hold_skips_a_job_that_is_not_queued():
    started = NEW | {"status": "preheating"}
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[_state([]), _state([], current=started)])
    action = respx.post(f"{GESTOR}/api/queue/action")
    _orca_ok()
    out = await srv.send_to_queue(hold=True)
    assert out["held"] is False and "only a queued job" in out["hold_error"]
    assert not action.called


@respx.mock
async def test_known_history_and_current_jobs_are_not_new():
    current = {"id": "cur", "status": "printing"}
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[
        _state([OLD], current=current), _state([OLD, NEW], current=current)])
    _orca_ok()
    out = await srv.send_to_queue()
    assert out["job"]["id"] == "new1"


@respx.mock
async def test_unreachable_gestor_still_sends_without_hold():
    respx.get(f"{GESTOR}/api/state").mock(side_effect=httpx.ConnectError("down"))
    orca = _orca_ok()
    out = await srv.send_to_queue()
    assert orca.called and out["sent"] is True and out["queue_confirmed"] is False
    assert "before sending" in out["queue_detail"]


@respx.mock
async def test_unreachable_gestor_refuses_to_send_with_hold():
    respx.get(f"{GESTOR}/api/state").mock(side_effect=httpx.ConnectError("down"))
    orca = _orca_ok()
    out = await srv.send_to_queue(hold=True)
    assert out["error"] == "gestor_unreachable" and not orca.called


@respx.mock
async def test_queue_read_failure_after_sending_is_reported():
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[_state([]), httpx.ConnectError("down")])
    _orca_ok()
    out = await srv.send_to_queue(hold=True)
    assert out["sent"] is True and out["queue_confirmed"] is False and out["held"] is False
    assert "could not read the queue" in out["queue_detail"]


@respx.mock
async def test_orca_error_keeps_detail_and_skips_confirmation():
    respx.get(f"{GESTOR}/api/state").mock(return_value=_state([]))
    respx.post(f"{BASE}/print/queue").mock(return_value=httpx.Response(
        403, json={"error": "not_queue_printer", "detail": "select the printer preset that sends to the home queue"}))
    out = await srv.send_to_queue()
    assert out["error"].startswith("not_queue_printer")
    assert "queue_confirmed" not in out


@respx.mock
async def test_invalid_gestor_settings_still_send_without_hold(monkeypatch):
    monkeypatch.setenv("ORCA_GESTOR_TIMEOUT", "abc")
    orca = _orca_ok()
    out = await srv.send_to_queue()
    assert orca.called and out["sent"] is True and out["queue_confirmed"] is False
    assert "ORCA_GESTOR_TIMEOUT" in out["queue_detail"]


@respx.mock
async def test_invalid_gestor_settings_refuse_to_send_with_hold(monkeypatch):
    monkeypatch.setenv("ORCA_GESTOR_TIMEOUT", "abc")
    orca = _orca_ok()
    out = await srv.send_to_queue(hold=True)
    assert out["error"] == "gestor_config" and not orca.called


@respx.mock
async def test_non_json_queue_after_sending_keeps_sent():
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[
        _state([]), httpx.Response(200, text="<html>restarting</html>")])
    _orca_ok()
    out = await srv.send_to_queue()
    assert out["sent"] is True and out["queue_confirmed"] is False
    assert "not JSON" in out["queue_detail"]


@respx.mock
async def test_non_object_state_before_sending_is_unconfirmed():
    respx.get(f"{GESTOR}/api/state").mock(return_value=httpx.Response(200, json=[1, 2]))
    orca = _orca_ok()
    out = await srv.send_to_queue()
    assert orca.called and out["queue_confirmed"] is False
    assert "not a JSON object" in out["queue_detail"]


@respx.mock
async def test_job_already_held_is_not_toggled():
    held = NEW | {"status": "held"}
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[_state([]), _state([held])])
    action = respx.post(f"{GESTOR}/api/queue/action")
    _orca_ok()
    out = await srv.send_to_queue(hold=True)
    assert out["held"] is True and out["job"]["status"] == "held"
    assert not action.called


@respx.mock
async def test_job_leaving_the_queue_while_holding_is_reported():
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[_state([]), _state([NEW]), _state([])])
    respx.post(f"{GESTOR}/api/queue/action").mock(return_value=httpx.Response(200, json={}))
    _orca_ok()
    out = await srv.send_to_queue(hold=True)
    assert out["held"] is False and "left the queue" in out["hold_error"]


@respx.mock
async def test_hold_sent_but_not_read_back_is_unverified():
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[_state([]), _state([NEW]), httpx.ConnectError("down")])
    action = respx.post(f"{GESTOR}/api/queue/action").mock(return_value=httpx.Response(200, json={}))
    _orca_ok()
    out = await srv.send_to_queue(hold=True)
    assert action.called and out["queue_confirmed"] is True
    assert out["held"] is None and "could not be read back" in out["hold_detail"]
    assert "hold_error" not in out and out["job"]["status"] == "queued"


@respx.mock
async def test_hold_retries_once_with_a_fresh_token_on_403():
    held = NEW | {"status": "held"}
    fresh = httpx.Response(200, json={"mutationToken": "tok2", "queue": {"jobs": [NEW], "history": []}})
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[_state([]), _state([NEW]), fresh, _state([held])])
    action = respx.post(f"{GESTOR}/api/queue/action").mock(side_effect=[
        httpx.Response(403, json={"error": "token"}), httpx.Response(200, json={})])
    _orca_ok()
    out = await srv.send_to_queue(hold=True)
    assert out["held"] is True and action.call_count == 2
    assert [c.request.headers["X-Cad3d-Token"] for c in action.calls] == ["tok", "tok2"]


@respx.mock
async def test_null_queue_reads_as_empty():
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[
        httpx.Response(200, json={"mutationToken": "tok", "queue": None}), _state([NEW])])
    _orca_ok()
    out = await srv.send_to_queue()
    assert out["queue_confirmed"] is True and out["job"]["id"] == "new1"


@respx.mock
async def test_non_object_queue_before_sending_is_unconfirmed():
    respx.get(f"{GESTOR}/api/state").mock(return_value=httpx.Response(
        200, json={"mutationToken": "tok", "queue": [OLD]}))
    orca = _orca_ok()
    out = await srv.send_to_queue()
    assert orca.called and out["queue_confirmed"] is False
    assert "queue is not a JSON object" in out["queue_detail"]


@respx.mock
async def test_non_object_queue_with_hold_sends_nothing():
    respx.get(f"{GESTOR}/api/state").mock(return_value=httpx.Response(
        200, json={"mutationToken": "tok", "queue": "x"}))
    orca = _orca_ok()
    out = await srv.send_to_queue(hold=True)
    assert out["error"] == "gestor_unreachable" and not orca.called


@respx.mock
async def test_jobs_without_id_neither_hide_nor_become_the_new_job():
    anon = {"filename": "anon.gcode.3mf", "status": "queued"}
    respx.get(f"{GESTOR}/api/state").mock(side_effect=[_state([anon]), _state([anon, NEW])])
    _orca_ok()
    out = await srv.send_to_queue()
    assert out["queue_confirmed"] is True and out["job"]["id"] == "new1"


def test_known_ids_skip_jobs_without_id():
    queue = {"jobs": [{"filename": "a"}, OLD], "history": [{"id": None}, DONE], "current": None}
    assert srv._queue_known_ids(queue) == {"old", "done"}
