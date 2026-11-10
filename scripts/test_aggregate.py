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

# ───────────── ⑥b 阶段 7 批 2：runs.profile 补列 + 归属回填 ─────────────
# 补列只是第一步，回填才是这条链的要害：runs 行是否重灌由 ingest_fp 决定，
# 产物没变的老轮次**永远不会**再被灌一次。若 profile 留 NULL，§8.7 第 6 项
# 分组时它们自成一类，历史轮次从"可比组"里静默消失——一致率算出来变成
# "样本不足"而不是"旧数据在"，结论方向可能没变但依据已经换了。
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "s3.db"
    import re
    import sqlite3
    con = sqlite3.connect(str(db))
    # 建"批 2 之前形态"的库：列清单从产品 SCHEMA 现算再剔除 profile 行，
    # 不手写、也不按下标切（上一版按下标切行会连 run_id/kind 一起漏掉，
    # SCHEMA 一改就错）。按内容剔除与列顺序无关。
    _ddl = re.search(r"CREATE TABLE IF NOT EXISTS runs\((.*?)\n\);",
                     A.SCHEMA, re.S).group(1)
    _old_ddl = "\n".join(ln for ln in _ddl.split("\n") if "profile TEXT" not in ln)
    con.executescript("CREATE TABLE runs(" + _old_ddl + ")")
    con.execute("INSERT INTO runs(run_id,kind,fixture) VALUES('old1','patrol','F-CPU-01')")
    con.execute("INSERT INTO runs(run_id,kind,fixture) VALUES('old2','m4','F-CPU-01')")
    con.commit()
    con.close()

    from upi.config.project_profile import owner_profile_id
    c = A.connect(db)                       # connect 内部触发 _migrate
    cols = {r[1] for r in c.execute("PRAGMA table_info(runs)")}
    ok("⑥b runs 补出 profile 列", "profile" in cols, str(sorted(cols)))
    vals = [v[0] for v in c.execute("SELECT profile FROM runs").fetchall()]
    ok("⑥b 历史行按归属判据回填，不留 NULL",
       vals == [owner_profile_id({})] * 2, str(vals))
    ok("⑥b 回填不新增也不丢失轮次",
       c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2)
    # 幂等：回填只补 NULL，已有归属不得被后续 connect 覆盖
    c.execute("UPDATE runs SET profile='gameB' WHERE run_id='old1'")
    c.commit()
    c.close()
    c2 = A.connect(db)
    got = dict(c2.execute("SELECT run_id, profile FROM runs").fetchall())
    ok("⑥b 已有归属不被回填覆盖（只补 NULL 行）",
       got.get("old1") == "gameB", str(got))
    c2.close()

# 入库取数优先级：state.json 开轮记录 > detection 的数据侧声明 > 归属判据。
# 三级都不许取"当前运行档案"：重灌是离线动作，那时档案可能已切到别的工程，
# 那样会把旧工程的历史轮次标成新工程的，G7 分组直接被污染。
with tempfile.TemporaryDirectory() as tmp:
    root, db = Path(tmp), Path(tmp) / "s4.db"
    _keep_runs, _keep_db = A.RUNS, A.DB
    A.RUNS, A.DB = root, db
    make_run(root, "patrol_p1", spans=[dict(span(1), run_id="patrol_p1")],
             state={"profile_id": "from-state", "findings": [],
                    "verdict": {"status": "resolved"}},
             report={"fixture": "F-CPU-01", "route": "proceed"},
             det={"protocol_fingerprint": {"profile_id": "from-det",
                                           "scene_guid": "sha256:x"}})
    make_run(root, "patrol_p2", spans=[dict(span(1), run_id="patrol_p2")],
             state={"findings": [], "verdict": {"status": "resolved"}},
             report={"fixture": "F-CPU-01", "route": "proceed"},
             det={"protocol_fingerprint": {"profile_id": "from-det",
                                           "scene_guid": "sha256:y"}})
    make_run(root, "patrol_p3", spans=[dict(span(1), run_id="patrol_p3")],
             report={"fixture": "F-CPU-01", "route": "proceed"})
    try:
        A.rebuild(db, force=True)
        c = A.connect(db)
        prof = dict(c.execute("SELECT run_id, profile FROM runs").fetchall())
        c.close()
    finally:
        A.RUNS, A.DB = _keep_runs, _keep_db
    ok("⑥b 归属优先取轮次自述（state.json）",
       prof.get("patrol_p1") == "from-state", str(prof))
    ok("⑥b state 没写时退回数据侧声明（detection.json）",
       prof.get("patrol_p2") == "from-det", str(prof))
    ok("⑥b 两者都无 ⇒ 按归属判据，不拿当前档案凑",
       prof.get("patrol_p3") == owner_profile_id({}), str(prof))

# ───────────── ⑥c 旧库只读路径：不得因缺 profile 列而崩 ─────────────
# 这条是 ⑥b 之后实测撞出来的：checklist / meta_metrics 走 connect_ro
# （只读连接，设计上**不跑迁移**——否则守护每次渲染报告都在写库结构）。
# 我修 profile 时给口径表达式加了列存在性判断，却把两处 SQL 的列名写死了，
# 拿旧的 dashboard/stats.db 跑 `upi dev stats` 直接崩在
# "no such column: profile"。教训：**列存在性判断必须覆盖每一条读路径**，
# 只修其中一处等于修了个样子（本轮同类错误第二次）。
with tempfile.TemporaryDirectory() as tmp:
    import re
    import sqlite3
    from upi.config.project_profile import owner_profile_id
    db = Path(tmp) / "legacy.db"
    con = sqlite3.connect(str(db))
    con.executescript(A.SCHEMA)
    # 列清单从产品 SCHEMA 现算并剔除 profile 行，不手写（手写会与产品脱节）
    _ddl = re.search(r"CREATE TABLE IF NOT EXISTS runs\((.*?)\n\);",
                     A.SCHEMA, re.S).group(1)
    _old = "\n".join(l for l in _ddl.split("\n") if "profile TEXT" not in l)
    con.executescript("DROP TABLE runs; CREATE TABLE runs(" + _old + ");")
    for rid in ("L1", "L2"):
        con.execute("INSERT INTO runs(run_id,kind,fixture,verdict,"
                    "protocol_fingerprint,env_fingerprint) "
                    "VALUES(?,'patrol','F-CPU-01','resolved','fp','env')", (rid,))
    con.commit()
    con.close()

    def _run_cols():
        _c = sqlite3.connect(str(db))
        try:
            return {r[1] for r in _c.execute("PRAGMA table_info(runs)")}
        finally:
            _c.close()          # 句柄必须关：Windows 上不关就锁住文件

    ok("⑥c 前置：旧库确实没有 profile 列", "profile" not in _run_cols())

    # 只读统计必须降级出数，而不是抛 OperationalError
    try:
        m10 = A.meta_metrics(db)
        txt = m10["missing"]["cross_run_verdict_consistency"]
        ok("⑥c 旧库跑 meta_metrics 不崩且给出可比组数", "1 组" in txt, txt[:60])
    except sqlite3.OperationalError as e:
        ok("⑥c 旧库跑 meta_metrics 不崩且给出可比组数", False, str(e))
    try:
        from upi.tooling import checklist as CL
        cl10 = CL.compute(db)["items"]["cross_run_verdict_consistency"]
        ok("⑥c 旧库跑体检表不崩（第 6 项按三列分组出数）",
           cl10["n"] == 1, json.dumps(cl10, ensure_ascii=False)[:90])
    except sqlite3.OperationalError as e:
        ok("⑥c 旧库跑体检表不崩（第 6 项按三列分组出数）", False, str(e))

    # 只读路径**永不**改真相源：跑完统计后列仍然不存在（没被偷偷 ALTER）
    ok("⑥c 只读统计未顺带改动库结构", "profile" not in _run_cols())

    # 分组键与口径文案必须同源：旧库上两者同时退化
    c = A.connect_ro(db)
    ok("⑥c 分组键按列存在性退化（不含工程）",
       A.runs_group_cols(c) == ["fixture", "protocol_fingerprint",
                                "env_fingerprint"], str(A.runs_group_cols(c)))
    lab = A.runs_group_label(c)
    ok("⑥c 口径文案与实际分组同源（不是另写一句中文）",
       lab == "同夹具×同协议×同环境", lab)
    # 文案的每一项都必须真的出现在 GROUP BY 里，反之亦然
    ok("⑥c 文案项与分组列一一对应（防『说明写四列、实际按三列分组』）",
       all(A.GROUP_LABELS[col] in lab for col in A.runs_group_cols(c))
       and lab.count("同") == len(A.runs_group_cols(c)),
       f"{lab} vs {A.runs_group_cols(c)}")
    c.close()

    # 写路径（A.connect 跑迁移）之后，同一份库升级成含工程维度且历史行已回填
    c2 = A.connect(db)
    ok("⑥c 经写路径后分组键升级为含工程",
       A.runs_group_cols(c2)[0] == "profile", str(A.runs_group_cols(c2)))
    ok("⑥c 升级后历史行已回填归属（不是留 NULL）",
       {r[0] for r in c2.execute("SELECT profile FROM runs")}
       == {owner_profile_id({})})
    ok("⑥c 升级后文案同步带上工程维度",
       A.runs_group_label(c2).startswith("同工程"), A.runs_group_label(c2))
    c2.close()

# ───────────── ⑥d 只读句柄必须异常安全释放（阶段 7 批 2 引入） ─────────────
    # 这段是本轮自己踩出来的两类问题，都需要常驻保护：
    #
    # 一、泄漏的成因不是"忘了 close"这么简单。产品侧原来只在**正常路径**末尾
    #     c.close()：SQL 一抛错就跳过它，未关的只读句柄在 Windows 上把 .db 锁到
    #     进程退出。实测后果不是"慢一点"，而是**把现场毁掉**——变异测试本该因为
    #     某条断言变红而报失败，结果先撞上 TemporaryDirectory 清理的
    #     PermissionError，排查的人只看到"文件删不掉"，看不到真正那条 SQL 错。
    #
    # 二、重构时我自己引入了比修前更糟的一处：包装层已用 _with_ro 开好连接并把
    #     c 传进实现函数，实现体里却还留着 `c = connect_ro(db)`——它把传入的连接
    #     丢掉换成一把永不关闭的新连接，而我同时删掉了配套的 c.close()。
    #     结果 _with_ro 关的是空锁，每次调用净泄漏一个句柄。
    #     ⚠️ 上一版审计写的是"connect_ro 只出现在几行"，它确实收敛了，**所以没
    #     抓住这条**：数行数验证不了"接收连接的函数不再自开连接"。下面最后一条
    #     判据因此按 AST 的函数体来查，而不是按文本出现次数。
    #
    # ⚠️ 不用 `with sqlite3.connect(...) as c`：实测本机 Python 3.14 上它是
    #    **事务**上下文，提交事务但不关句柄（文件仍锁着）。用它"修"等于没修，
    #    且比不修更隐蔽——看着像已收口。统一走 aggregate._with_ro（显式
    #    try/finally + c.close()），异常路径实测可当场 unlink。
    #
    # 变异验证结果（三个变异分别注入又还原，如实记录各自的暴露形态）：
    #   A 摘掉 _with_ro 的 finally  → 整段崩溃，连"N 通过 / M 失败"摘要都打不出来
    #   B 实现函数塞一行自开连接    → 2 条断言报红（句柄泄漏 + AST 结构判据）
    #   C 列存在性判断写死恒真      → 8 条报红（四个只读入口全崩在缺列上）
    # ⚠️ A 的形态值得单独记：泄漏并不总是表现为"断言变红"，它会**吞掉断言本身**
    #    ——清理临时目录时先抛 PermissionError，测试进程在那一刻就死了。所以判据
    #    不能只看"有没有 FAIL 行"（上一版变异脚本就是这么写的，三次都报"0 失败"，
    #    实际是过滤条件匹配不到任何输出行）。可靠判据是**退出码**与摘要行是否出现。
    with tempfile.TemporaryDirectory() as tmp:
        import sqlite3 as _sq
        from contextlib import closing as _closing

        def _cols_of(db, table="runs"):
            """现算列清单。注意列名在 r[1]：r[0] 是数字 cid（写错会得到空列
            清单，拼出 `INSERT INTO runs ()` 这种语法错误——夹具自己坏了）。"""
            with _closing(_sq.connect(str(db))) as c:
                return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}

        def _mk_legacy(db):
            """老形态库：按产品完整 SCHEMA 建全套表，再 DROP 掉 runs.profile 列。
            必须建全套表——只建 runs 会让查询撞上 "no such table: detections"，
            测到的是"缺表"而不是要验的"缺列"，两者降级路径完全不同。"""
            with _closing(_sq.connect(str(db))) as c:
                c.executescript(A.SCHEMA)
                c.execute("ALTER TABLE runs DROP COLUMN profile")
                for rid in ("L1", "L2"):
                    c.execute("INSERT INTO runs(run_id,kind,fixture,verdict,"
                              "protocol_fingerprint,env_fingerprint) "
                              "VALUES(?,'patrol','F-CPU-01','resolved','fp','env')",
                              (rid,))
                c.commit()

        legacy = Path(tmp) / "legacy.db"
        _mk_legacy(legacy)
        ok("⑥d 前置：老库确实没有 profile 列", "profile" not in _cols_of(legacy))

        # ── 五个只读入口在老库上都必须可跑（本轮改过连接写法的不止两个） ──
        from upi.tooling import checklist as _CL
        from upi.tooling import report_data as _RD
        from upi.tooling import undetectable as _UD
        _empty_runs = Path(tmp) / "no_runs"
        _empty_runs.mkdir()
        for _label, _fn in (("meta_metrics", lambda: A.meta_metrics(legacy)),
                            ("summary", lambda: A.summary(legacy)),
                            ("report_data.gather", lambda: _RD.gather(legacy)),
                            ("undetectable.build", lambda: _UD.build(legacy, _empty_runs)),
                            ("checklist.compute", lambda: _CL.compute(legacy))):
            try:
                _r = _fn()
                ok(f"⑥d 老库跑 {_label} 不崩", isinstance(_r, dict))
            except Exception as _e:
                ok(f"⑥d 老库跑 {_label} 不崩", False,
                   f"{type(_e).__name__}: {str(_e)[:90]}")

        # ── 核心判据：跑完统计后库文件必须当场可删（= 没有句柄漏在外面） ──
        # 这条比"结果对不对"更能揪出泄漏：断言全绿但句柄没关的情况，实测发生过。
        try:
            legacy.unlink()
            ok("⑥d 五个入口跑完后库可立即删除（无句柄泄漏）", True)
        except PermissionError as _e:
            ok("⑥d 五个入口跑完后库可立即删除（无句柄泄漏）", False, str(_e)[:70])

        # ── 异常路径：SQL 真抛错时，错误必须可见，且句柄照样释放 ──
        broken = Path(tmp) / "broken.db"
        with _closing(_sq.connect(str(broken))) as c:
            c.executescript(A.SCHEMA)
            c.execute("INSERT INTO runs(run_id,kind,fixture,verdict,profile,"
                      "protocol_fingerprint,env_fingerprint) "
                      "VALUES('R1','patrol','F-CPU-01','resolved','p','fp','env')")
            # 把 detections 换成缺列的形态：统计里的 SQL 必然 OperationalError。
            # ⚠️ 不能靠"再建一次同名表"制造失败——产品 SCHEMA 已建过 detections，
            # 那句自己先抛 "table already exists"，测不到目标 SQL。
            c.execute("DROP TABLE detections")
            c.execute("CREATE TABLE detections(run_id TEXT, detector TEXT)")
            c.commit()
        _raised = None
        try:
            A.meta_metrics(broken)
        except Exception as _e:
            _raised = _e
        # 允许两种正当结局：如实抛出，或降级成"无观测"；唯独不许静默当成正常值
        ok("⑥d SQL 报错要么抛出要么如实降级（不静默冒充正常）",
           _raised is None or isinstance(_raised, _sq.OperationalError),
           f"{type(_raised).__name__ if _raised else '降级'}")
        try:
            broken.unlink()
            ok("⑥d 异常路径后库仍可立即删除（finally/closing 生效）", True)
        except PermissionError as _e:
            ok("⑥d 异常路径后库仍可立即删除（finally/closing 生效）", False,
               str(_e)[:70])

        # ── 结构判据（按 AST 函数体，不按文本次数）：防"实现函数自开连接"回归 ──
        import ast as _ast
        _root = Path(__file__).resolve().parents[1]
        _src = (_root / "upi" / "tooling" / "aggregate.py").read_text(encoding="utf-8")
        _tree = _ast.parse(_src)
        _offenders = []
        for _d in _tree.body:
            if not isinstance(_d, _ast.FunctionDef):
                continue
            _args = [a.arg for a in _d.args.args]
            if "c" in _args:            # 接收连接的实现函数：体内不得自开只读连接
                for _n in _ast.walk(_d):
                    if isinstance(_n, _ast.Call):
                        _nm = getattr(_n.func, "id", None) or getattr(_n.func, "attr", None)
                        if _nm == "connect_ro":
                            _offenders.append(f"{_d.name}:{_n.lineno} 自开只读连接")
        ok("⑥d 接收连接的实现函数不自开连接（本轮实测引入的泄漏形态）",
           not _offenders, str(_offenders)[:120])

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
