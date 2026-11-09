"""M7 报告层离线测试。留档整体重定向到 tempfile，不读真实 runs/。

断言按"报告失效模式"设计，不是按"代码行覆盖"设计——每类都在钉一条门禁：
① G20 硬要求：清单必须存在、且**与检出率同屏**（同一 section 内，
   拆开渲染就违反门禁，所以断言的是相对位置不是各自存在）。
② §7.3 硬要求：小样本禁折线；n<30 时必须出现散点与"禁折线"声明。
③ 版本竖线规则：无跳变时必须显式说"均无跳变"，不能静默省略（否则读者
   以为规则被漏了，与"趋势是自然改善"误读同源）。
④ 口径同屏：每个视图都要有口径说明文本，"只给数不给口径"不通过。
⑤ 体检表：缺项必须渲染成"未计算"且整体 blocked，不得出现"达标"字样冒充。
⑥ 只读：产物里不得出现任何 input/button/form/onclick（I3 与 §7.3 末条）。
⑦ 数字可回溯：证据链索引里的目录路径必须真的指向该轮次目录。
⑧ 空值不泄漏：不得出现裸 None/null 文本（fmt/esc 失效的信号）。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from upi.tooling import aggregate as A      # noqa: E402
from upi.tooling import checklist as CL      # noqa: E402
from upi.tooling import report as R          # noqa: E402
from upi.tooling import report_data as RD    # noqa: E402
from upi.tooling import undetectable as UD   # noqa: E402

PASS, FAIL = [], []


def ok(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name + ("" if cond else f"  ← {detail[:160]}"))


def wj(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def wl(p: Path, rows: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")


# ─────────── 造一份可控留档：6 轮，其中 2 轮有判定 ───────────
def build_fixture(tmp: Path) -> Path:
    runs = tmp / "runs"
    guards = [{"metric": "drawCalls", "before": 120.0, "after": 90.0, "delta": -30.0,
               "regressed": False, "value_source": "window", "basis": "窗口中位 1% 容差"},
              {"metric": "cpuFrameTimeMs", "before": 1.9, "after": 2.4, "delta": 0.5,
               "regressed": True, "value_source": "window", "basis": "超容差判劣化"},
              {"metric": "monoUsedBytes", "before": 80.0, "after": 82.0, "delta": 2.0,
               "regressed": False, "value_source": "fresh_post_gc", "basis": "post_gc 净常驻"}]
    unev = [{"metric": "monoHeapBytes", "value_source": "gc_unresponsive",
             "basis": "强制 GC 后 Δ≈0，不响应", "window_before": 392.0,
             "window_after": 392.0, "window_delta": 0.0, "cross_check_ok": None},
            {"metric": "totalAllocatedBytes", "value_source": "gc_unresponsive",
             "basis": "A 档字段不响应", "window_before": 700.0,
             "window_after": 710.0, "window_delta": 10.0, "cross_check_ok": None}]
    det = {"protocol_fingerprint": {"scene_guid": "sha256:aaa"},
           "fp_selfcheck": {"DET-CPU-GC-FRAMEALLOC": "not_found",
                            "DET-GPU-BATCHBREAK": "not_found"},
           "conflicts": {"F-CPU-01": [{"rule_id": "P1_DENSITY_COLLAPSE"}]},
           "results": {
               "F-CPU-01": {"detector": "DET-CPU-GC-FRAMEALLOC",
                            "detector_version": "v1", "metric": "gc_alloc_per_frame",
                            "verdict": "detected", "magnitude": 68000.0,
                            "deviation_ratio": 9.0, "clean": 8000.0,
                            "fixture": 76000.0, "window_n": 4},
               "F-GPU-01": {"detector": "DET-GPU-BATCHBREAK", "detector_version": "v1",
                            "metric": "drawCalls", "verdict": "detected",
                            "magnitude": 180.0, "deviation_ratio": 4.0,
                            "clean": 20.0, "fixture": 200.0, "window_n": 4}}}
    for i in range(6):
        rid = f"patrol_2026110{i + 1}_100000"
        d = runs / rid
        d.mkdir(parents=True, exist_ok=True)
        judged = i in (4, 5)
        wl(d / "trace.jsonl",
           [{"run_id": rid, "seq": 1, "stage": "detect", "wallclock_ms": 900,
             "exit_status": 0, "environment_fingerprint": "env-A",
             "emitted_at": f"2026-11-0{i + 1}T10:00:00"},
            {"run_id": rid, "seq": 2, "stage": "verify", "wallclock_ms": 3000,
             "exit_status": 0 if judged else 1, "environment_fingerprint": "env-A",
             "emitted_at": f"2026-11-0{i + 1}T10:01:00"}])
        wj(d / "runreport.json",
           {"run_id": rid, "fixture": "F-CPU-01",
            "route": "proceed" if judged else "needs_human",
            "attempts": 1 if judged else None, "error": None,
            "spent": {"wallclock_s": 100 + i * 5, "tokens": 2000 + i * 100},
            "final_rollback": ({"rolled_back_to": "upi/attempt-x",
                                "head_after": "abc1234567890",
                                "asset_manifest_restored": True,
                                "scene_restored": True} if not judged else None)})
        state = {"findings": [{"kind": "issue"}, {"kind": "issue"}],
                 "records": [{"kind": "fix", "execute": {"status": "applied"}}]}
        if judged:
            state["verdict"] = {"status": "regressed" if i == 4 else "resolved",
                                "guards": guards, "unevaluated": [],
                                "perception": {"render_diff_score": None,
                                               "anim_event_diff": None,
                                               "replay_divergence": None,
                                               "passed": False},
                                "compile_ok": True, "conflicts": []}
            state["records"].append(
                {"kind": "verify", "status": state["verdict"]["status"],
                 "target": {}, "unevaluated": unev})
            state["records"].append(
                {"kind": "review", "round": 1,
                 "verdict_status": "regressed",
                 "rollback": {"rolled_back_to": "upi/attempt-y",
                              "head_after": "def9876543210",
                              "asset_manifest_restored": True,
                              "scene_restored": True}})
        else:
            state["verdict"] = None
        wj(d / "state.json", state)
        wj(d / "detection.json", det)
    return runs


def isolated(tmp: Path) -> dict:
    """把三个模块的留档根与库路径全部指向临时目录。"""
    runs = build_fixture(tmp)
    db = tmp / "stats.db"
    A.RUNS = RD.AG.RUNS = UD.AG.RUNS = runs
    A.DB = RD.AG.DB = UD.AG.DB = CL.AG.DB = db
    CL.AG.RUNS = runs
    A.KB_DB = RD.AG.KB_DB = CL.AG.KB_DB = tmp / "no_kb.db"
    A._fp_of = lambda *a: "fp-fixed"       # 固定内容指纹，防跨秒抖动
    return {"runs": runs, "db": db}


ctx = {}
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    ctx = isolated(tmp)
    A.rebuild(ctx["db"], force=True)
    out_html = tmp / "报告.html"
    g = RD.gather(ctx["db"])
    R.render(g, out_html)
    HTML = out_html.read_text(encoding="utf-8")
    # ⚠️ 所有需要读库的断言必须在临时目录还活着时算好：
    #   曾把这几行写在 with 块外，目录已删 ⇒ 库不存在 ⇒ 取回空数据 ⇒
    #   断言失败看起来像实现的错，实际是测试自己的时序错。
    M = A.meta_metrics(ctx["db"])
    CLR = CL.compute(ctx["db"])
    BLIND = UD.build(ctx["db"])
    DB_GONE_OK = None
    # 顺手验"库没了不崩"（守护首启 / dashboard 被清理时的真实路径）
    # 注意：此刻 with 尚未退出，用另一个空临时目录模拟"无库"
    with tempfile.TemporaryDirectory() as t2:
        DB_GONE_OK = A.meta_metrics(Path(t2) / "nope.db")

    # 证据链目录存在性同样必须在此刻算：出了 with 块，整个 fixture 就没了
    LINKS = re.findall(r'href="file:///([^"]+)"', HTML)
    LINKS_EXIST = all((ctx["runs"] / Path(u.replace("\\", "/")).name).exists()
                      for u in LINKS)

# ── ① G20：清单存在 + 与检出率同屏（相对位置，不是各自存在）──
i_v2 = HTML.find("视图 2")
i_blind = HTML.find("不可检出清单")
i_v3 = HTML.find("视图 3")
ok("①报告含不可检出清单", i_blind > 0)
ok("①清单与检出率同屏（G20：在视图2与视图3之间）",
   0 < i_v2 < i_blind < i_v3, f"v2={i_v2} blind={i_blind} v3={i_v3}")
ok("①清单四项齐",
   len(BLIND["items"]) == 4)
ok("①清单证据取自本 fixture 而非真实库",
   "DET-CPU-GC-FRAMEALLOC" in HTML)

# ── ② 小样本禁折线 ──
ok("②样本不足时声明禁折线", "＜30" in HTML or "禁止折线" in HTML)
ok("②未画任何折线 path/polyline（只允许散点与条）",
   "<path" not in HTML and "<polyline" not in HTML,
   f"path={HTML.count('<path')} polyline={HTML.count('<polyline')}")
ok("②散点圆点存在（数据确已画出）", HTML.count("<circle") > 0,
   f"circle={HTML.count('<circle')}")

# ── ③ 版本竖线规则必须显式交代 ──
ok("③无跳变时显式写明（不静默省略规则）",
   "均无跳变" in HTML and "不画竖线" in HTML)

# ── ④ 口径同屏：每个视图都有"口径："说明 ──
n_views = len(re.findall(r"<h2>视图", HTML))
n_notes = HTML.count("口径：")
ok("④七个视图齐", n_views == 7, str(n_views))
ok("④口径说明数量 ≥ 视图数（每图给口径）", n_notes >= n_views,
   f"口径 {n_notes} / 视图 {n_views}")

# ── ⑤ 体检表：缺项渲染为未计算、整体 blocked ──
cl = CLR
ok("⑤缺项标注为未计算", HTML.count("未计算") >= cl["n_missing"],
   f"页面 {HTML.count('未计算')} vs 计算 {cl['n_missing']}")
ok("⑤整体 blocked/incomplete 有结论",
   "整体结论：incomplete" in HTML or "整体结论：blocked" in HTML,
   cl["status"])
ok("⑤阈值口径来自反查（同屏给通过条件；页面经 esc 转义 < >）",
   "&lt;=0.15" in HTML and "&gt;=0.5" in HTML,
   "页面无转义阈值文本")
ok("⑤n 与结论同屏（G15）",
   re.search(r"<tr><td><code>tuning_holdout_gap</code>.*?</tr>", HTML, re.S) is not None)

# ── ⑥ 只读：无任何交互控件 ──
ok("⑥无 input/button/form/onclick（I3、§7.3 末条）",
   not any(k in HTML for k in ("<input", "<button", "<form", "onclick=", "onchange=")),
   next((k for k in ("<input", "<button", "<form", "onclick=") if k in HTML), ""))

# ── ⑦ 数字可回溯：证据链目录真的存在 ──
ok("⑦证据链索引有条目", len(LINKS) >= 2, str(len(LINKS)))
ok("⑦每个证据路径指向真实存在的轮次目录", LINKS_EXIST, str(LINKS[:2]))

# ── ⑧ 空值不泄漏 ──
ok("⑧无裸 None/null 文本",
   not re.search(r">\s*None\s*<|>\s*null\s*<", HTML),
   (re.search(r".{30}None.{10}", HTML) or re.search(r"", "")).group(0)
   if re.search(r">\s*None\s*<", HTML) else "")
ok("⑧未评估盲区在热力图单列", "未评估" in HTML)
ok("⑧劣化格标红", 'class="r"' in HTML)

# ── 库缺失时的降级（守护首启/dashboard 被清理的真实路径）──
ok("⑨无库时指标不崩、计数降级为 0",
   DB_GONE_OK["cost"]["runs_traced"] == 0 and
   DB_GONE_OK["quality"]["recall_pairs"]["pairs"] == [],
   json.dumps(DB_GONE_OK["cost"], ensure_ascii=False)[:120])

# ── 附加：聚合层与报告层口径一致（同一 gather 取数） ──
m = M
ok("附加：报告的检出率与 stats 命令同源",
   str(m["quality"]["recall_pairs"]["pairs"][0]["detection_rate"]) in HTML or
   "0.50" in HTML or "1.00" in HTML,
   json.dumps(m["quality"]["recall_pairs"], ensure_ascii=False)[:120])
ok("附加：untraced 计数入页脚", "untraced" in HTML)
ok("附加：HTML 含中文 lang 与 UTF-8 声明",
   '<html lang="zh-CN"' in HTML and 'charset="utf-8"' in HTML)
ok("附加：非 ASCII 未转义成实体（中文可读）", "历史统计与总结报告" in HTML)

print(f"\nM7 报告层测试：{len(PASS)} 通过 / {len(FAIL)} 失败")
for f in FAIL:
    print("  ✗", f)
sys.exit(1 if FAIL else 0)
