import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "merge_settings_schemas", Path(__file__).resolve().parents[1] / "scripts/merge_settings_schemas.py")
msm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(msm)


def _rec(label, enum=None):
    return {"label": label, "tooltip": label, "enum_values": enum}


def test_chain_marks_since_removed_and_older_choices():
    a = {"_meta": {"n": 1}, "kept": _rec("a", ["x"]), "gone": _rec("old")}
    b = {"_meta": {"n": 2}, "kept": _rec("b", ["x", "y"]), "gone": _rec("old"), "mid": _rec("m")}
    c = {"_meta": {"n": 3}, "kept": _rec("c", ["x", "y"]), "mid": _rec("m"), "own": _rec("f")}
    doc = msm.merge_layers([("1.0", a), ("2.0", b), ("fork", c)])
    assert list(doc["_meta"]["versions"]) == ["1.0", "2.0", "fork"]
    assert doc["kept"]["label"] == "c" and doc["kept"]["enum_values_1_0"] == ["x"]
    assert "enum_values_2_0" not in doc["kept"] and "since" not in doc["kept"]
    assert doc["gone"]["removed_in"] == "fork" and "since" not in doc["gone"]
    assert doc["mid"]["since"] == "2.0" and "removed_in" not in doc["mid"]
    assert doc["own"]["since"] == "fork"
    assert (doc["_meta"]["since_count"], doc["_meta"]["removed_count"]) == (2, 1)


def test_two_schemas_merge_as_before():
    old = {"k": _rec("o"), "d": _rec("d")}
    new = {"k": _rec("n"), "s": _rec("s")}
    doc = msm.merge(old, "2.4.2", new, "2.5.0")
    assert doc["d"]["removed_in"] == "2.5.0" and doc["s"]["since"] == "2.5.0" and doc["k"]["label"] == "n"
