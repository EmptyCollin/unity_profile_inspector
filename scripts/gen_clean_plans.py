"""生成 11case oracle clean plans（整方法替换）。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "sim" / "project"
INF = ROOT / "test_project"
OUT = ROOT / "runs" / "oracle_clean_plans.json"
CG = "Assets/DevSim/Runtime/ChromaGame.cs"
BM = "Assets/DevSim/Runtime/BoardModel.cs"


def block(text: str, method: str) -> str:
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if f" {method}(" in ln or f" {method} (" in ln:
            if ln.strip().startswith(("private", "public", "internal", "protected")):
                start = i
                break
    if start is None:
        raise ValueError(f"method not found: {method}")
    depth = 0
    seen = False
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if "{" in lines[i]:
            seen = True
        if seen and depth == 0:
            return "\n".join(lines[start:i + 1])
    raise ValueError(f"block not closed: {method}")


def plan(unit: str, rel: str, method: str, marker: str = "") -> dict:
    clean = (CLEAN / rel).read_text(encoding="utf-8")
    inf = (INF / rel).read_text(encoding="utf-8")
    diffs = []
    if marker:
        diffs.append({"find": marker, "replace": ""})
    diffs.append({"find": block(inf, method), "replace": block(clean, method)})
    return {"playbook_id": f"clean-revert-{unit}", "metric": "",
            "invariant": {}, "diffs": diffs}


def main() -> int:
    plans = {
        "unit-01": plan("unit-01", CG, "UpdateBody",
                        "        private static readonly ProfilerMarker AnimMarker = new ProfilerMarker(\"ChromaGame.UpdateBody\");\n"),
        "unit-02": plan("unit-02", CG, "RefreshTiles",
                        "        private static readonly ProfilerMarker TileTextureMarker = new ProfilerMarker(\"ChromaGame.RefreshTiles\");\n"),
        "unit-03": plan("unit-03", CG, "Update",
                        "        private static readonly ProfilerMarker CanvasChurnMarker = new ProfilerMarker(\"ChromaGame.Update\");\n"),
        "unit-05": plan("unit-05", CG, "ResolvePendingWhenIdle",
                        "        private static readonly ProfilerMarker HintScanMarker = new ProfilerMarker(\"ChromaGame.ResolvePendingWhenIdle\");\n"),
        "unit-07": plan("unit-07", CG, "BuildBoard",
                        "        private static readonly ProfilerMarker MaterialChurnMarker = new ProfilerMarker(\"ChromaGame.BuildBoard\");\n"),
        "unit-08": plan("unit-08", CG, "OpenShop",
                        "        private static readonly ProfilerMarker ShopSyncMarker = new ProfilerMarker(\"ChromaGame.OpenShop\");\n        private int _shopPayloadChars;\n"),
        "unit-09": {"playbook_id": "clean-revert-unit-09", "metric": "",
                    "invariant": {},
                    "diffs": [{"find": block((INF / CG).read_text(encoding="utf-8"), "RefreshItemLabelsBody"),
                               "replace": block((CLEAN / CG).read_text(encoding="utf-8"), "RefreshItemLabelsBody")}]},
        "unit-10": plan("unit-10", CG, "ResolveAllSync",
                        "        private static readonly ProfilerMarker ClearFxMarker = new ProfilerMarker(\"ChromaGame.ResolveAllSync\");\n"),
        "unit-06": {"playbook_id": "clean-revert-unit-06", "metric": "",
                    "invariant": {},
                    "diffs": [{"find": (INF / BM).read_text(encoding="utf-8"),
                               "replace": (CLEAN / BM).read_text(encoding="utf-8")}]},
    }
    # c02/c03 fields
    field_find = ("        private bool busy, dragging;\n"
                  "        private int dragR = -1, dragC = -1, hoverR = -1, hoverC = -1;")
    field_repl = ("        private bool busy, dragging;\n"
                  "        private int lastHudRound = int.MinValue;\n"
                  "        private int lastHudTarget = int.MinValue;\n"
                  "        private int lastHudScore = int.MinValue;\n"
                  "        private int lastHudCoins = int.MinValue;\n"
                  "        private int[] lastItemCounts;\n"
                  "        private int dragR = -1, dragC = -1, hoverR = -1, hoverC = -1;")
    hud = {"playbook_id": "clean-revert-unit-04", "metric": "",
           "invariant": {}, "diffs": [
               {"find": field_find, "replace": field_repl},
               {"find": block((INF / CG).read_text(encoding="utf-8"), "UpdateHudBody"),
                "replace": block((CLEAN / CG).read_text(encoding="utf-8"), "UpdateHudBody")}]}
    plans["unit-04"] = hud
    OUT.write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT, len(plans))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
