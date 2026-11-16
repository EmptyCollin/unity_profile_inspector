"""按 window_id 从 per-action .data 定向解包。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import capability_run as C  # noqa: E402

CFG = ROOT / "runs" / "_probe" / "profile_window_cfg.txt"
OUT = ROOT / "runs" / "_probe" / "loaded_window.json"
PROBE = ROOT / "probe" / "load_window.cs"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--window-id", required=True)
    ap.add_argument("--project", default="test_project")
    a = ap.parse_args()
    run = ROOT / "runs" / a.run_id
    meta = json.loads((run / "profiles" / "profile_windows.json").read_text(encoding="utf-8"))
    hit = next((x for x in meta if x.get("window_id") == a.window_id), None)
    if hit is None:
        raise SystemExit(f"window not found: {a.window_id}")
    p = Path(hit["path"])
    if not p.is_file():
        raise SystemExit(f"profile missing: {p}")
    CFG.write_text(p.as_posix(), encoding="utf-8")
    r = C.unity("script.eval_file", "--file", str(PROBE),
                "--project-path", str(Path(a.project).resolve()), timeout=180)
    if not r.get("success"):
        raise SystemExit(json.dumps(r.get("errors"), ensure_ascii=False))
    doc = json.loads(OUT.read_text(encoding="utf-8"))
    dest = run / "windows" / f"{a.window_id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    allocs = [x for x in doc.get("samples", []) if "GC.Alloc" in x.get("name", "")]
    print(json.dumps({"window": a.window_id, "first": doc.get("first"),
                      "last": doc.get("last"),
                      "samples": len(doc.get("samples", [])),
                      "allocs": len(allocs),
                      "alloc_bytes": sum(int(x.get("bytes") or 0) for x in allocs),
                      "out": str(dest)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
