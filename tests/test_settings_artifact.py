import json
from importlib.resources import files


def _doc():
    p = files("orcaslicer_mcp").joinpath("data/print_settings_schema.json")
    return json.loads(p.read_text(encoding="utf-8"))


def test_artifact_has_meta_and_many_settings():
    doc = _doc()
    assert "_meta" in doc
    settings = {k: v for k, v in doc.items() if not k.startswith("_")}
    assert len(settings) >= 780
    assert doc["_meta"]["setting_count"] == len(settings)
    # broad coverage: most settings carry a human label
    assert doc["_meta"]["with_label"] >= 680


def test_known_float_setting():
    s = _doc()["layer_height"]
    assert s["type"] == "coFloat"
    assert s["label"]
    assert s["tooltip"]
    assert s["unit"] == "mm"


def test_known_enum_setting_has_values():
    s = _doc()["sparse_infill_pattern"]
    assert s["type"] == "coEnum"
    assert isinstance(s["enum_values"], list) and "gyroid" in s["enum_values"]


def test_no_phantom_commented_setting():
    # adaptive_layer_height is commented out in source; must not appear
    assert "adaptive_layer_height" not in _doc()


def test_versions_are_marked_for_every_line():
    # Built from 2.4.2, 2.5.0, 2.5.1 and the ErikAnswer fork (scripts/merge_settings_schemas.py): a
    # 2.5-only key says so, a key 2.5 dropped is still described with the version that removed it,
    # and a key only our build has says "since": "fork".
    doc = _doc()
    assert list(doc["_meta"]["versions"]) == ["2.4.2", "2.5.0", "2.5.1", "fork"]
    assert doc["slicing_pipeline_plugin"]["since"] == "2.5.0"
    assert doc["silent_mode"]["removed_in"] == "2.5.0"
    assert "since" not in doc["layer_height"] and "removed_in" not in doc["layer_height"]
    assert doc["prime_tower_side_scrape"]["since"] == "fork"


def test_every_fork_key_is_described():
    fork = {k: v for k, v in _doc().items() if not k.startswith("_") and v.get("since") == "fork"}
    assert {"filament_prime_tower_speed_factor_curve", "prime_tower_side_scrape", "prime_tower_tail_speed_factor",
            "enable_circle_compensation", "hole_coef_1", "counter_limit_max", "enable_height_slowdown",
            "cooling_slowdown_logic", "custom_parameters_filament"} <= set(fork)
    assert all(r["label"] and r["tooltip"] and r["type"] for r in fork.values())
