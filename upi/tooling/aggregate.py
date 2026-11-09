"""统计聚合底座（M1'，DESIGN §7.1 / AGENTS I10、G13、G15）。

数据流：runs/*/trace.jsonl + runreport.json + detection.json + patrol_runs.jsonl
       → SQLite 四表（runs / spans / stage_stats / outcomes_view）
       → 只读指标函数给报告与控制台用。

四条设计取舍（都来自普查到的实况，不是设想）：
1. **jsonl 是唯一真相源，SQLite 是缓存**：表可整表重建（--rebuild），
   从不反向写 jsonl；聚合失败不影响管线跑（DESIGN §7.1 只读原则）。
2. **增量幂等**：按 (run_id, seq) 去重、按 run 内容指纹决定要不要重灌。
   守护会常驻跑几百轮，重灌全表既慢又会让"某个数字来自哪一轮"失去唯一性。
3. **untraced 判定（G13）**：run 目录里有 runreport.json / state.json 等
   产物却没有 trace.jsonl ⇒ 该 run 标 untraced，从一切趋势与指标里剔除。
   没有这道闸，"跑过但没埋点"的轮次会混进统计，看起来全绿其实无据。
4. **成本口径要能算出来**：历史 span 的 llm/tool_calls 多为空（普查实测），
   所以 token 与墙钟同时从 runreport.spent 补录——否则成本轴是空的，
   G15"成本与质量同轴"就成了一句空话。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
DB = ROOT / "dashboard" / "stats.db"
# 特征库（成功率口径的唯一来源）。模块级变量而非函数内写死，
# 与 RUNS/DB 同构，便于离线测试整体隔离到临时目录。
KB_DB = ROOT / "knowledge" / "patterns.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  run_id TEXT PRIMARY KEY,
  kind TEXT,                     -- patrol | m4 | m1detect | m3_rehearsal | ...
  fixture TEXT,
  started_at TEXT, ended_at TEXT,
  wallclock_s REAL, tokens INTEGER,
  route TEXT, verdict TEXT, error TEXT, attempts INTEGER,
  traced INTEGER,                -- 1=有 trace；0=untraced（G13 剔除）
  findings INTEGER,              -- 检出条数（轮次 state.json 权威源）
  unevaluated_guards INTEGER,    -- 未评估守护数（G20，verdict.unevaluated）
  protocol_fingerprint TEXT,
  ingest_fp TEXT,                -- 内容指纹，决定要不要重灌
  time_source TEXT,              -- span_emitted_at | file_mtime | unknown
  env_fingerprint TEXT
);
CREATE TABLE IF NOT EXISTS spans(
  run_id TEXT, seq INTEGER, stage TEXT,
  issue_id TEXT, attempt_id TEXT,
  wallclock_ms REAL, exit_status INTEGER,
  llm_model TEXT, token_in INTEGER, token_out INTEGER,
  tool_kind TEXT, tool_name TEXT, tool_ms REAL,
  evidence_refs TEXT, environment_fingerprint TEXT,
  channel TEXT, emitted_at TEXT,
  PRIMARY KEY(run_id, seq)
);
CREATE TABLE IF NOT EXISTS stage_stats(
  stage TEXT, environment_fingerprint TEXT,
  n INTEGER, n_ok INTEGER,
  p50_ms REAL, p95_ms REAL, max_ms REAL, total_ms REAL,
  tokens INTEGER,
  PRIMARY KEY(stage, environment_fingerprint)
);
CREATE TABLE IF NOT EXISTS detections(
  run_id TEXT, fixture TEXT, detector TEXT, detector_version TEXT,
  metric TEXT, verdict TEXT, magnitude REAL, deviation_ratio REAL,
  clean_median REAL, fixture_median REAL, window_n INTEGER,
  conflict_rules TEXT,
  PRIMARY KEY(run_id, fixture)
);
CREATE TABLE IF NOT EXISTS guards(
  run_id TEXT, metric TEXT, before REAL, after REAL, delta REAL,
  regressed INTEGER, value_source TEXT, basis TEXT,
  PRIMARY KEY(run_id, metric)
);
-- 未评估守护（G20 盲区）单独一张表：字段名与 guards 不同是**有意的**——
-- 实测盲区行带的是 window_before/window_after/window_delta（被污染或不响应的
-- 窗口值），语义是"这个数我看得到但不可用于判定"，与 guards 的 before/after
-- （可用于判定）混塞一张表，迟早被人拿去做 Δ 计算，那就是 I1 违规。
CREATE TABLE IF NOT EXISTS unevaluated(
  run_id TEXT, metric TEXT, value_source TEXT, basis TEXT,
  window_before REAL, window_after REAL, window_delta REAL,
  cross_check_ok INTEGER,
  PRIMARY KEY(run_id, metric)
);
"""

# 缓存库结构演进：本库是 jsonl/产物的**派生缓存**（DESIGN §7.1），
# 一旦列语义变了，直接丢弃整库重灌，绝不做"猜旧值填新列"的迁移。
_MIGRATION = [("runs", "time_source", "TEXT"),
              ("runs", "env_fingerprint", "TEXT")]
# 判据只能是**旧形态独有的列**是否存在：新表/新列存在是正常状态，
# 拿它当重建信号会让每次 connect 都整库重建（自己造的坑，实测踩过）。
_OBSOLETE_COLS = [("runs", "blindspots"),      # 旧：采集层盲区，语义已换
                  ("guards", "assessed")]      # 旧：盲区曾混在 guards 表里


def _migrate(c: sqlite3.Connection) -> None:
    obsolete = False
    for table, old in _OBSOLETE_COLS:
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if old in cols:
            obsolete = True
            break
    if obsolete:
        for t in ("runs", "spans", "stage_stats", "detections", "guards", "unevaluated"):
            c.execute(f"DROP TABLE IF EXISTS {t}")
        c.executescript(SCHEMA)
        c.commit()
        return
    for table, col, typ in _MIGRATION:
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def _fp_of(paths: list[Path]) -> str:
    import hashlib
    h = hashlib.sha256()
    for p in paths:
        try:
            h.update(str(p.stat().st_mtime_ns).encode())
            h.update(str(p.stat().st_size).encode())
        except OSError:
            h.update(b"missing")
    return h.hexdigest()[:16]


def _load_jsonl(p: Path) -> list[dict]:
    out = []
    if not p.exists():
        return out
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue          # 半行由写侧原子性保证不会出现；真遇到就跳过
    return out


def connect(db: Path | str = DB) -> sqlite3.Connection:
    p = Path(db)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    c.executescript(SCHEMA)
    _migrate(c)
    return c


def connect_ro(db: Path | str = DB):
    """只读连接，且**永不改动磁盘上的真相源**。

    为什么单独一个入口：只读查询走 connect() 会顺带跑建表与迁移——
    守护常驻时每次渲染报告都在写数据库结构，而只读路径本该永远不可能
    改动真相源。接口上分开，比"自觉只查不写"可靠。

    库还不存在时（首次使用 / 测试隔离 / dashboard 被清理）：在**内存**里
    按同一 SCHEMA 建空表，于是 COUNT 返回 0、SUM 返回 NULL，指标函数自然
    降级为 None/0 并如实报"无数据"。
    ⚠️ 这里曾返回一个"任何查询都给零行"的伪对象：上层大量写
    `.fetchone()[0]`，而 SQL 的 COUNT(*) 本来必然返回一行——伪对象返回空行
    直接抛 IndexError，把"没数据"变成"取数崩溃"。空表结构才是正确降级。
    ⚠️ 调用方仍须 close()：Windows 上未关句柄会锁住 .db 文件
    （实测把 TemporaryDirectory 清理直接卡崩）。
    """
    p = Path(db)
    if not p.exists():
        c = sqlite3.connect(":memory:")
        c.executescript(SCHEMA)          # 只在内存建结构，不落盘
        return c
    return sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)


def _run_time(d: Path, spans: list[dict]) -> tuple[Optional[str], str]:
    """轮次时刻：优先用 span 里的 emitted_at（新数据，实测）；
    没有则退到 trace.jsonl 的 mtime（旧 75 条 span 无时间戳），
    并如实标 time_source——旧数据的时间是文件时刻，会被后续写入推移，
    不能当精确时长用。绝不假装它是实测的。"""
    ems = sorted(s["emitted_at"] for s in spans if s.get("emitted_at"))
    if ems:
        return ems[-1], "span_emitted_at"
    for name in ("trace.jsonl", "runreport.json", "detection.json"):
        f = d / name
        if f.exists():
            return time.strftime("%Y-%m-%dT%H:%M:%S",
                                 time.localtime(f.stat().st_mtime)), "file_mtime"
    return None, "unknown"


def ingest_run(c: sqlite3.Connection, d: Path) -> str:
    """灌一个 run 目录。返回 created / updated / skipped / untraced。"""
    run_id = d.name
    srcs = [p for p in (d / "trace.jsonl", d / "runreport.json",
                        d / "detection.json") if p.exists()]
    fp = _fp_of(srcs)
    row = c.execute("SELECT ingest_fp, traced FROM runs WHERE run_id=?",
                    (run_id,)).fetchone()
    if row and row[0] == fp:
        return "skipped"

    spans = _load_jsonl(d / "trace.jsonl")
    rep = {}
    if (d / "runreport.json").exists():
        try:
            rep = json.loads((d / "runreport.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rep = {}
    det = {}
    if (d / "detection.json").exists():
        try:
            det = json.loads((d / "detection.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            det = {}

    # G13：有产物无 trace = untraced。注意 patrol_* 之外的一轮型 run 也可能
    # 只有 trace 没有 report，那不算 untraced（有 span 就有据可查）。
    traced = 1 if spans else 0
    kind = ("patrol" if run_id.startswith("patrol") else
            "m4_drill" if run_id.startswith("m4_drill") else
            "m3_rehearsal" if run_id.startswith("m3_rehearsal") else
            "a4_altertest" if run_id.startswith("a4_altertest") else
            "m1detect" if run_id.startswith("m1detect") else "other")

    spent = rep.get("spent") or {}
    wall = spent.get("wallclock_s")
    if wall is None and spans:
        wall = round(sum((s.get("wallclock_ms") or 0) for s in spans) / 1000, 1)
    toks = spent.get("tokens")
    if not toks and spans:
        toks = sum(((s.get("llm") or {}).get("token_in") or 0) +
                   ((s.get("llm") or {}).get("token_out") or 0) for s in spans)

    # 判定与检出一律取轮次自己的 state.json（权威源）。
    # ⚠️ 这里曾经按时间戳去 patrol_runs.jsonl 配对取检出数，是错的路子：
    #   run_id 是轮次**开始**时刻，日志的 ts 是**结束**时刻，两者天然不等，
    #   实测 13 个 patrol 目录只配上 1 个，其余全被读成"0 检出"——
    #   静默把有结论的轮次写成静默轮，正是 B7 明令禁止的冒充。
    #   结论：跨文件配对只在有显式外键时做，否则回到本目录产物取数。
    st: dict = {}
    if (d / "state.json").exists():
        try:
            st = json.loads((d / "state.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            st = {}
    verdict_obj = st.get("verdict") or {}
    v = verdict_obj.get("status") or rep.get("verdict_status")
    # findings：检出条数（Issue + Potential，G18 分列在报告层做）
    n_find = len(st.get("findings") or [])
    # 未评估守护数（G20 盲区）。⚠️ 实测结构：盲区清单只写在**最后一条
    # kind=verify 的记录**里，verdict 顶层的 unevaluated 是空的（105554 实况
    # 顶层 0 条、末条 verify 4 条）。取错层 = 报告把"四项没测"写成"零盲区"，
    # 正是 G20 禁止的盲区隐匿。多轮 attempt 时也只认最后一次判定。
    final_verify = next((r for r in reversed(st.get("records") or [])
                         if isinstance(r, dict) and r.get("kind") == "verify"), {})
    uneval = (final_verify.get("unevaluated") or [])
    n_unev = len(uneval)

    ended, tsrc = _run_time(d, spans)
    env_fp = next((s.get("environment_fingerprint") for s in spans
                   if s.get("environment_fingerprint")), None)
    c.execute("""INSERT OR REPLACE INTO runs(
                   run_id, kind, fixture, started_at, ended_at,
                   wallclock_s, tokens, route, verdict, error, attempts,
                   traced, findings, unevaluated_guards, protocol_fingerprint,
                   ingest_fp, time_source, env_fingerprint)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (run_id, kind, rep.get("fixture"), ended, ended,
               wall, toks, rep.get("route"), v, str(rep.get("error"))[:200],
               rep.get("attempts"), traced, n_find, n_unev,
               (det.get("protocol_fingerprint") or {}).get("scene_guid"),
               fp, tsrc, env_fp))

    c.execute("DELETE FROM spans WHERE run_id=?", (run_id,))
    for s in spans:
        tcs = s.get("tool_calls") or []
        tk = tcs[0].get("kind") if tcs else None
        tn = tcs[0].get("name") if tcs else None
        tm = tcs[0].get("duration_ms") if tcs else None
        llm = s.get("llm") or {}
        c.execute("INSERT OR REPLACE INTO spans VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (s.get("run_id"), s.get("seq"), s.get("stage"),
                   s.get("issue_id"), s.get("attempt_id"),
                   s.get("wallclock_ms"), s.get("exit_status"),
                   llm.get("model"), llm.get("token_in"), llm.get("token_out"),
                   tk, tn, tm,
                   json.dumps(s.get("evidence_refs") or []),
                   s.get("environment_fingerprint"), s.get("channel"),
                   s.get("emitted_at")))
    c.execute("DELETE FROM detections WHERE run_id=?", (run_id,))
    for fx, r in (det.get("results") or {}).items():
        # results 的键是夹具 id（F-CPU-01 等），不是场景名——列名 fixture 指的
        # 就是它；clean/fixture 两列存的是 CLEAN 中位与缺陷中位（实测为浮点）
        conf = [cc.get("rule_id") for cc in (det.get("conflicts") or {}).get(fx, [])]
        c.execute("INSERT OR REPLACE INTO detections VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (run_id, fx, r.get("detector"), r.get("detector_version"),
                   r.get("metric"), r.get("verdict"), r.get("magnitude"),
                   r.get("deviation_ratio"), r.get("clean"), r.get("fixture"),
                   r.get("window_n"), json.dumps(conf)))
    # guards：verdict 的守护明细（报告热力图的数据源），只放**可用于判定**的。
    # G20 的未评估盲区单独进 unevaluated 表（字段语义不同，见建表处注释）。
    # 没有 verdict 的轮次（停在待批、或作废轮）两张表都无行，按"该轮无判定"呈现。
    c.execute("DELETE FROM guards WHERE run_id=?", (run_id,))
    for g in (verdict_obj.get("guards") or []):
        c.execute("INSERT OR REPLACE INTO guards VALUES(?,?,?,?,?,?,?,?)",
                  (run_id, g.get("metric"), g.get("before"), g.get("after"),
                   g.get("delta"), int(bool(g.get("regressed"))),
                   g.get("value_source", "window"), str(g.get("basis", ""))[:300]))
    c.execute("DELETE FROM unevaluated WHERE run_id=?", (run_id,))
    for u in uneval:
        c.execute("INSERT OR REPLACE INTO unevaluated VALUES(?,?,?,?,?,?,?,?)",
                  (run_id, u.get("metric"), u.get("value_source", ""),
                   str(u.get("basis", ""))[:300],
                   u.get("window_before"), u.get("window_after"),
                   u.get("window_delta"),
                   None if u.get("cross_check_ok") is None
                   else int(bool(u.get("cross_check_ok")))))
    c.commit()
    return "untraced" if not traced else ("created" if not row else "updated")


def rebuild_stage_stats(c: sqlite3.Connection) -> int:
    """按 (stage, 环境指纹) 聚合。环境不同的同阶段不合并——
    跨环境混算正是 G7 在采样层禁的事，统计层同理。"""
    c.execute("DELETE FROM stage_stats")
    rows = c.execute("""
      SELECT stage, environment_fingerprint,
             COUNT(*) n,
             SUM(CASE WHEN exit_status=0 THEN 1 ELSE 0 END) n_ok,
             GROUP_CONCAT(wallclock_ms) ms,
             SUM(COALESCE(token_in,0)+COALESCE(token_out,0)) tokens
      FROM spans GROUP BY stage, environment_fingerprint""").fetchall()
    import statistics as st
    for stage, env, n, n_ok, ms, tokens in rows:
        vals = sorted(float(x) for x in (ms or "").split(",") if x not in ("", "None"))
        p50 = st.median(vals) if vals else None
        p95 = vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))] if vals else None
        c.execute("INSERT OR REPLACE INTO stage_stats VALUES(?,?,?,?,?,?,?,?,?)",
                  (stage, env, n, n_ok, p50, p95, max(vals) if vals else None,
                   sum(vals), tokens or 0))
    c.commit()
    return len(rows)


def rebuild(db: Path | str = DB, force: bool = False) -> dict:
    c = connect(db)
    stats = {"created": 0, "updated": 0, "skipped": 0, "untraced": 0}
    for d in sorted(RUNS.iterdir()):
        if not d.is_dir() or not any(
                (d / f).exists() for f in ("trace.jsonl", "runreport.json",
                                           "detection.json")):
            continue
        if force:
            c.execute("DELETE FROM runs WHERE run_id=?", (d.name,))
        r = ingest_run(c, d)
        stats[r] = stats.get(r, 0) + 1
    n_stage = rebuild_stage_stats(c)
    out = {**stats, "stage_rows": n_stage,
           "runs_total": c.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
           "db": str(db)}
    c.close()
    return out


# ────────────────────────── 只读指标函数（给报告用） ──────────────────────────

def meta_metrics(db: Path | str = DB) -> dict:
    """DESIGN §7.2 三类指标里"当前能算的"。算不出的显式返回 None 并注明缺什么，
    禁止用估算填充（I1）；G15 要求成对展示的，这里成对返回。"""
    c = connect_ro(db)
    q = lambda sql, *a: c.execute(sql, a).fetchall()

    traced_runs = q("SELECT COUNT(*) FROM runs WHERE traced=1")[0][0]
    untraced = q("SELECT COUNT(*) FROM runs WHERE traced=0")[0][0]

    # 成本轴
    cost = {
        "runs_traced": traced_runs,
        "runs_untraced": untraced,
        "total_wallclock_s": q("SELECT SUM(wallclock_s) FROM runs WHERE traced=1")[0][0],
        "total_tokens": q("SELECT SUM(tokens) FROM runs WHERE traced=1")[0][0] or 0,
        "stage_p50_ms": {r[0]: r[1] for r in
                         q("SELECT stage, AVG(p50_ms) FROM stage_stats GROUP BY stage")},
        "stage_share": None,        # 需按 pipeline 版本聚合，样本不足 ⇒ 不算
    }

    # 质量轴：检出/FP 来自 detections；成功率来自 outcomes_view（patterns.db）
    det_by = dict(q("SELECT verdict, COUNT(*) FROM detections GROUP BY verdict"))
    selfcheck = _fp_selfcheck()
    quality = {
        "detector_verdicts": det_by,
        "recall_pairs": _recall_pairs(db),       # 检出率×FP 成对（G15）
        "fp_selfcheck": selfcheck,
        "success_rate": _success_rate(),         # 与 n_effective/CI 同屏
        "needs_human_share": None, "ece": None,  # 见 missing
        "realized_over_expected": None,
        "potential_ratification_rate": None,     # M3.5 未建 ⇒ 无追认对象
        "shadow_agreement": None,                # 影子模式未建
    }
    stability = {
        "rollback_count": q("SELECT COUNT(*) FROM spans WHERE stage='review' "
                            "AND evidence_refs LIKE '%rollback%'")[0][0],
        "cross_run_verdict_consistency": None,   # 同夹具同协议轮次不足
        "fingerprint_distinct": q(
            "SELECT COUNT(DISTINCT protocol_fingerprint) FROM runs")[0][0],
    }
    n_verdict = q("SELECT COUNT(*) FROM runs WHERE verdict IS NOT NULL")[0][0]
    # 以下三处说明原先写死具体数字（"2 行均 NULL"、"实测 1"），
    # 文档腐坏的典型形态就是数字不随实况变。一律改为现算。
    roe_tot = roe_null = 0
    if Path(KB_DB).exists():
        try:
            k = sqlite3.connect(f"file:{Path(KB_DB).as_posix()}?mode=ro", uri=True)
            roe_tot, roe_null = k.execute(
                "SELECT COUNT(*), SUM(CASE WHEN realized_over_expected IS NULL "
                "THEN 1 ELSE 0 END) FROM outcomes").fetchone()
            k.close()
        except sqlite3.OperationalError:
            pass
    # 可比组：同夹具 × 同协议 × 同环境且有判定，组内 ≥2 轮才谈得上"一致"
    n_cmp_groups = len(q("""SELECT 1 FROM runs WHERE verdict IS NOT NULL
                            AND protocol_fingerprint IS NOT NULL
                            GROUP BY fixture, protocol_fingerprint, env_fingerprint
                            HAVING COUNT(*) >= 2"""))
    missing = {
        "ece": f"需校准曲线：置信度分箱后与结局对齐，可配对观测仅 {n_verdict} 个"
               f"（每轮只有被采纳的 Top1 假设才有结局，其余假设的反事实不可观测）",
        "realized_over_expected": (
            f"outcomes 表 {roe_tot} 行里 {roe_null} 行的该列为 NULL"
            if roe_tot else "特征库尚无 outcome 记录"),
        "potential_ratification_rate": "M3.5 未建，无 potential 可追认",
        "shadow_agreement": "影子模式未启用",
        "cross_run_verdict_consistency": (
            f"同夹具×同协议×同环境且 ≥2 轮判定的可比组共 {n_cmp_groups} 组"
            + ("（协议指纹缺失的轮次不计，按 G7 不拿夹具同名冒充可比）"
               if n_cmp_groups == 0 else "")),
        "holdout_gap": "分组已可用（按 fixture_spec.holdout，见 checklist.split_of "
                       "三态建模）；缺的是每侧的置信度×结局分箱样本",
    }
    c.close()
    return {"cost": cost, "quality": quality, "stability": stability,
            "missing": missing,
            "n_note": "n 全部随值返回；n<30 的报告禁止画折线，只画点（§7.3）"}


def _fp_selfcheck() -> dict:
    """CLEAN 自比自：任何 detected 都是误报（D6 口径）。取最新 detection.json。"""
    ds = sorted(RUNS.glob("m1detect_*/detection.json"))
    if not ds:
        return {}
    d = json.loads(ds[-1].read_text(encoding="utf-8"))
    fp = d.get("fp_selfcheck") or {}
    return {"n_detectors": len(fp),
            "n_fp": sum(1 for v in fp.values() if v == "detected")}


def _recall_pairs(db: Path | str = DB) -> dict:
    """检出率与 FP 率成对（G15）：按 detector 版本分面。
    ⚠️ 早期（2026-11-07）detection.json 的 results 只有 verdict/why 两键、
    无 detector 字段（实测 2 行），把它们混进分母会算出"检出率 0"的假结论。
    正确做法是排除并显式报排除数——数据缺口要可见，不能靠合并掩盖（I1）。"""
    c = connect_ro(db)
    rows = c.execute("""
      SELECT detector, detector_version,
             COUNT(*) n_obs,
             SUM(CASE WHEN verdict='detected' THEN 1 ELSE 0 END) n_det,
             COUNT(DISTINCT run_id) n_runs
      FROM detections WHERE detector IS NOT NULL
      GROUP BY detector, detector_version ORDER BY detector""").fetchall()
    n_skipped = c.execute(
        "SELECT COUNT(*) FROM detections WHERE detector IS NULL").fetchone()[0]
    c.close()
    return {
        "pairs": [{"detector": det, "version": ver, "n_obs": n, "n_runs": nr,
                   "n_detected": n_det,
                   "detection_rate": round(n_det / n, 3) if n else None}
                  for det, ver, n, n_det, nr in rows],
        "excluded_no_detector_field": n_skipped,
        "note": "n_obs = 夹具×轮次观测数；CLEAN 自比自的误报另见 fp_selfcheck（D6 口径）"}


def _success_rate(kb: Path | str | None = None) -> Optional[dict]:
    kb = Path(KB_DB if kb is None else kb)
    if not kb.exists():
        return None
    k = sqlite3.connect(str(kb))
    try:
        rows = k.execute("SELECT state, COUNT(*) FROM outcomes GROUP BY state").fetchall()
    except sqlite3.OperationalError:
        k.close()
        return None
    n_eff = k.execute(
        "SELECT COUNT(DISTINCT fixture||'|'||quality_tier) FROM outcomes").fetchone()[0]
    k.close()
    c = dict(rows)
    res, fail = c.get("resolved", 0), c.get("failed", 0) + c.get("rolled_back", 0)
    den = res + fail
    from upi.core.knowledge import wilson_lower_bound as wlb
    return {"n_success": res, "n_failed": fail, "n_needs_human": c.get("needs_human", 0),
            "success_rate": round(res / den, 3) if den else None,
            "wilson_lower_bound": round(wlb(res, den), 3) if den else None,
            "n_effective_contexts": n_eff,
            "note": "n_effective 按 §8.2 独立上下文计数，同上下文重复运行不重复计入"}


def summary(db: Path | str = DB) -> dict:
    """给控制台一帧用的紧凑摘要。"""
    c = connect_ro(db)
    rows = c.execute("""SELECT run_id, kind, traced, wallclock_s, tokens,
                        route, verdict, findings, unevaluated_guards
                        FROM runs ORDER BY run_id DESC LIMIT 60""").fetchall()
    stage = c.execute("""SELECT stage, SUM(n), AVG(p50_ms), AVG(p95_ms), SUM(tokens)
                         FROM stage_stats GROUP BY stage""").fetchall()
    c.close()
    return {"recent_runs": rows, "stages": stage, "meta": meta_metrics(db)}


if __name__ == "__main__":
    import sys
    print(json.dumps(rebuild(force="--force" in sys.argv), ensure_ascii=False, indent=1))
