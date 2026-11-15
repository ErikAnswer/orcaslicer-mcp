import json

import httpx
import pytest
import respx

import orcaslicer_mcp.server as srv
from orcaslicer_mcp import preset_sync as ps
from orcaslicer_mcp.client import OrcaClient
from orcaslicer_mcp.config import Config

BASE = "http://x:13130/api/v1"
GESTOR = "http://g"

PARENT = {"inherits": "", "filament_settings_id": "Generic PLA @System", "nozzle_temperature": "220",
          "filament_flow_ratio": "0.98", "filament_vendor": "Generic", "compatible_printers": "",
          "filament_start_gcode": "; filament start gcode\\n", "pressure_advance": "0.02"}
PC = PARENT | {"inherits": "Generic PLA @System", "filament_settings_id": "\"SM PLA - White\"",
               "nozzle_temperature": "210", "filament_vendor": "\"Smart Materials\"",
               "pressure_advance": "0.036"}
GESTOR_FILE = {"from": "User", "inherits": "Generic PLA @System", "name": "SM PLA - White",
               "filament_settings_id": ["SM PLA - White"], "filament_vendor": ["Smart Materials"],
               "nozzle_temperature": ["210"], "pressure_advance": ["0.02"], "version": "2.4.0.4"}


def test_strings_round_trip_orca_escaping():
    assert ps.unescape_strings('"a b";c;"x\\"y\\ny"') == ["a b", "c", 'x"y\ny']
    assert ps.unescape_strings('""') == [""]
    assert ps.unescape_strings("a;") == ["a", ""]
    assert ps.unescape_strings("") == []


def test_api_values_take_the_file_shapes_orca_writes():
    assert ps.from_api("nozzle_temperature", "210,215") == ["210", "215"]
    assert ps.from_api("filament_vendor", '"Smart Materials"') == ["Smart Materials"]
    assert ps.from_api("machine_start_gcode", "G28\\nG1 Z5") == "G28\nG1 Z5"
    assert ps.from_api("bed_exclude_area", "0x0,10x0") == ["0x0", "10x0"]
    assert ps.from_api("bed_mesh_max", "99999,99999") == "99999,99999"
    assert ps.from_api("layer_height", "0.2") == "0.2"
    assert ps.from_api("print_settings_id", "0.20mm") == "0.20mm"


def test_canonical_ignores_number_spelling_and_splits_vectors_given_as_text():
    assert ps.canonical("nozzle_temperature", ["210"]) == ps.canonical("nozzle_temperature", "210.0")
    assert ps.canonical("filament_flow_ratio", ["1"]) != ps.canonical("filament_flow_ratio", ["0.98"])


def test_file_keeps_only_overrides_and_matches_the_gestor_format():
    out = ps.build_file("filament", "SM PLA - White", PC, PARENT)
    assert out == {"from": "User", "name": "SM PLA - White", "inherits": "Generic PLA @System",
                   "filament_settings_id": ["SM PLA - White"], "filament_vendor": ["Smart Materials"],
                   "nozzle_temperature": ["210"], "pressure_advance": ["0.036"]}
    text = ps.dump_file(out | {"version": "2.4.0.4"}).decode()
    assert text.startswith('{\n\t"filament_settings_id": [\n\t\t"SM PLA - White"\n\t],')


def test_file_without_parent_is_written_in_full():
    out = ps.build_file("print", "Mine", {"inherits": "", "layer_height": "0.2", "wall_loops": "3"}, None)
    assert out["layer_height"] == "0.2" and out["wall_loops"] == "3" and out["print_settings_id"] == "Mine"


def test_redacted_credentials_are_never_written_and_keep_the_old_value():
    pc = {"inherits": "", "printhost_apikey": "<redacted>", "print_host": "192.168.1.17:8898",
          "printer_model": "Creator 5 Pro"}
    assert "printhost_apikey" not in ps.build_file("printer", "Q", pc, None)
    kept = ps.build_file("printer", "Q", pc, None, {"printhost_apikey": "abc"})
    assert kept["printhost_apikey"] == "abc"


def test_compare_uses_effective_values_on_both_sides():
    result = ps.compare(PC, GESTOR_FILE, PARENT)
    assert [(d.key, d.pc, d.gestor) for d in result.differences] == [
        ("pressure_advance", ["0.036"], ["0.02"])]
    flattened = GESTOR_FILE | {k: ps.from_api(k, v) for k, v in PARENT.items() if k not in GESTOR_FILE}
    flattened["pressure_advance"] = ["0.036"]
    assert ps.compare(PC, flattened, PARENT).differences == []


def test_compare_reports_a_different_parent_and_unknown_parent():
    result = ps.compare(PC, GESTOR_FILE | {"inherits": "Other"}, None)
    assert result.differences[0].key == "inherits" and not result.parent_known


@pytest.fixture
def both(monkeypatch):
    monkeypatch.setattr(srv, "_client", lambda: OrcaClient(Config(base_url="http://x:13130", token="t", timeout=5)))
    monkeypatch.setenv("ORCA_GESTOR_URL", GESTOR)


def _mock_pc(status_modified=None, selected="Other"):
    respx.get(f"{BASE}/presets").mock(return_value=httpx.Response(200, json={
        "filament": [{"name": "SM PLA - White", "system": False}, {"name": "Generic PLA @System", "system": True}],
        "print": [], "printer": []}))

    def preset_config(request):
        name = json.loads(request.read())["name"]
        cfg = {"SM PLA - White": PC, "Generic PLA @System": PARENT}.get(name)
        if cfg is None:
            return httpx.Response(404, json={"error": "unknown_preset"})
        return httpx.Response(200, json={"name": name, "system": name.endswith("@System"), "config": cfg})
    respx.post(f"{BASE}/preset/config").mock(side_effect=preset_config)
    respx.get(f"{BASE}/status").mock(return_value=httpx.Response(200, json={
        "presets": {"print": "p", "printer": "q", "filaments": [selected]},
        "modified": {"print": [], "printer": [], "filament": status_modified or []}}))


def _mock_gestor(entries=None, file=GESTOR_FILE):
    entries = [{"path": "SM PLA - White.json", "name": "SM PLA - White", "kind": "filament"}] if entries is None else entries
    respx.get(f"{GESTOR}/api/state").mock(return_value=httpx.Response(200, json={
        "presets": entries, "mutationToken": "tok"}))
    respx.get(f"{GESTOR}/api/download").mock(return_value=httpx.Response(200, content=json.dumps(file).encode()))
    trash = respx.post(f"{GESTOR}/api/trash").mock(return_value=httpx.Response(201, json={"entry": {"id": "T1"}}))
    upload = respx.post(f"{GESTOR}/api/preset-upload").mock(return_value=httpx.Response(
        201, json={"preset": {"path": "SM PLA - White.json"}}))
    restore = respx.post(f"{GESTOR}/api/restore").mock(return_value=httpx.Response(200, json={"restored": {}}))
    return trash, upload, restore


@respx.mock
async def test_compare_lists_differences_per_preset(both):
    _mock_pc()
    _mock_gestor(entries=[{"path": "SM PLA - White.json", "name": "SM PLA - White", "kind": "filament"},
                          {"path": "0.20mm.json", "name": "0.20mm", "kind": "process"}])
    out = await srv.sync_presets()
    rows = {r["name"]: r for r in out["presets"]}
    assert rows["SM PLA - White"]["status"] == "different"
    assert rows["SM PLA - White"]["differences"] == [{"key": "pressure_advance", "pc": ["0.036"], "gestor": ["0.02"]}]
    assert rows["0.20mm"]["status"] == "only_gestor"
    assert "Generic PLA @System" not in rows


@respx.mock
async def test_push_needs_names_and_dry_run_writes_nothing(both):
    assert (await srv.sync_presets("pc_to_gestor"))["error"] == "names_required"
    _mock_pc()
    trash, upload, _ = _mock_gestor()
    out = await srv.sync_presets("pc_to_gestor", ["SM PLA - White"])
    row = out["presets"][0]
    assert row["action"] == "replace" and row["dry_run"] is True and row["difference_count"] == 1
    assert trash.call_count == 0 and upload.call_count == 0


@respx.mock
async def test_push_backs_up_to_the_trash_then_uploads(both):
    _mock_pc()
    trash, upload, _ = _mock_gestor()
    row = (await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False))["presets"][0]
    assert row["backup"]["gestor_trash_id"] == "T1" and row["written"] == "SM PLA - White.json"
    assert trash.calls.last.request.headers["X-Cad3d-Token"] == "tok"
    sent = json.loads(upload.calls.last.request.read())
    assert sent["pressure_advance"] == ["0.036"] and sent["version"] == "2.4.0.4"
    assert upload.calls.last.request.url.params["filename"] == "SM PLA - White.json"


@respx.mock
async def test_push_restores_the_backup_when_the_upload_fails(both):
    _mock_pc()
    _, upload, restore = _mock_gestor()
    upload.mock(return_value=httpx.Response(400, json={"error": "El preset JSON no es válido."}))
    row = (await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False))["presets"][0]
    assert row["error"] == "upload_failed" and row["restored"] is True
    assert json.loads(restore.calls.last.request.read()) == {"id": "T1"}


@respx.mock
async def test_push_refuses_unsaved_edits_and_system_presets(both):
    _mock_pc(status_modified=["nozzle_temperature"], selected="SM PLA - White")
    trash, upload, _ = _mock_gestor()
    out = await srv.sync_presets("pc_to_gestor", ["SM PLA - White", "Generic PLA @System"], dry_run=False)
    errors = [r["error"] for r in out["presets"]]
    assert errors == ["unsaved_changes", "system_preset"]
    assert trash.call_count == 0 and upload.call_count == 0


@respx.mock
async def test_push_creates_a_new_preset_without_trashing(both):
    _mock_pc()
    trash, upload, _ = _mock_gestor(entries=[])
    row = (await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False))["presets"][0]
    assert row["action"] == "create" and "backup" not in row and trash.call_count == 0
    assert upload.call_count == 1


@respx.mock
async def test_push_skips_a_preset_that_is_already_the_same(both):
    _mock_pc()
    trash, upload, _ = _mock_gestor(file=GESTOR_FILE | {"pressure_advance": ["0.036"]})
    row = (await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False))["presets"][0]
    assert row["action"] == "none" and upload.call_count == 0


@respx.mock
async def test_unreachable_gestor_is_reported(both):
    respx.get(f"{GESTOR}/api/state").mock(side_effect=httpx.ConnectError("down"))
    out = await srv.sync_presets()
    assert out["error"] == "gestor_error" and "not reachable" in out["detail"]


@respx.mock
async def test_a_failed_restore_keeps_the_trash_id_in_the_answer(both):
    _mock_pc()
    _, upload, restore = _mock_gestor()
    upload.mock(return_value=httpx.Response(400, json={"error": "bad"}))
    restore.mock(return_value=httpx.Response(500, json={"error": "down"}))
    row = (await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False))["presets"][0]
    assert row["restored"] is False and row["backup"]["gestor_trash_id"] == "T1"


@respx.mock
async def test_a_gestor_copy_changed_meanwhile_is_not_replaced(both):
    _mock_pc()
    trash, upload, _ = _mock_gestor()
    respx.get(f"{GESTOR}/api/download").mock(side_effect=[
        httpx.Response(200, content=json.dumps(GESTOR_FILE).encode()),
        httpx.Response(200, content=json.dumps(GESTOR_FILE | {"nozzle_temperature": ["205"]}).encode())])
    row = (await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False))["presets"][0]
    assert row["error"] == "gestor_changed" and trash.call_count == 0 and upload.call_count == 0


@respx.mock
async def test_one_failing_preset_keeps_the_other_results(both):
    _mock_pc()
    _mock_gestor(entries=[{"path": "SM PLA - White.json", "name": "SM PLA - White", "kind": "filament"},
                          {"path": "0.20mm.json", "name": "0.20mm", "kind": "process"}])
    respx.get(f"{GESTOR}/api/download").mock(side_effect=[
        httpx.Response(200, content=b"{not json"), httpx.Response(404, json={"error": "gone"})])
    rows = {r["name"]: r for r in (await srv.sync_presets())["presets"]}
    assert rows["0.20mm"]["error"] == "gestor_error" and rows["0.20mm"]["status"] == 404
    assert rows["SM PLA - White"]["error"] == "gestor_file_invalid"


@respx.mock
async def test_a_name_in_two_groups_needs_a_type(both):
    _mock_pc()
    _mock_gestor(entries=[{"path": "SM PLA - White.json", "name": "SM PLA - White", "kind": "process"}])
    out = await srv.sync_presets("pc_to_gestor", ["SM PLA - White"])
    assert out["error"] == "ambiguous_name" and sorted(out["types"]) == ["filament", "print"]


@respx.mock
async def test_long_differences_are_truncated_with_the_full_count(both):
    many = {f"k{i}": str(i) for i in range(60)}
    result = ps.Comparison([ps.Difference(k, v, None) for k, v in many.items()])
    rows = srv._diff_rows(result)
    assert len(rows["differences"]) == srv._DIFF_LIMIT and rows["difference_count"] == 60


@respx.mock
async def test_redacted_credentials_keep_the_gestor_value_through_the_tool(both):
    _mock_pc()
    _, upload, _ = _mock_gestor()
    secret_pc = PC | {"printhost_apikey": "<redacted>"}
    respx.post(f"{BASE}/preset/config").mock(side_effect=lambda r: httpx.Response(200, json={
        "name": json.loads(r.read())["name"], "system": False,
        "config": secret_pc if json.loads(r.read())["name"] == "SM PLA - White" else PARENT}))
    respx.get(f"{GESTOR}/api/download").mock(return_value=httpx.Response(
        200, content=json.dumps(GESTOR_FILE | {"printhost_apikey": "keep"}).encode()))
    await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False)
    assert json.loads(upload.calls.last.request.read())["printhost_apikey"] == "keep"


def test_escaping_survives_separators_quotes_and_backslashes():
    assert ps.unescape_strings('"a;b";"c\\\\d"; e') == ["a;b", "c\\d", "e"]
    assert ps.from_api("compatible_printers", '"Creator 5 Pro 0.4 - Queue";FF') == ["Creator 5 Pro 0.4 - Queue", "FF"]


def test_a_bad_gestor_timeout_is_a_clear_error(monkeypatch):
    from orcaslicer_mcp.gestor import GestorError, load_gestor_config
    monkeypatch.setenv("ORCA_GESTOR_TIMEOUT", "soon")
    with pytest.raises(GestorError):
        load_gestor_config()


@respx.mock
async def test_a_trash_without_id_stops_before_uploading(both):
    _mock_pc()
    trash, upload, _ = _mock_gestor()
    trash.mock(return_value=httpx.Response(201, json={"entry": {}}))
    row = (await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False))["presets"][0]
    assert row["error"] == "backup_unconfirmed" and upload.call_count == 0


@respx.mock
async def test_a_stale_token_is_refreshed_once(both):
    _mock_pc()
    _, upload, _ = _mock_gestor()
    upload.mock(side_effect=[httpx.Response(403, json={"error": "La sesión ha caducado."}),
                             httpx.Response(201, json={"preset": {"path": "SM PLA - White.json"}})])
    row = (await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False))["presets"][0]
    assert row["written"] == "SM PLA - White.json" and upload.call_count == 2


@respx.mock
async def test_a_standalone_preset_compares_every_pc_key(both):
    standalone = {"inherits": "", "filament_settings_id": "\"SM PLA - White\"", "nozzle_temperature": "210",
                  "filament_type": "PLA"}
    respx.get(f"{BASE}/presets").mock(return_value=httpx.Response(200, json={
        "filament": [{"name": "SM PLA - White", "system": False}], "print": [], "printer": []}))
    respx.post(f"{BASE}/preset/config").mock(return_value=httpx.Response(200, json={
        "name": "SM PLA - White", "system": False, "config": standalone}))
    _mock_gestor(file={"name": "SM PLA - White", "inherits": "", "filament_settings_id": ["SM PLA - White"],
                       "nozzle_temperature": ["210"]})
    row = (await srv.sync_presets())["presets"][0]
    assert row["status"] == "different" and "note" not in row
    assert row["differences"] == [{"key": "filament_type", "pc": ["PLA"], "gestor": None}]


@respx.mock
async def test_compare_reports_a_preset_only_on_the_pc(both):
    _mock_pc()
    _mock_gestor(entries=[])
    rows = (await srv.sync_presets())["presets"]
    assert rows == [{"type": "filament", "name": "SM PLA - White", "status": "only_pc"}]


@respx.mock
async def test_a_conflict_when_creating_is_an_upload_failure_without_restore(both):
    _mock_pc()
    trash, upload, restore = _mock_gestor(entries=[])
    upload.mock(return_value=httpx.Response(409, json={"error": "Ya existe un preset con ese nombre."}))
    row = (await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False))["presets"][0]
    assert row["error"] == "upload_failed" and "Ya existe" in row["detail"] and "restored" not in row
    assert trash.call_count == 0 and restore.call_count == 0


@respx.mock
async def test_a_stale_token_on_trash_is_refreshed_once(both):
    _mock_pc()
    trash, upload, _ = _mock_gestor()
    trash.mock(side_effect=[httpx.Response(403, json={"error": "La sesión ha caducado."}),
                            httpx.Response(201, json={"entry": {"id": "T2"}})])
    row = (await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False))["presets"][0]
    assert row["backup"]["gestor_trash_id"] == "T2" and trash.call_count == 2 and upload.call_count == 1


@respx.mock
async def test_type_picks_the_group_of_a_name_found_in_two(both):
    _mock_pc()
    _mock_gestor(entries=[{"path": "SM PLA - White.json", "name": "SM PLA - White", "kind": "filament"},
                          {"path": "process/SM PLA - White.json", "name": "SM PLA - White", "kind": "process"}])
    out = await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], type="filament")
    row = out["presets"][0]
    assert row["type"] == "filament" and row["action"] == "replace"
    downloads = [c.request.url.params["path"] for c in respx.calls if c.request.url.path == "/api/download"]
    assert downloads == ["SM PLA - White.json"]


@respx.mock
async def test_an_empty_gestor_copy_is_invalid_not_a_new_preset(both):
    _mock_pc()
    trash, upload, _ = _mock_gestor()
    respx.get(f"{GESTOR}/api/download").mock(return_value=httpx.Response(200, content=b""))
    row = (await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False))["presets"][0]
    assert row["error"] == "gestor_file_invalid" and trash.call_count == 0 and upload.call_count == 0


@respx.mock
async def test_a_new_preset_name_that_is_not_a_safe_file_name_is_refused(both):
    respx.get(f"{BASE}/presets").mock(return_value=httpx.Response(200, json={
        "filament": [{"name": n, "system": False} for n in ("a/b", "a\\b", "..x")], "print": [], "printer": []}))
    respx.post(f"{BASE}/preset/config").mock(side_effect=lambda r: httpx.Response(200, json={
        "name": json.loads(r.read())["name"], "system": False, "config": PC}))
    respx.get(f"{BASE}/status").mock(return_value=httpx.Response(200, json={
        "presets": {"filaments": ["Other"]}, "modified": {}}))
    _, upload, _ = _mock_gestor(entries=[])
    out = await srv.sync_presets("pc_to_gestor", ["a/b", "a\\b", "..x"], dry_run=False)
    assert [r["error"] for r in out["presets"]] == ["invalid_name"] * 3 and upload.call_count == 0


@respx.mock
async def test_edits_made_while_comparing_stop_the_push(both):
    _mock_pc()
    trash, upload, _ = _mock_gestor()
    clean = {"presets": {"filaments": ["SM PLA - White"]}, "modified": {"filament": []}}
    edited = {"presets": {"filaments": ["SM PLA - White"]}, "modified": {"filament": ["nozzle_temperature"]}}
    respx.get(f"{BASE}/status").mock(side_effect=[httpx.Response(200, json=clean),
                                                  httpx.Response(200, json=edited)])
    row = (await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False))["presets"][0]
    assert row["error"] == "unsaved_changes" and row["modified"] == ["nozzle_temperature"]
    assert trash.call_count == 0 and upload.call_count == 0


@respx.mock
async def test_a_success_answer_that_is_not_json_is_a_gestor_error(both):
    _mock_pc()
    trash, upload, _ = _mock_gestor()
    trash.mock(return_value=httpx.Response(201, content=b"<html>ok</html>"))
    row = (await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False))["presets"][0]
    assert row["error"] == "gestor_error" and "/api/trash" in row["detail"] and upload.call_count == 0


@respx.mock
async def test_a_gestor_copy_that_is_json_but_not_an_object_is_invalid(both):
    _mock_pc()
    trash, upload, _ = _mock_gestor()
    for body in (b"null", b"[]", b"123"):
        respx.get(f"{GESTOR}/api/download").mock(return_value=httpx.Response(200, content=body))
        row = (await srv.sync_presets("pc_to_gestor", ["SM PLA - White"], dry_run=False))["presets"][0]
        assert row["error"] == "gestor_file_invalid"
    assert trash.call_count == 0 and upload.call_count == 0


@respx.mock
async def test_an_existing_gestor_copy_keeps_its_path_whatever_the_name(both):
    respx.get(f"{BASE}/presets").mock(return_value=httpx.Response(200, json={
        "filament": [{"name": "..x", "system": False}], "print": [], "printer": []}))
    respx.post(f"{BASE}/preset/config").mock(side_effect=lambda r: httpx.Response(200, json={
        "name": json.loads(r.read())["name"], "system": False, "config": PC}))
    respx.get(f"{BASE}/status").mock(return_value=httpx.Response(200, json={
        "presets": {"filaments": ["Other"]}, "modified": {}}))
    _, upload, _ = _mock_gestor(entries=[{"path": "x.json", "name": "..x", "kind": "filament"}])
    row = (await srv.sync_presets("pc_to_gestor", ["..x"], dry_run=False))["presets"][0]
    assert "error" not in row and upload.calls.last.request.url.params["filename"] == "x.json"
