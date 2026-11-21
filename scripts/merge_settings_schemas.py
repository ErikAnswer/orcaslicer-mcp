"""Merge extracted settings schemas (scripts/extract_settings_schema.py output) into the bundled
one, so describe_setting and search_settings serve users of every line given, oldest first.

The newest schema holding a key gives its record. A key missing from the first schema is marked
"since" with the first version that has it; a key missing from the last one stays, marked
"removed_in" with the version after the last that has it; when an older choice list differs
from the one kept, it stays as "enum_values_<older>" next to it.

The last schema is usually the ErikAnswer/OrcaSlicer fork, labelled "fork", so the keys only
our build has say "since": "fork":

    python scripts/merge_settings_schemas.py 2.4.2.json 2.4.2 2.5.1.json 2.5.1 fork.json fork [--out PATH]
"""
import argparse
import json
from pathlib import Path

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "src/orcaslicer_mcp/data/print_settings_schema.json"


def _keys(doc: dict) -> set:
    return {k for k in doc if not k.startswith("_")}


def _merge_key(k: str, layers: list) -> dict:
    present = [i for i, (_, doc) in enumerate(layers) if k in doc]
    first, last = present[0], present[-1]
    rec = dict(layers[last][1][k])
    if first > 0:
        rec["since"] = layers[first][0]
    if last < len(layers) - 1:
        rec["removed_in"] = layers[last + 1][0]
    kept = rec.get("enum_values") or []
    for i in present[:-1]:
        ver, doc = layers[i]
        if (doc[k].get("enum_values") or []) != kept:
            rec["enum_values_" + ver.replace(".", "_")] = doc[k].get("enum_values") or []
    return rec


def merge_layers(layers: list) -> dict:
    """layers: [(version, schema), ...], oldest first."""
    if len(layers) < 2:
        raise ValueError("merge needs at least two schemas")
    out = {k: _merge_key(k, layers) for k in sorted(set().union(*(_keys(d) for _, d in layers)))}
    meta = {
        "source": "PrintConfig.cpp",
        "versions": {ver: doc.get("_meta", {}) for ver, doc in layers},
        "setting_count": len(out),
        "with_label": sum(1 for r in out.values() if r.get("label")),
        "with_label_and_tooltip": sum(1 for r in out.values() if r.get("label") and r.get("tooltip")),
        "since_count": sum(1 for r in out.values() if "since" in r),
        "removed_count": sum(1 for r in out.values() if "removed_in" in r),
    }
    return {"_meta": meta, **out}


def merge(old: dict, old_ver: str, new: dict, new_ver: str) -> dict:
    return merge_layers([(old_ver, old), (new_ver, new)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs", nargs="+", metavar="SCHEMA VERSION",
                    help="schema file and its version label, repeated, oldest first")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args()
    if len(a.pairs) < 4 or len(a.pairs) % 2:
        ap.error("give at least two SCHEMA VERSION pairs")
    layers = [(ver, json.loads(Path(path).read_text(encoding="utf-8")))
              for path, ver in zip(a.pairs[::2], a.pairs[1::2])]
    doc = merge_layers(layers)
    a.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    m = doc["_meta"]
    by_since = {}
    for r in doc.values():
        if "since" in r:
            by_since[r["since"]] = by_since.get(r["since"], 0) + 1
    since = ", ".join(f"{n} since {v}" for v, n in by_since.items()) or "none new"
    print(f"{m['setting_count']} settings ({since}; {m['removed_count']} removed) -> {a.out}")


if __name__ == "__main__":
    main()
