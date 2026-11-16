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
NAME = "SM PLA - White"
BASE_NAME = "Generic PLA @System"

PARENT = {"inherits": "", "filament_settings_id": "\"Generic PLA @System\"", "nozzle_temperature": "220",
          "filament_flow_ratio": "0.98", "filament_vendor": "Generic", "compatible_printers": "",
          "filament_start_gcode": "\"; filament start gcode\\n\"", "pressure_advance": "0.02"}
PC = PARENT | {"inherits": BASE_NAME, "filament_settings_id": "\"SM PLA - White\"",
               "nozzle_temperature": "210", "filament_vendor": "\"Smart Materials\"",
               "pressure_advance": "0.036", "filament_flow_ratio": "0.95"}
GESTOR_FILE = {"from": "User", "inherits": BASE_NAME, "name": NAME,
               "filament_settings_id": [NAME], "filament_vendor": ["Smart Materials"],
               "nozzle_temperature": ["210"], "pressure_advance": ["0.025"], "version": "2.4.0.4"}


class FakeOrca:
    """Just enough of the slicer: presets, the edited config of the selected one, saving."""

    def __init__(self, presets: dict, modified: dict | None = None, selected: str = "Other"):
        self.presets = {k: dict(v) for k, v in presets.items()}
        self.modified = modified or {}
        self.selected = {"print": "p", "printer": "q", "filament": [selected, "Other 2"]}
        self.tab = selected
        self.edited: dict = {}
        self.edited_from: str | None = None
        self.put_errors: dict = {}
        self.reselect_keeps_edits = False
        self.select_error: str | None = None
        self.select_error_when_clean = False
        self.put_failures_after: int | None = None
        self.filaments_failures: set = set()
        self.filaments_reads = 0
        self.tab_select_error_for: str | None = None
        self.writes: list = []

    def install(self):
        respx.get(f"{BASE}/presets").mock(side_effect=lambda r: httpx.Response(200, json={
            "filament": [{"name": n, "system": n.endswith("@System")} for n in self.presets],
            "print": [], "printer": []}))
        respx.post(f"{BASE}/preset/config").mock(side_effect=self._config)
        respx.get(f"{BASE}/status").mock(side_effect=lambda r: httpx.Response(200, json={
            "presets": {"print": self.selected["print"], "printer": self.selected["printer"],
                        "filaments": list(self.selected["filament"])},
            "modified": {"print": [], "printer": [], "filament": self._edited_keys()} | self.modified}))
        respx.put(f"{BASE}/preset").mock(side_effect=self._select)
        respx.get(f"{BASE}/filaments").mock(side_effect=self._filaments)
        respx.put(f"{BASE}/config").mock(side_effect=self._put)
        respx.post(f"{BASE}/preset/save").mock(side_effect=self._save)

    def _edited_keys(self) -> list:
        saved = self.presets.get(self.edited_from, {}) if self.edited_from else {}
        return sorted(k for k, v in self.edited.items() if saved.get(k) != v)

    def _config(self, request):
        name = json.loads(request.read())["name"]
        if name not in self.presets:
            return httpx.Response(404, json={"error": "unknown_preset"})
        return httpx.Response(200, json={"name": name, "system": name.endswith("@System"),
                                         "config": self.presets[name]})

    def _filaments(self, request):
        self.filaments_reads += 1
        if self.filaments_reads in self.filaments_failures:
            return httpx.Response(500, json={"error": "internal"})
        return httpx.Response(200, json={
            "filaments": [{"index": i, "preset": n} for i, n in enumerate(self.selected["filament"])],
            "tab": {"preset": self.tab, "slot": None}})

    def _select(self, request):
        body = json.loads(request.read())
        self.writes.append(("select", body))
        if "slot" not in body and body["name"] == self.tab_select_error_for:
            return httpx.Response(500, json={"error": "internal"})
        after_put = any(w[0] == "put" for w in self.writes) and self.select_error_when_clean
        if (self._edited_keys() or after_put) and self.select_error:
            return httpx.Response(500, json={"error": self.select_error})
        if self._edited_keys() and self.reselect_keeps_edits:
            return httpx.Response(200, json={"selected": body["name"]})
        if "slot" in body:
            self.selected["filament"][body["slot"]] = body["name"]
        else:
            self.tab = body["name"]
            self.edited = dict(self.presets.get(body["name"], {}))
            self.edited_from = body["name"]
        return httpx.Response(200, json={"selected": body["name"]})

    def _put(self, request):
        changes = json.loads(request.read())
        puts = sum(w[0] == "put" for w in self.writes)
        self.writes.append(("put", changes))
        if self.put_failures_after is not None and puts >= self.put_failures_after:
            return httpx.Response(500, json={"error": "internal"})
        errors, self.put_errors = self.put_errors, {}
        self.edited |= {k: v for k, v in changes.items() if k not in errors}
        return httpx.Response(200, json={"applied": sorted(set(changes) - set(errors)), "errors": errors})

    def _save(self, request):
        body = json.loads(request.read())
        self.writes.append(("save", body))
        inherits = self.edited_from if self.edited_from.endswith("@System") else self.edited.get("inherits", "")
        self.presets[body["name"]] = self.edited | {"inherits": inherits,
                                                    "filament_settings_id": f"\"{body['name']}\""}
        self.tab = body["name"]
        self.edited, self.edited_from = dict(self.presets[body["name"]]), body["name"]
        return httpx.Response(200, json={"saved": body["name"]})


@pytest.fixture
def both(monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "_client", lambda: OrcaClient(Config(base_url="http://x:13130", token="t", timeout=5)))
    monkeypatch.setenv("ORCA_GESTOR_URL", GESTOR)
    monkeypatch.setenv("ORCA_PRESET_BACKUP_DIR", str(tmp_path / "backups"))
    return tmp_path / "backups"


def _gestor(file=GESTOR_FILE, entries=None):
    entries = [{"path": f"{NAME}.json", "name": NAME, "kind": "filament"}] if entries is None else entries
    respx.get(f"{GESTOR}/api/state").mock(return_value=httpx.Response(200, json={"presets": entries}))
    respx.get(f"{GESTOR}/api/download").mock(return_value=httpx.Response(200, content=json.dumps(file).encode()))


def test_api_text_round_trips_file_values():
    assert ps.to_api("filament_vendor", ["Smart Materials"]) == '"Smart Materials"'
    assert ps.to_api("filament_settings_id", ["a;b", "c"]) == '"a;b";c'
    assert ps.to_api("nozzle_temperature", ["210", "215"]) == "210,215"
    assert ps.to_api("machine_start_gcode", 'G28\nM117 "hi" \\') == 'G28\\nM117 \\"hi\\" \\\\'
    assert ps.to_api("layer_height", "0.2") == "0.2"
    assert ps.to_api("bed_custom_texture", "C:\\t.png") == "C:\\t.png"
    for key, text in [("filament_vendor", '"a b";;c'), ("machine_start_gcode", 'G1\\n\\"x\\"'),
                      ("bed_exclude_area", "0x0,10x0"), ("nozzle_temperature", "200")]:
        assert ps.to_api(key, ps.from_api(key, text)) == text


def test_target_changes_lay_the_file_over_the_parent():
    changes = ps.target_changes(GESTOR_FILE, PC, PARENT)
    assert changes == {"filament_flow_ratio": "0.98", "pressure_advance": "0.025"}
    assert ps.target_changes(GESTOR_FILE, PARENT, PARENT) == {
        "filament_vendor": '"Smart Materials"', "nozzle_temperature": "210", "pressure_advance": "0.025"}


def test_target_changes_leave_redacted_credentials_alone():
    start = {"printhost_apikey": "<redacted>", "layer_height": "0.2"}
    assert ps.target_changes({"printhost_apikey": "x", "layer_height": "0.3"}, start, {}) == {"layer_height": "0.3"}


@respx.mock
async def test_dry_run_shows_differences_and_writes_nothing(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT})
    pc.install()
    _gestor()
    out = await srv.sync_presets("gestor_to_pc", [NAME])
    row = out["presets"][0]
    assert row["action"] == "replace" and row["dry_run"] is True and row["start_from"] == NAME
    assert {d["key"] for d in row["differences"]} == {"filament_flow_ratio", "pressure_advance"}
    assert row["keys_to_write"] == 2
    assert pc.writes == [] and not both.exists()


@respx.mock
async def test_pull_writes_backs_up_and_ends_the_same(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT})
    pc.install()
    _gestor()
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["written"] is True and row["verified"] is True
    assert pc.writes[0] == ("select", {"type": "filament", "name": NAME})
    assert pc.writes[1] == ("put", {"filament_flow_ratio": "0.98", "pressure_advance": "0.025"})
    assert pc.writes[2][0] == "save" and pc.writes[2][1]["name"] == NAME
    backup = json.loads(open(row["backup"]).read())
    assert backup["config"]["pressure_advance"] == "0.036"
    assert row["reselected"] == [{"type": "filament", "name": "Other", "tab": True}]
    again = (await srv.sync_presets("compare", [NAME]))["presets"][0]
    assert again["status"] == "same"


@respx.mock
async def test_a_new_preset_starts_from_its_parent_without_backup(both):
    pc = FakeOrca({BASE_NAME: PARENT})
    pc.install()
    _gestor()
    row = (await srv.sync_presets("gestor_to_pc", [NAME], type="filament", dry_run=False))["presets"][0]
    assert row["action"] == "create" and row["start_from"] == BASE_NAME
    assert "backup" not in row and row["verified"] is True
    assert pc.presets[NAME]["inherits"] == BASE_NAME


@respx.mock
async def test_unsaved_edits_anywhere_refuse_before_touching_anything(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT}, modified={"print": ["layer_height"]})
    pc.install()
    _gestor()
    out = await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False)
    assert out["error"] == "unsaved_changes" and out["modified"] == {"print": ["layer_height"]}
    assert pc.writes == [] and not both.exists()


@respx.mock
async def test_edits_made_after_the_first_check_still_stop_the_write(both, monkeypatch):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT})
    pc.install()
    _gestor()
    original = srv._pull_one

    async def edit_meanwhile(*args):
        pc.modified = {"filament": ["nozzle_temperature"]}
        return await original(*args)
    monkeypatch.setattr(srv, "_pull_one", edit_meanwhile)
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["error"] == "unsaved_changes" and pc.writes == []


@respx.mock
async def test_protected_keys_are_reported_not_written(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT})
    pc.install()
    _gestor(GESTOR_FILE | {"filament_start_gcode": ["M104 S0"], "post_process": ["evil.sh"]})
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    skipped = {s["key"]: s for s in row["skipped_keys"]}
    assert set(skipped) == {"filament_start_gcode", "post_process"}
    assert skipped["filament_start_gcode"]["pc_keeps"] == ["; filament start gcode\n"]
    put = next(w[1] for w in pc.writes if w[0] == "put")
    assert "filament_start_gcode" not in put and "post_process" not in put
    assert row["verified"] is True


@respx.mock
async def test_already_the_same_writes_nothing(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT})
    pc.install()
    _gestor(GESTOR_FILE | {"pressure_advance": ["0.036"], "filament_flow_ratio": ["0.95"]})
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row == {"type": "filament", "name": NAME, "action": "none", "status": "same"}
    assert pc.writes == []


@respx.mock
async def test_a_different_parent_starts_from_the_files_parent(both):
    other = PARENT | {"filament_settings_id": "\"Other PLA @System\""}
    pc = FakeOrca({NAME: PC | {"inherits": "Other PLA @System"}, BASE_NAME: PARENT, "Other PLA @System": other})
    pc.install()
    _gestor()
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["start_from"] == BASE_NAME and row["verified"] is True
    assert pc.presets[NAME]["inherits"] == BASE_NAME


@respx.mock
async def test_refusals_unknown_base_standalone_system_and_missing(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT})
    pc.install()
    _gestor(GESTOR_FILE | {"inherits": "Missing @System"})
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["error"] == "unknown_base"
    _gestor(GESTOR_FILE | {"inherits": "", "name": "Loose"},
            entries=[{"path": "Loose.json", "name": "Loose", "kind": "filament"}])
    row = (await srv.sync_presets("gestor_to_pc", ["Loose"], type="filament", dry_run=False))["presets"][0]
    assert row["error"] == "no_base"
    _gestor(entries=[{"path": "x.json", "name": BASE_NAME, "kind": "filament"}])
    row = (await srv.sync_presets("gestor_to_pc", [BASE_NAME], dry_run=False))["presets"][0]
    assert row["error"] == "system_preset"
    _gestor(entries=[])
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["error"] == "not_in_gestor"
    assert (await srv.sync_presets("gestor_to_pc"))["error"] == "names_required"
    assert pc.writes == []


@respx.mock
async def test_a_failed_save_still_restores_the_selection(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT})
    pc.install()
    respx.post(f"{BASE}/preset/save").mock(return_value=httpx.Response(
        409, json={"error": "temperature_pairs_unsafe"}))
    _gestor()
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["error"] == "write_failed" and "backup" in row
    assert row["reselected"] == [{"type": "filament", "name": "Other", "tab": True}]
    assert pc.presets[NAME] == PC


@respx.mock
async def test_no_backup_no_write(both, monkeypatch):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT})
    pc.install()
    _gestor()

    def fail(*args):
        raise OSError("read-only")
    monkeypatch.setattr(srv, "_write_pc_backup", fail)
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["error"] == "backup_failed" and pc.writes == []


def test_backup_file_name_is_safe(both):
    path = srv._write_pc_backup("filament", "../a/b:c", {"config": {}})
    assert path.startswith(str(both)) and "/" not in path[len(str(both)) + 1:]


@respx.mock
async def test_a_failed_save_on_the_selected_preset_discards_the_half_applied_edits(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT}, selected=NAME)
    pc.install()
    respx.post(f"{BASE}/preset/save").mock(return_value=httpx.Response(
        409, json={"error": "temperature_pairs_unsafe"}))
    _gestor()
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["error"] == "write_failed" and row["edits_discarded"] is True
    assert row["reselected"] == []
    assert pc.writes[-2:] == [("put", {"filament_flow_ratio": "0.95", "pressure_advance": "0.036"}),
                              ("select", {"type": "filament", "name": NAME})]
    assert pc.edited == PC


@respx.mock
async def test_only_protected_differences_leave_the_pc_alone(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT})
    pc.install()
    _gestor(GESTOR_FILE | {"pressure_advance": ["0.036"], "filament_flow_ratio": ["0.95"],
                           "filament_start_gcode": ["M104 S0"]})
    for dry_run in (True, False):
        row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=dry_run))["presets"][0]
        assert row["action"] == "none" and row["status"] == "only_protected_keys_differ"
        assert [s["key"] for s in row["skipped_keys"]] == ["filament_start_gcode"]
        assert "written" not in row and "backup" not in row
    assert pc.writes == [] and not both.exists()


@respx.mock
async def test_refused_keys_are_reported_and_the_rest_put_back(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT}, selected=NAME)
    pc.install()
    pc.put_errors = {"pressure_advance": "out_of_range"}
    _gestor()
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["error"] == "invalid_keys" and row["errors"] == {"pressure_advance": "out_of_range"}
    assert row["edits_discarded"] is True and "still_modified" not in row
    assert not any(w[0] == "save" for w in pc.writes)
    assert pc.edited == PC and pc.presets[NAME] == PC


def _failed_save_with_a_new_key(pc):
    pc.install()
    respx.post(f"{BASE}/preset/save").mock(return_value=httpx.Response(
        409, json={"error": "temperature_pairs_unsafe"}))
    _gestor(GESTOR_FILE | {"filament_cooling_final_speed": ["3.4"]})


@respx.mock
async def test_a_new_key_left_after_the_rollback_is_dropped_by_selecting_the_preset_again(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT}, selected=NAME)
    _failed_save_with_a_new_key(pc)
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["error"] == "write_failed" and row["edits_discarded"] is True
    assert "still_modified" not in row and "hint" not in row
    assert [w for w in pc.writes if w[0] == "select"] == [("select", {"type": "filament", "name": NAME})] * 2
    assert pc.edited == PC and pc.presets[NAME] == PC and pc.tab == NAME


@respx.mock
async def test_a_new_key_that_selecting_again_does_not_drop_is_reported(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT}, selected=NAME)
    _failed_save_with_a_new_key(pc)
    pc.reselect_keeps_edits = True
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["error"] == "write_failed" and row["edits_discarded"] is False
    assert row["still_modified"] == ["filament_cooling_final_speed"] and "hint" in row
    assert pc.presets[NAME] == PC


@respx.mock
async def test_a_refused_reselect_after_the_rollback_is_reported(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT}, selected=NAME)
    _failed_save_with_a_new_key(pc)
    pc.select_error = "select_cancelled"
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["error"] == "write_failed" and row["edits_discarded"] is False
    assert row["still_modified"] == ["filament_cooling_final_speed"]
    assert "select_cancelled" in row["discard_error"] and "hint" in row
    assert pc.presets[NAME] == PC


@respx.mock
async def test_a_refused_reselect_with_nothing_left_to_drop_counts_as_discarded(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT}, selected=NAME)
    pc.install()
    respx.post(f"{BASE}/preset/save").mock(return_value=httpx.Response(
        409, json={"error": "temperature_pairs_unsafe"}))
    _gestor()
    pc.select_error = "select_cancelled"
    pc.select_error_when_clean = True
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["error"] == "write_failed" and row["edits_discarded"] is True
    assert "discard_error" not in row and "hint" not in row and pc.edited == PC


@respx.mock
async def test_a_failed_rollback_is_still_dropped_by_selecting_the_preset_again(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT}, selected=NAME)
    _failed_save_with_a_new_key(pc)
    pc.put_failures_after = 1
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["error"] == "write_failed" and row["edits_discarded"] is True
    assert "discard_error" not in row and pc.edited == PC


@respx.mock
async def test_a_new_key_is_dropped_when_the_preset_is_on_the_filament_tab_but_in_no_slot(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT})
    pc.tab = NAME
    _failed_save_with_a_new_key(pc)
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["error"] == "write_failed" and row["edits_discarded"] is True
    assert "still_modified" not in row and row["reselected"] == []
    assert pc.edited == PC and pc.tab == NAME


@respx.mock
async def test_a_new_key_left_on_the_filament_tab_outside_every_slot_is_reported(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT})
    pc.tab = NAME
    _failed_save_with_a_new_key(pc)
    pc.reselect_keeps_edits = True
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["edits_discarded"] is False
    assert row["still_modified"] == ["filament_cooling_final_speed"] and "hint" in row


class SelectionClient:
    def __init__(self, presets: dict, printer_switches: dict | None = None):
        self.presets = presets
        self.printer_switches = printer_switches or {}
        self.selects: list = []

    async def get_status(self):
        return {"presets": {k: list(v) if isinstance(v, list) else v for k, v in self.presets.items()}}

    async def select_preset(self, ptype, name, slot=None):
        self.selects.append((ptype, name, slot))
        if ptype == "filament":
            self.presets["filament"][slot] = name
            return
        self.presets[ptype] = name
        if ptype == "printer":
            self.presets |= self.printer_switches


async def test_restore_selection_puts_back_printer_print_and_every_filament_slot():
    before = {"printer": "C5P 0.4", "print": "0.20 Standard", "filament": ["PLA A", "PETG B", "TPU C"]}
    c = SelectionClient({"printer": "Other printer", "print": "0.20 Standard", "filament": ["SM PLA", "PETG B", "X"]},
                        printer_switches={"print": "0.16 Fine", "filament": ["PLA A", "Y", "X"]})
    restored = await srv._restore_selection(c, before)
    assert c.presets == before
    assert restored == [{"type": "printer", "name": "C5P 0.4"}, {"type": "print", "name": "0.20 Standard"},
                        {"type": "filament", "name": "PETG B", "slot": 1},
                        {"type": "filament", "name": "TPU C", "slot": 2}]
    assert [s[0] for s in c.selects[:2]] == ["printer", "print"]


async def test_restore_selection_leaves_matching_and_missing_slots_alone():
    before = {"printer": "q", "print": "p", "filament": ["A", "B", "C"]}
    c = SelectionClient({"printer": "q", "print": "p", "filament": ["A", "Z"]})
    restored = await srv._restore_selection(c, before)
    assert restored == [{"type": "filament", "name": "B", "slot": 1}]
    assert c.selects == [("filament", "B", 1)]


def test_one_item_vectors_match_the_per_variant_vectors_of_orcaslicer_2_5():
    variants = ["Direct Drive Standard", "Direct Drive High Flow", "Bowden Standard"]
    assert ps.same_value("nozzle_temperature", "230,230,230", ["230"], per_variant=True)
    assert ps.same_value("filament_extruder_variant", variants, ["Direct Drive Standard"], per_variant=True)
    assert not ps.same_value("filament_extruder_variant", variants, ["Bowden Standard"], per_variant=True)
    assert not ps.same_value("nozzle_temperature", "230,235,230", ["230"], per_variant=True)
    assert not ps.same_value("nozzle_temperature", "230,230", ["230", "235", "230"], per_variant=True)
    assert not ps.same_value("nozzle_temperature", "", ["230"], per_variant=True)
    pc = PC | {"nozzle_temperature": "210,210,210", "pressure_advance": "0.036,0.036,0.036",
               "filament_extruder_variant": '"Direct Drive Standard";"Direct Drive High Flow";"Bowden Standard"'}
    file = GESTOR_FILE | {"filament_extruder_variant": ["Direct Drive Standard"]}
    comparison = ps.compare(pc, file, PARENT, per_variant=True)
    assert {d.key for d in comparison.differences} == {"filament_flow_ratio", "pressure_advance"}
    changes = ps.target_changes(file, pc, PARENT, per_variant=True)
    assert changes == {"filament_flow_ratio": "0.98", "pressure_advance": "0.025,0.025,0.025"}


def test_filament_keys_outside_the_per_variant_set_keep_their_length_strict():
    assert not ps.same_value("filament_colour", ["#FFFFFF", "#FFFFFF"], ["#FFFFFF"], per_variant=True)
    assert not ps.same_value("filament_cost", "20,20,20", ["20"], per_variant=True)
    assert ps.same_value("filament_cost", "20", ["20"], per_variant=True)
    assert ps.fit_to_current("filament_cost", ["20"], ["20", "20", "20"], per_variant=True) == ["20"]
    pc = PC | {"filament_cost": "20,20,20", "nozzle_temperature": "210,210,210"}
    file = GESTOR_FILE | {"filament_cost": ["20"]}
    assert "filament_cost" in {d.key for d in ps.compare(pc, file, PARENT, per_variant=True).differences}
    assert ps.target_changes(file, pc, PARENT, per_variant=True)["filament_cost"] == "20"


def test_the_per_variant_set_holds_only_vector_options_the_schema_knows():
    vectors = ps._COMMA_VECTORS | {"coStrings"}
    unknown = {key for key in ps._PER_VARIANT_KEYS if ps.option_type(key) not in vectors}
    assert unknown == set()


def test_printer_and_print_vectors_keep_their_length_strict():
    assert not ps.same_value("nozzle_diameter", "0.4,0.4", ["0.4"])
    assert ps.fit_to_current("nozzle_diameter", ["0.4"], ["0.4", "0.4"]) == ["0.4"]
    machine = {"inherits": "", "nozzle_diameter": "0.4,0.4"}
    assert {d.key for d in ps.compare(machine, {"nozzle_diameter": ["0.4"]}, {}).differences} == {"nozzle_diameter"}
    assert ps.target_changes({"nozzle_diameter": ["0.4"]}, machine, {}) == {"nozzle_diameter": "0.4"}


@respx.mock
async def test_nozzle_bookkeeping_of_orcaslicer_2_5_is_not_an_unsaved_edit(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT},
                  modified={"printer": ["extruder_nozzle_stats#0", "extruder_nozzle_stats#1"]})
    pc.install()
    _gestor()
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["written"] is True and row["verified"] is True


@respx.mock
async def test_the_filament_tab_goes_back_and_the_sidebar_slots_stay(both):
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT}, selected="Slot 1")
    pc.selected["filament"] = ["Slot 1", "Slot 2", NAME, "Slot 4"]
    pc.tab = "Slot 2"
    pc.install()
    _gestor()
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["verified"] is True
    assert pc.selected["filament"] == ["Slot 1", "Slot 2", NAME, "Slot 4"] and pc.tab == "Slot 2"
    assert row["reselected"] == [{"type": "filament", "name": "Slot 2", "tab": True}]


def _four_slots_with_another_preset_on_the_tab() -> FakeOrca:
    pc = FakeOrca({NAME: PC, BASE_NAME: PARENT}, selected="Slot 1")
    pc.selected["filament"] = ["Slot 1", "Slot 2", NAME, "Slot 4"]
    pc.tab = "Slot 2"
    pc.install()
    _gestor()
    return pc


@respx.mock
async def test_a_filament_tab_that_cannot_be_read_before_the_write_is_reported(both):
    pc = _four_slots_with_another_preset_on_the_tab()
    pc.filaments_failures = {1}
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["written"] is True and row["verified"] is True
    assert row["reselected"] == [] and pc.tab == NAME
    assert row["filament_tab"]["restored"] is False and row["filament_tab"]["reason"] == "not_read"
    assert row["filament_tab"]["detail"] and "Filament tab" in row["filament_tab"]["hint"]
    assert pc.selected["filament"] == ["Slot 1", "Slot 2", NAME, "Slot 4"]


@respx.mock
async def test_a_filament_tab_that_cannot_be_selected_again_is_reported(both):
    pc = _four_slots_with_another_preset_on_the_tab()
    pc.tab_select_error_for = "Slot 2"
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["written"] is True and row["verified"] is True
    assert row["reselected"] == [] and "reselect_error" not in row and pc.tab == NAME
    assert row["filament_tab"] == {"restored": False, "reason": "not_selected", "wanted": "Slot 2",
                                   "detail": row["filament_tab"]["detail"],
                                   "hint": row["filament_tab"]["hint"]}
    assert row["filament_tab"]["detail"] and "Slot 2" not in row["filament_tab"]["hint"]


@respx.mock
async def test_a_failed_write_still_reports_the_filament_tab_it_could_not_put_back(both):
    pc = _four_slots_with_another_preset_on_the_tab()
    pc.put_errors = {"pressure_advance": "out of range"}
    pc.tab_select_error_for = "Slot 2"
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["error"] == "invalid_keys"
    assert row["filament_tab"]["reason"] == "not_selected" and row["filament_tab"]["wanted"] == "Slot 2"


@respx.mock
async def test_the_filament_tab_is_selected_again_when_it_cannot_be_read_after_the_write(both):
    pc = _four_slots_with_another_preset_on_the_tab()
    pc.filaments_failures = {2}
    row = (await srv.sync_presets("gestor_to_pc", [NAME], dry_run=False))["presets"][0]
    assert row["verified"] is True and "filament_tab" not in row and pc.tab == "Slot 2"
    assert row["reselected"] == [{"type": "filament", "name": "Slot 2", "tab": True}]


async def test_restore_selection_keeps_what_it_restored_when_the_tab_fails():
    class TabFails(SelectionClient):
        async def get_filaments(self):
            return {"tab": {"preset": "Other"}}

        async def select_preset(self, ptype, name, slot=None):
            if ptype == "filament" and slot is None:
                raise srv.ApiError("internal")
            await super().select_preset(ptype, name, slot)

    c = TabFails({"printer": "q", "print": "p", "filament": ["A", "Z"]})
    with pytest.raises(srv._FilamentTabNotRestored) as caught:
        await srv._restore_selection(c, {"printer": "q", "print": "p", "filament": ["A", "B"]}, "Tab preset")
    assert caught.value.wanted == "Tab preset"
    assert caught.value.restored == [{"type": "filament", "name": "B", "slot": 1}]
