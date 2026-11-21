#!/usr/bin/env python3
"""Extract OrcaSlicer setting definitions from PrintConfig.cpp into a bundled JSON."""
from __future__ import annotations
import argparse, datetime, json, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from orcaslicer_mcp.schema_extract import parse_print_config  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "src/orcaslicer_mcp/data/print_settings_schema.json"


def _git(src: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(src.parent), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout


def git_sha(src: Path, ref: str = "HEAD") -> str:
    try:
        return _git(src, "rev-parse", "--short=8", ref).strip()
    except Exception:
        return "unknown"


def read_source(src: Path, ref: str | None) -> str:
    if ref is None:
        return src.read_text(encoding="utf-8", errors="replace")
    prefix = _git(src, "rev-parse", "--show-prefix").strip()
    return _git(src, "show", f"{ref}:{prefix}{src.name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True,
                    help="OrcaSlicer source file src/libslic3r/PrintConfig.cpp")
    ap.add_argument("--ref", default=None,
                    help="read --src as it is at this tag or commit of its git checkout, e.g. v2.4.2")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--min-count", type=int, default=600)
    ap.add_argument("--date", default=None, help="override extraction date (YYYY-MM-DD)")
    args = ap.parse_args()

    text = read_source(args.src, args.ref)
    settings, unparsed = parse_print_config(text)
    with_label = sum(1 for r in settings.values() if r.get("label"))
    with_tip = sum(1 for r in settings.values() if r.get("label") and r.get("tooltip"))
    meta = {
        "source": args.src.name,
        "source_git_sha": git_sha(args.src, args.ref or "HEAD"),
        "extracted": args.date or datetime.date.today().isoformat(),
        "setting_count": len(settings),
        "with_label": with_label,
        "with_label_and_tooltip": with_tip,
        "unparsed_keys": sorted(unparsed),
    }
    if args.ref:
        meta["source_tag"] = args.ref
    doc = {"_meta": meta}
    doc.update(dict(sorted(settings.items())))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    cov = (with_tip / len(settings) * 100) if settings else 0.0
    print(f"parsed {len(settings)} settings; label+tooltip {with_tip} ({cov:.1f}%); "
          f"unparsed {len(unparsed)} -> {args.out}")
    if len(settings) < args.min_count:
        print(f"ERROR: only {len(settings)} settings (< {args.min_count})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
