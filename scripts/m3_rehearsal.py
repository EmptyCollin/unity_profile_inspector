#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m3_rehearsal.py —— M3 闭环彩排：F-CPU-01 修→验→回滚 全流程实走。

权限说明（如实）：CombatSkillManager.cs 属夹具脚本 = blocked 档，
管线**无自动执行权**。本次彩排走 human_approved 旁路（用户在对话中批准
开工，批准文本落进报告）。旁路的旁路防护在 fixer：批准记录为空=视同未批。

流程（每步产物落 runs/m3_rehearsal_<stamp>/）：
 0 编辑器就绪 + 夹具路径干净检查
 1 before 束当场重采（4 窗口 full 会话，与 after 同常驻态 ⇒ G7 可比）
 2 attempt 快照（空提交 + tag + 资产清单哈希）
 3 EditPlan 执行（4 条精确 diff：缓冲复用/组件缓存/键预生成/字典复用）
 4 G8 编译门禁（真实编辑器 recompile_status，失败即回滚不采样）
 5 after 束当场重采
 6 Verdict：目标 k·σ（或 G17 绝对门限）+ 全量守护 + P4 矛盾表
 7 回滚演练：git 复位 + 资产哈希校验（resolved 演示后恢复缺陷夹具，
   否则后续检出演示无原料——回滚不是失败，是本彩排的收尾步骤）

P2 语义断言 / G3 感知门禁：基建在 M7，本彩排如实标 pending。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents import fixer                                   # noqa: E402
from upi.agents.verify import (gc_floor_measure, resample,     # noqa: E402
                               verdict_from_bundles)
from upi.core.models import EditPlan                           # noqa: E402
from upi.tooling import snapshot as snapmod                    # noqa: E402

PROJECT = ROOT / "fixtures" / "upi-fixture"
CSFILE = "fixtures/upi-fixture/Assets/UpiFixtures/F_CPU_01/CombatSkillManager.cs"
SCENE = PROJECT / "Assets/UpiFixtures/Scenes/Scene_F-CPU-01.unity"

APPROVAL_NOTE = ("用户在 2026-11-08 对话中以「开工」批准 M3 彩排执行；"
                 "夹具 blocked 档走 human_approved 旁路，批准内容=本彩排"
                 "对 CombatSkillManager.cs 的 4 条语义保持改动")

# ---- 3 条精确 diff。**原文从当前文件按行切片取得**——手工誊写对齐空格
# 是不可靠做法（实测：注释前空格数目测不准导致命中失败）。行号为本轮
# dump 核对值；dry-run 校验 expect_count，任何一处不符即整体中止。
_CS = (ROOT / CSFILE).read_text(encoding="utf-8").split("\n")
_lines = lambda a, b: "\n".join(_CS[a - 1:b])      # 1-based 闭区间


def _mk(a, b, repl, why):
    return {"file": CSFILE, "find": _lines(a, b), "replace": repl,
            "expect_count": 1, "_why": why}


_NEW_AWAKE = """        private readonly List<SkillRuntime> _skills = new List<SkillRuntime>();

        // M3 修复（PB-FULL-REUSE，行段 %d-%d 替换）：缓存 + 预生成。语义等价
        // 前提——state/level 在 Awake 后不再被改写（Update 全文无赋值点，已核对），
        // 故 ready 筛选、排序结果、key 字符串均为常量，可一次预生成。
        private Rigidbody _body;
        private CapsuleCollider _col;
        private List<SkillRuntime> _readyRanked;
        private string[] _keyByIndex;
        private readonly Dictionary<int, float> _reusedCooldown = new Dictionary<int, float>();

        private void Awake()
        {
            for (int i = 0; i < skillCount; i++)
                _skills.Add(new SkillRuntime { index = i, level = 1 + (i % 3), state = (SkillState)(i % 4) });

            _body = GetComponent<Rigidbody>();
            _col = GetComponent<CapsuleCollider>();
            // 语义等价关键：原 Update 先筛 state==Ready 再筛 level>1 再排序。
            // 预生成必须取同一交集——若漏掉 Ready 筛选，非 Ready 技能的
            // score/lastKey 会从默认值变成被赋值，即改变可见行为（实测复查抓出）。
            _readyRanked = _skills.Where(x => x.state == SkillState.Ready)
                                   .Where(x => x.level > 1)
                                   .OrderByDescending(x => x.level).ThenBy(x => x.index).ToList();
            _keyByIndex = new string[skillCount];
            foreach (var s in _skills)
                _keyByIndex[s.index] = s.index + ":" + s.state + ":"
                    + (_body != null ? 1 : 0) + (_col != null ? 1 : 0);
        }""".replace("%d-%d", "21-27")

_NEW_UPDATE = """            // 每帧分配清零（①②③④ 逐条对应证据，行段 31-57 替换）：
            foreach (var s in _readyRanked)
            {
                var key = _keyByIndex[s.index];
                s.lastKey = key;
                // 原逻辑 map[key]=s.level; s.score=map[key]; map 每帧新建且
                // 只含该 key ⇒ s.score 恒等于 s.level，直取（字符串+字典分配消）
                s.score = s.level;
            }

            _reusedCooldown.Clear();
            for (int i = 0; i < _skills.Count; i++)
                _reusedCooldown[i] = Time.time + 0.1f * i;
            _cooldownMap = _reusedCooldown;"""

# 字段区：原声明保留为对外别名（cooldown 语义不破坏），新增复用缓冲在 _NEW_AWAKE 段
D_DIFF1 = _mk(21, 27, _NEW_AWAKE, "缓冲/组件/键 预生成（Awake 扩展）")
D_DIFF2 = _mk(31, 57, _NEW_UPDATE, "Update 热路径分配清零")

PLAN = EditPlan(playbook_id="PB-FULL-REUSE",
                diffs=[D_DIFF1, D_DIFF2],
                invariants_assert=["state/level 仅 Awake 赋值（静态核对）",
                                   "map[key]=level;score=map[key] ⇒ score=level 恒等",
                                   "_cooldownMap 声明保留（对外语义不破坏），改指向复用缓冲",
                                   "P2 语义断言基建 pending（M7）——本条如实标注"],
                side_effect="none",
                touched_assets=[CSFILE],
                permission_tier="blocked")


def editor_ready() -> bool:
    from upi.tooling.unity_cli import run_unity
    r = run_unity(["command", "editor status", "--project-path", str(PROJECT)], timeout_s=60)
    res = (r.data or {}).get("result") or {}
    return r.success and res.get("status") in ("ready", "playing") \
        and not res.get("compiling") and not res.get("domainReloadInProgress")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只验 diff 命中，不写入不采样")
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = ROOT / "runs" / f"m3_rehearsal_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"stamp": stamp, "approval_note": APPROVAL_NOTE, "steps": {}}

    print("== 0 前置 ==")
    if not editor_ready():
        print("!! 编辑器不可达/编译中/Safe Mode 待排查（G9：先 unity pipeline list）")
        return 2
    dirty, files = snapmod.workdir_state(PROJECT, ROOT)
    if dirty:
        print(f"!! 夹具路径有未提交改动，先处理：{files[:5]}")
        return 2
    print("   编辑器 ready，夹具路径干净")

    if args.dry_run:
        src = (ROOT / CSFILE).read_text(encoding="utf-8")
        for i, d in enumerate(PLAN.diffs):
            n = src.count(d["find"])
            print(f"   diff[{i}] 命中 {n}/{d['expect_count']}")
            if n != d["expect_count"]:
                return 1
        print("dry-run: 全部命中，未写入")
        return 0

    print("\n== 1 before 束重采（4 窗口，当前缺陷态）==")
    before = resample("F-CPU-01", f"m3_rehearsal_{stamp}/before")
    report["steps"]["before"] = {"path": before["_path"]}
    print("   ok:", before["_path"])

    print("\n== 1b 改前态 fresh 采集（记忆体守护两侧对称口径）==")
    # a4_altertest 实测教训：只采改后态的地板分不清"修复净代价"与
    # "两侧都在积累"。改前态必须同样采一份 fresh，post_gc 对 post_gc。
    floor_before = None
    try:
        floor_before = gc_floor_measure("F-CPU-01")
        report["steps"]["gc_floor_before"] = {
            k: {"pre_gc_MB": round(vv["pre_gc"] / 1e6, 2),
                "post_gc_MB": round(vv["post_gc"] / 1e6, 2)}
            for k, vv in floor_before.items()}
        for k, vv in floor_before.items():
            print(f"   {k:22s} pre_gc={vv['pre_gc']/1e6:8.2f}MB  "
                  f"post_gc={vv['post_gc']/1e6:8.2f}MB")
    except Exception as e:  # noqa: BLE001 — 缺侧即盲区，fail-open 到未评估
        report["steps"]["gc_floor_before"] = {"error": str(e)[:200]}
        print("   !! 改前态 fresh 失败（记忆体守护将记未评估盲区）:", str(e)[:140])

    print("\n== 2 attempt 快照 ==")
    fp = "warmup=8,window=20,poll=1.0,resident=breachprobe+replaydriver+a2,replay=none-v0"
    snap = snapmod.snapshot(f"m3-{stamp}", PROJECT, SCENE, fp, root=ROOT)
    report["steps"]["snapshot"] = snap.to_dict()
    print("   tag:", snap.git_ref, "amh:", snap.asset_manifest_hash)

    print("\n== 3+4 执行修复（human_approved 旁路）+ G8 编译门禁 ==")
    rep = fixer.execute(PLAN, snap, project_path=PROJECT, scene_path=SCENE, root=ROOT,
                        human_approved=True, approval_note=APPROVAL_NOTE)
    report["steps"]["execute"] = rep
    print("   status:", rep["status"])
    if rep["status"] != "applied":
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("!! 未落地即中止。详情:", str(rep)[:400])
        if rep["status"] == "compile_failed_rolled_back":
            print("   （G8 生效：已回滚、未烧采样——这条路径本身就是被验证的对象）")
            print("   报告:", out / "report.json")
            return 1
        return 1
    print("   applied:", rep["applied"])

    print("\n== 5 after 束重采（修复态，同常驻态同协议）==")
    after = resample("F-CPU-01", f"m3_rehearsal_{stamp}/after")
    report["steps"]["after"] = {"path": after["_path"]}

    print("\n== 5b 地板复采（修复态，monoUsedBytes GC 响应性实测）==")
    # 插在 after 与 verdict 之间：此刻夹具处修复态，与 after 束同状态。
    # 失败 → floor=None → fail-closed 维持 regressed，原因必须落报告。
    floor = None
    try:
        floor = gc_floor_measure("F-CPU-01")
        report["steps"]["gc_floor"] = {
            k: {"pre_gc_MB": round(vv["pre_gc"] / 1e6, 2),
                "post_gc_MB": round(vv["post_gc"] / 1e6, 2)}
            for k, vv in floor.items()}
        for k, vv in floor.items():
            print(f"   {k:22s} pre_gc={vv['pre_gc']/1e6:8.2f}MB  "
                  f"post_gc={vv['post_gc']/1e6:8.2f}MB")
    except Exception as e:  # noqa: BLE001 — 地板失败不毁彩排，降级为不豁免
        report["steps"]["gc_floor"] = {"error": str(e)[:200]}
        print("   !! 地板复采失败（维持 fail-closed，不豁免）:", str(e)[:160])

    print("\n== 6 Verdict ==")
    v, detail = verdict_from_bundles("DET-CPU-GC-FRAMEALLOC", "F-CPU-01",
                                     before, after, compile_ok=True,
                                     floor=floor, floor_before=floor_before)
    for q in detail.get("memory_guards_unevaluated", []):
        print(f"   [盲区] 记忆体守护未评估: {q['metric']} —— {q['basis'][:60]}")
    t = v.target
    print(f"   gc_alloc_per_frame: {t['before']:.0f} → {t['after']:.0f} "
          f"(改善 {t['delta']:.0f}, σ={t['sigma']:.0f}, basis={t['significance_basis']})")
    print("   判定:", v.status)
    for g in v.guards:
        print(f"   guard {g.metric:28s} {g.before:>12.3f} → {g.after:>12.3f} "
              f"Δ{g.delta:+.3f}{'  !!regressed' if g.regressed else ''}")
    if v.conflicts:
        print("   conflicts:", [c.rule_id for c in v.conflicts])
    report["steps"]["verdict"] = {"verdict": v.model_dump(), "detail": {
        k: (str(x) if not isinstance(x, (int, float, bool, list, dict, type(None))) else x)
        for k, x in detail.items()}}

    print("\n== 7 回滚演练（恢复缺陷夹具，保留彩排证据）==")
    rb = snapmod.rollback(snap, PROJECT, SCENE)
    report["steps"]["rollback_drill"] = rb
    print("   资产复位:", rb["asset_manifest_restored"], " 场景复位:", rb["scene_restored"],
          " 未碰 Editor:", not rb["editor_touched"])

    report["final"] = {"status": v.status,
                       "note": "resolved 后主动回滚=彩排收尾，不是失败判定；"
                               "P2 语义断言/G3 感知门禁 pending（M7）"}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                default=str), encoding="utf-8")
    ok = v.status == "resolved" and rb["asset_manifest_restored"] and not any(
        g.regressed for g in v.guards)
    print("\n==== 彩排结果:", "闭环全绿（resolved+守护零劣化+回滚复位）" if ok
          else f"见报告 {out/'report.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
