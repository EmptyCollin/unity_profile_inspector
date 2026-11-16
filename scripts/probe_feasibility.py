"""三项可行性实验：GC.Alloc 样本、call path、counter/function lag。"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import capability_run as C  # noqa: E402
import exam_multi as M  # noqa: E402

TREE = ROOT / "probe" / "exp_sample_tree.cs"
CFG = ROOT / "runs" / "_probe" / "exp_tree_cfg.json"
OUT = ROOT / "runs" / "_probe" / "exp_tree.json"


def unity(*args, timeout=120):
    return C.unity(*args, timeout=timeout)


def lag_from_pairs(counter, func, max_lag=10):
    best = (0, -2.0)
    for lag in range(-max_lag, max_lag + 1):
        pairs = [(counter[i + lag], func[i]) for i in func if i + lag in counter]
        if len(pairs) < 5:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        vx = sum((x - mx) ** 2 for x in xs)
        vy = sum((y - my) ** 2 for y in ys)
        if not vx or not vy:
            continue
        cov = sum((x - mx) * (y - my) for x, y in pairs)
        corr = cov / math.sqrt(vx * vy)
        if corr > best[1]:
            best = (lag, corr)
    return best


def path_to(edges, target):
    parent = {}
    for e in edges or []:
        p = C._console_errors if False else None
        par = str(e.get("parent") or "").split("::")[-1].split("(")[0].split(".")[-1]
        ch = str(e.get("child") or "").split("::")[-1].split("(")[0].split(".")[-1]
        if par and ch:
            parent.setdefault(ch, par)
    chain = [target]
    seen = set()
    while chain[-1] in parent and chain[-1] not in seen:
        seen.add(chain[-1])
        chain.append(parent[chain[-1]])
    return list(reversed(chain))


def main() -> int:
    p = Path("test_project").resolve()
    M.ensure_ready(p)
    code = ("UnityEngine.Profiling.Profiler.enabled=true;"
            "UnityEditorInternal.ProfilerDriver.enabled=true;"
            "UnityEditorInternal.ProfilerDriver.deepProfiling=true;"
            "UnityEngine.Profiling.Profiler.enableAllocationCallstacks=true;"
            "UnityEditorInternal.ProfilerDriver.ClearAllFrames();return 'on';")
    unity("script.eval", "--code", code, "--project-path", str(p))
    CFG.write_text(json.dumps({"frames": 5}), encoding="utf-8")
    spec = C.load_replay(ROOT / "sim" / "replay" / "c01.json")
    ex = C.ReplayExecutor(p)
    for cmd in spec.commands:
        ex._one(cmd)  # noqa: SLF001
    d = unity("script.eval_file", "--file", str(TREE),
              "--project-path", str(p))
    tree = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    alloc = [x for x in tree.get("samples", []) if "GC.Alloc" in x.get("name", "")]
    game_alloc = [x for x in alloc
                  if any(k in x.get("path", "") for k in
                         ("ChromaGame", "BoardModel", "DevSim"))]
    alloc_examples = game_alloc[:5]
    # call path：使用已有 edge 实验数据
    edges_doc = ROOT / "runs" / "edges_test" / "unified_probe.json"
    edges = json.loads(edges_doc.read_text(encoding="utf-8")).get("edges", []) if edges_doc.exists() else []
    path = path_to(edges, "FindMatches")
    # lag：使用已有 paired 实验数据
    lag = None
    ue = ROOT / "runs" / "unified_experiment.json"
    if ue.exists():
        d0 = json.loads(ue.read_text(encoding="utf-8"))["shallow"]
        counter = {x["frame_idx"]: x["counters"].get("CPU Main Thread Frame Time", 0) / 1e6
                   for x in d0.get("counters") or []}
        func = {x["frame_idx"]: x.get("total_ms", 0) for x in d0.get("functions") or []
                if x.get("symbol", "").endswith("ChromaGame.Update")}
        if counter and func:
            lag = lag_from_pairs(counter, func)
    out = {
        "alloc_available": bool(alloc),
        "alloc_samples": len(alloc),
        "game_alloc_samples": len(game_alloc),
        "alloc_examples": alloc_examples,
        "call_path_find_matches": path,
        "counter_function_lag": lag,
        "exp_probe_success": bool(tree),
        "exp_probe_result": d.get("success"),
    }
    (ROOT / "runs" / "probe_feasibility.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
