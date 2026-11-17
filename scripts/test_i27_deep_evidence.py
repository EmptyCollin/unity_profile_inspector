"""I27 deep_evidence：从窗口 .data 解包摘要给 candidate 排序。"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.candidates_v2 import CandidateV2  # noqa: E402
from upi.core.deep_evidence import (candidates_from_summary,  # noqa: E402
                                    load_window, merge_summaries,
                                    rank_candidates, summarize)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
run = TMP / "run"
(run / "windows").mkdir(parents=True)
doc = {"first": 0, "last": 20, "samples": [
    {"frame": 1, "name": "GC.Alloc",
     "path": "Main>ChromaGame.Update>ChromaGame.UpdateHud>GC.Alloc",
     "bytes": 1000, "time_ns": 100},
    {"frame": 2, "name": "GC.Alloc",
     "path": "Main>ChromaGame.Update>ChromaGame.UpdateHud>GC.Alloc",
     "bytes": 2000, "time_ns": 100},
    {"frame": 1, "name": "ChromaGame.UpdateHud", "path": "Main>ChromaGame.UpdateHud",
     "bytes": 0, "time_ns": 2_000_000},
    {"frame": 1, "name": "ChromaGame.Update", "path": "Main>ChromaGame.Update",
     "bytes": 0, "time_ns": 1_000_000},
]}
(run / "windows" / "cmd-10-open_shop.json").write_text(
    json.dumps(doc), encoding="utf-8")

loaded = load_window(run, "cmd-10-open_shop")
ok("load_window", loaded.get("first") == 0, loaded)
s = summarize(loaded, top_n=5)
ok("alloc 聚合", s["alloc_bytes"] == 3000, s)
ok("alloc symbol", s["allocs"][0]["symbol"] == "ChromaGame.UpdateHud", s["allocs"])
ok("hot 排序", s["hot"][0]["symbol"] == "ChromaGame.UpdateHud", s["hot"])
ok("hot ms", s["hot"][0]["ms"] == 2.0, s["hot"])

c1 = CandidateV2(id="c1", root_cause="hot_function:ChromaGame.Update",
                 mechanism="hot_path", kind="function",
                 window_id="cmd-10-open_shop")
c2 = CandidateV2(id="c2", root_cause="hot_function:ChromaGame.UpdateHud",
                 mechanism="hot_path", kind="function",
                 window_id="cmd-10-open_shop")
ranked = rank_candidates([c1, c2], s)
ok("deep 命中优先", [c.id for c in ranked] == ["c2", "c1"],
   [c.id for c in ranked])

empty = summarize({"samples": []})
ok("空窗口不炸", empty["allocs"] == [] and empty["hot"] == [], empty)

merged = merge_summaries([s, s])
ok("summary 合并 bytes", merged["alloc_bytes"] == 6000, merged)
ok("summary 合并 windows", merged["windows"] == 2, merged)

# symbol → 源码锚点 → 新 candidate
proj = TMP / "proj"
(proj / "Assets").mkdir(parents=True)
(proj / "Assets" / "Game.cs").write_text(
    "public class ChromaGame {\n"
    "  void UpdateHud() { new object(); }\n"
    "  void Update() { }\n"
    "}\n", encoding="utf-8")
dc = candidates_from_summary(s, proj, window_id="cmd-10-open_shop")
roots = {x["root_cause"] for x in dc}
ok("deep 生成 hot candidate",
   "hot_function:ChromaGame.UpdateHud" in roots, roots)
ok("deep candidate 有锚点",
   all(x.get("code_anchors") for x in dc), dc)
ok("deep candidate 有窗口",
   all(x.get("window_id") == "cmd-10-open_shop" for x in dc), dc)
s2 = dict(s)
s2["hot"] = list(s.get("hot") or []) + [
    {"symbol": "ChromaGame.Missing", "time_ns": 1, "ms": 0.001, "calls": 1}]
dc2 = candidates_from_summary(s2, proj, window_id="cmd-10-open_shop")
ok("deep 无源码的 symbol 被跳过",
   not any("Missing" in x["root_cause"] for x in dc2),
   [x["root_cause"] for x in dc2])

print(f"\n==== I27 deep_evidence: {len(PASS)} ok, {len(FAIL)} failed")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
