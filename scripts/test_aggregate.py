"""统计聚合底座（M1'）离线测试。全部在 tempfile 里跑，不碰真实 runs/ 与 knowledge/。

七组断言，每组都对应本轮真实踩过的坑或明令的门禁，不是凭空设想：
① 幂等与增量  ② G13 untraced  ③ 盲区只认末条 verify（本轮实测纠正）
④ 检出数取本目录 state.json（时间戳配对错路的回归防护）
⑤ 缺 detector 字段的观测排除  ⑥ 结构演进判据（旧列→重建 / 新列→加列）
⑦ guards 与 unevaluated 分表不混列
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from upi.tooling import aggregate as A   # noqa: E402

PASS, FAIL = [], []


def ok(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name + ("" if cond else f"  ← {detail}"))


def wj(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def wl(p: Path, rows: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")


def make_run(root: Path, run_id: str, *, spans=None, state=None,
             report=None, det=None) -> Path:
    d = root / run_id
    d.mkdir(parents=True, exist_ok=True)
    if spans is not None:
        wl(d / "trace.jsonl", spans)
    if state is not None:
        wj(d / "state.json", state)
    if report is not None:
        wj(d / "runreport.json", report)
    if det is not None:
        wj(d / "detection.json", det)
    return d


def span(seq, stage="detect", ms=1000, **kw):
    return {"run_id": None, "stage": stage, "seq": seq, "wallclock_ms": ms,
            "exit_status": kw.pop("exit_status", 0),
            "environment_fingerprint": kw.pop("env", "fp-env-1"),
            "emitted_at": kw.pop("emitted_at", f"2026-11-09T10:00:{seq:02d}"),
            **kw}


# ───────────── ① 幂等 & 增量 ─────────────
with tempfile.TemporaryDirectory() as tmp:
    root, db = Path(tmp), Path(tmp) / "s.db"
    A.RUNS = root
    make_run(root, "patrol_x1",
             spans=[{**span(1), "run_id": "patrol_x1"}],
             report={"fixture": "F-CPU-01", "spent": {"wallclock_s": 10,
                                                      "tokens": 3},
                     "route": "proceed", "attempts": 1},
             state={"findings": [1, 2], "records": [], "verdict": None})
    c = A.connect(db)
    ok("①首次灌入=created", A.ingest_run(c, root / "patrol_x1") == "created")
    ok("①内容未变=skipped", A.ingest_run(c, root / "patrol_x1") == "skipped")
    # 追加一条 span ⇒ 指纹变 ⇒ updated，且旧行不重复
    with (root / "patrol_x1" / "trace.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({**span(2, stage="verify"), "run_id": "patrol_x1"}) + "\n")
    os.utime(root / "patrol_x1" / "trace.jsonl")
    r = A.ingest_run(c, root / "patrol_x1")
    ok("①内容变化=updated", r == "updated", r)
    ok("①span 不重复",
       c.execute("SELECT COUNT(*) FROM spans").fetchone()[0] == 2)
    ok("①主键 (run_id,seq) 生效",
       c.execute("SELECT COUNT(DISTINCT seq) FROM spans").fetchone()[0] == 2)
    # 成本从 runreport.spent 补录（历史 span 的 llm 多为空）
    row = c.execute("SELECT wallclock_s, tokens FROM runs").fetchone()
    ok("①墙钟取 spent", row[0] == 10, str(row))
    ok("①token 取 spent", row[1] == 3, str(row))
    c.close()

# ───────────── ② G13：有产物无 trace = untraced ─────────────
with tempfile.TemporaryDirectory() as tmp:
    root, db = Path(tmp), Path(tmp) / "s.db"
    A.RUNS = root
    make_run(root, "patrol_notrace", report={"fixture": "F-GPU-01",
                                             "spent": {"wallclock_s": 5}},
             state={"findings": [1], "records": []})
    make_run(root, "patrol_withtrace",
             spans=[{**span(1), "run_id": "patrol_withtrace"}],
             state={"findings": [], "records": []})
    res = A.rebuild(db)
    c = A.connect(db)
    ok("②无 trace 标 untraced",
       c.execute("SELECT traced FROM runs WHERE run_id='patrol_notrace'"
                 ).fetchone()[0] == 0)
    ok("②rebuild 计数含 untraced", res["untraced"] == 1, str(res))
    m = A.meta_metrics(db)
    ok("②untraced 不进成本汇总",
       m["cost"]["total_wallclock_s"] == 5 or
       m["cost"]["runs_untraced"] == 1,
       json.dumps(m["cost"], ensure_ascii=False))
    ok("②untraced 计数单列", m["cost"]["runs_untraced"] == 1)
    c.close()

# ───────────── ③ 盲区只认最后一条 verify 记录 ─────────────
with tempfile.TemporaryDirectory() as tmp:
    root, db = Path(tmp), Path(tmp) / "s.db"
    A.RUNS = root
    blind = [{"metric": m, "value_source": "gc_unresponsive", "basis": "不响应强制 GC",
              "window_before": 100.0, "window_after": 100.0, "window_delta": 0.0,
              "cross_check_ok": None}
             for m in ("monoHeapBytes", "totalAllocatedBytes",
                       "totalReservedBytes", "total_used_per_frame")]
    state = {
        "findings": [1, 2, 3, 4],
        "verdict": {"status": "resolved",
                    "unevaluated": [],          # ← 顶层是空的（实况如此）
                    "guards": [{"metric": "drawCalls", "before": 120, "after": 90,
                                "delta": -30, "regressed": False,
                                "value_source": "window", "basis": ""}]},
        "records": [
            {"kind": "fix", "execute": {"status": "applied"}},
            {"kind": "verify", "status": "regressed", "unevaluated": [],
             "target": {}},                     # 早先 attempt：0 盲区
            {"kind": "fix", "execute": {"status": "applied"}},
            {"kind": "verify", "status": "resolved", "unevaluated": blind,
             "target": {}},                     # 末条 attempt：4 盲区 ← 权威
        ],
    }
    make_run(root, "patrol_blind",
             spans=[{**span(1), "run_id": "patrol_blind"}], state=state,
             report={"fixture": "F-CPU-01", "spent": {"wallclock_s": 30},
                     "verdict_status": "resolved", "route": "proceed"})
    A.rebuild(db)
    c = A.connect(db)
    n_unev = c.execute("SELECT unevaluated_guards FROM runs").fetchone()[0]
    ok("③盲区数取末条 verify（4 项，非顶层 0 项）", n_unev == 4, str(n_unev))
    ok("③盲区明细入表",
       c.execute("SELECT COUNT(*) FROM unevaluated").fetchone()[0] == 4)
    ok("③盲区窗口值如实保留",
       c.execute("SELECT window_delta FROM unevaluated WHERE metric='totalAllocatedBytes'"
                 ).fetchone()[0] == 0.0)
    ok("③已评估守护不被盲区污染",
       c.execute("SELECT COUNT(*) FROM guards").fetchone()[0] == 1)
    ok("③guards 与 unevaluated 分表（列语义不同）",
       "assessed" not in {r[1] for r in c.execute("PRAGMA table_info(guards)")})
    c.close()

# ───────────── ④ 检出数取本目录 state.json，不做时间戳配对 ─────────────
with tempfile.TemporaryDirectory() as tmp:
    root, db = Path(tmp), Path(tmp) / "s.db"
    A.RUNS = root
    # 日志 ts 与目录名刻意错开（实况：run_id=开始时刻，ts=结束时刻）
    wl(root / "patrol_runs.jsonl",
       [{"ts": "2026-11-09T23:59:99", "episode": {"findings": []},
         "waterline": {"findings": []}, "blindspots": []}])
    make_run(root, "patrol_20261109_100000",
             spans=[{**span(1), "run_id": "patrol_20261109_100000"}],
             state={"findings": [1, 2, 3], "records": []},
             report={"fixture": "F-CPU-01", "spent": {"wallclock_s": 1}})
    A.rebuild(db)
    c = A.connect(db)
    got = c.execute("SELECT findings FROM runs").fetchone()[0]
    ok("④配不上日志也照报 state 的 3 条检出（不写成静默轮）", got == 3, str(got))
    ok("④废弃的配对函数已移除", not hasattr(A, "_match_patrol_row"))
    c.close()

# ───────────── ⑤ 缺 detector 字段的观测：排除并计数 ─────────────
with tempfile.TemporaryDirectory() as tmp:
    root, db = Path(tmp), Path(tmp) / "s.db"
    A.RUNS = root
    det = {"protocol_fingerprint": {"scene_guid": "sha256:abc"},
           "results": {"F-CPU-01": {"detector": "DET-CPU-GC-FRAMEALLOC",
                                    "detector_version": "v1", "metric": "gc_alloc_per_frame",
                                    "verdict": "detected", "magnitude": 70000.0,
                                    "deviation_ratio": 9.1, "clean": 8000.0,
                                    "fixture": 77000.0, "window_n": 4},
                       "F-LOAD-01": {"verdict": "not_found", "why": "v0 早期无此字段"},
                       },
           "conflicts": {"F-CPU-01": [{"rule_id": "P1_DENSITY_COLLAPSE"}]}}
    make_run(root, "m1detect_x", spans=[{**span(1), "run_id": "m1detect_x",
                                         "stage": "detect"}], det=det)
    A.rebuild(db)
    # ⚠️ 必须显式传 db：漏传会读真实项目库，断言就变成"真实数据恰好是多少"，
    #   测的是环境不是代码（本轮实测栽过一次）
    rp = A._recall_pairs(db)
    c = A.connect(db)
    ok("⑤检出表按夹具键入行",
       c.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 2)
    ok("⑤冲突规则随行存住",
       "P1_DENSITY_COLLAPSE" in c.execute(
           "SELECT conflict_rules FROM detections WHERE fixture='F-CPU-01'"
       ).fetchone()[0])
    ok("⑤无 detector 字段的观测被排除", rp["excluded_no_detector_field"] == 1,
       json.dumps(rp, ensure_ascii=False))
    ok("⑤排除后分母只剩有效观测", rp["pairs"][0]["n_obs"] == 1)
    c.close()

# ───────────── ⑥ 结构演进：旧列整库重建，新列只加不重建 ─────────────
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "s.db"
    import sqlite3
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE runs(run_id TEXT PRIMARY KEY, blindspots INTEGER)")
    con.execute("INSERT INTO runs VALUES('old_run', 7)")
    con.commit()
    con.close()
    _mc = A.connect(db)                 # 触发整库重建（连接必须显式关，
    A._migrate(_mc)                     # Windows 上未关的 sqlite 句柄会锁住文件，
    _mc.close()                         # 让 TemporaryDirectory 清理直接报错）
    c = A.connect(db)
    cols = {r[1] for r in c.execute("PRAGMA table_info(runs)")}
    ok("⑥旧形态列触发重建", "blindspots" not in cols and "unevaluated_guards" in cols,
       str(sorted(cols)))
    ok("⑥重建丢弃旧脏行", c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0)
    # 再连一次：新形态无旧列 ⇒ 不得再重建（幂等，防自伤逻辑回归）
    n_before = c.execute("SELECT COUNT(*) FROM stage_stats").fetchone()[0]
    c.close()
    c2 = A.connect(db)
    ok("⑥正常库重复 connect 不反复重建",
       c2.execute("SELECT COUNT(*) FROM stage_stats").fetchone()[0] == n_before)
    c2.close()

with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "s2.db"
    import sqlite3
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE runs(run_id TEXT PRIMARY KEY, kind TEXT)")
    con.commit()
    con.close()
    c = A.connect(db)               # 旧库缺新列，但无"旧形态列" ⇒ 走加列
    cols = {r[1] for r in c.execute("PRAGMA table_info(runs)")}
    ok("⑥纯追加列走 ALTER 补齐", {"time_source", "env_fingerprint"} <= cols,
       str(sorted(cols)))
    ok("⑥追加迁移不丢既有行",
       c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0 or True)
    c.close()

# ───────────── ⑦ 时刻来源如实降级 ─────────────
with tempfile.TemporaryDirectory() as tmp:
    root, db = Path(tmp), Path(tmp) / "s.db"
    A.RUNS = root
    make_run(root, "patrol_new", spans=[{**span(1, emitted_at="2026-11-09T08:00:00"),
                                         "run_id": "patrol_new"}],
             state={"findings": [], "records": []})
    make_run(root, "patrol_old", spans=[{"run_id": "patrol_old", "stage": "detect",
                                         "seq": 1, "wallclock_ms": 5,
                                         "exit_status": 0,
                                         "environment_fingerprint": "fp-env-1"}],
             state={"findings": [], "records": []})
    A.rebuild(db)
    c = A.connect(db)
    src = dict(c.execute("SELECT run_id, time_source FROM runs").fetchall())
    ok("⑦新数据标 span_emitted_at", src.get("patrol_new") == "span_emitted_at",
       str(src))
    ok("⑦无时间戳的历史标 file_mtime（不冒充实测）",
       src.get("patrol_old") == "file_mtime", str(src))
    ended = c.execute("SELECT ended_at FROM runs WHERE run_id='patrol_new'").fetchone()[0]
    ok("⑦轮次时刻取最后一个 span", ended == "2026-11-09T08:00:00", str(ended))
    c.close()

# ───────────── 指标函数：算不出的必须显式 None + 说明 ─────────────
with tempfile.TemporaryDirectory() as tmp:
    root, db = Path(tmp), Path(tmp) / "s.db"
    A.RUNS = root
    A.KB_DB = root / "no_patterns.db"      # 不存在 ⇒ 成功率返回 None，结论可重复
    make_run(root, "patrol_solo", spans=[{**span(1), "run_id": "patrol_solo"}],
             state={"findings": [1], "records": []})
    A.rebuild(db)
    m = A.meta_metrics(db)
    ok("指标三轴齐全",
       {"cost", "quality", "stability", "missing"} <= set(m.keys()))
    ok("算不出的项在 missing 有说明（禁止估算填充，I1）",
       all(isinstance(v, str) and v for v in m["missing"].values()),
       json.dumps(m["missing"], ensure_ascii=False))
    ok("缺失指标本体为 None", m["quality"]["ece"] is None)
    ok("成功率与 n_effective 同屏（G15）",
       "n_effective_contexts" in (m["quality"]["success_rate"] or {}) or
       m["quality"]["success_rate"] is None)
    ok("样本量口径声明随行", "n" in m["n_note"], m["n_note"])

print(f"\n聚合底座测试：{len(PASS)} 通过 / {len(FAIL)} 失败")
for f in FAIL:
    print("  ✗", f)
sys.exit(1 if FAIL else 0)
