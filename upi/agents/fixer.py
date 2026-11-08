"""EditPlan 执行与编译门禁（M3，AGENTS §1.4 / G4 / G8 / G9 / I3）。

权限模型（I3：修改权限只来自权限矩阵，不来自 LLM 自我判断）：
- auto：直接执行；
- semi / blocked：必须携带 human_approved=True 且记录批准确认文本（谁、批了什么、
  在哪一次交互）——批准内容造假在流程层防不住，但**不落批准记录就拒执行**，
  这条在代码层钉死。

G8（编译门禁前置于采样）：diffs 落地 → AssetDatabase.Refresh → 轮询
recompile_status（实测教训：console get 不含脚本编译错误，不能用）。
编译失败 → 立即 git 回滚 → 本 attempt 失败返回，**不进入采样**（省一整轮成本）。

diff 形态（EditPlan.diffs 的 dict 约定，机械 search-replace）：
  {"file": 相对仓库根路径, "find": 精确原文, "replace": 替换文本,
   "expect_count": 命中次数（默认 1）}
find 不唯一/不命中 → 整个 plan 中止（不留半成品），这是"改动必须能一行一句
对应到证据"（§1.4）的执行侧保障。

可测性：root/project_path/scene_path 参数化，单测用临时目录即可测
apply_diffs 与权限闸，不需要编辑器在线。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from upi.core.models import EditPlan
from upi.tooling.snapshot import Snapshot, pre_execute_check, rollback
from upi.tooling.unity_cli import run_unity

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "fixtures" / "upi-fixture"


class FixError(RuntimeError):
    pass


def _cli(name: str, *extra: str, project: Path, timeout: int = 150) -> dict:
    env = run_unity(["command", name, "--project-path", str(project), *extra],
                    timeout_s=timeout)
    return {"ok": env.success, "data": env.data, "errors": [e.message for e in env.errors]}


def apply_diffs(plan: EditPlan, root: Optional[Path] = None) -> list[dict]:
    """机械应用 diffs。任何一片不精确命中 → 全部回退（不留半成品）。"""
    root = root or ROOT
    applied: list[dict] = []
    backups: list[tuple[Path, str]] = []
    try:
        for d in plan.diffs:
            fp = root / d["file"]
            src = fp.read_text(encoding="utf-8")
            n_expect = int(d.get("expect_count", 1))
            n = src.count(d["find"])
            if n != n_expect:
                raise FixError(f"diff 命中 {n} 次 ≠ 期望 {n_expect}：{d['file']}——"
                               "中止整个 plan（禁半成品落盘）")
            backups.append((fp, src))
            fp.write_text(src.replace(d["find"], d["replace"]), encoding="utf-8")
            applied.append({"file": d["file"], "replaced": n})
        return applied
    except Exception:
        for fp, src in backups:              # 回退已落盘的
            fp.write_text(src, encoding="utf-8")
        raise


def compile_gate(project: Optional[Path] = None, wait_s: int = 240) -> dict:
    """G8：触发刷新 → 轮询编译 → failed? 返回 {ok, errors[]}。"""
    project = project or PROJECT
    run_unity(["command", "script eval", "--project-path", str(project),
               "--code", 'UnityEditor.AssetDatabase.Refresh(); return "r";'],
              timeout_s=60)
    deadline = time.time() + wait_s
    last: dict = {"ok": False, "errors": ["未获得 recompile_status 回执"]}
    while time.time() < deadline:
        time.sleep(8)
        r = _cli("script recompile_status", project=project, timeout=60)
        if not r["ok"]:
            continue
        res = (r["data"] or {}).get("result") or {}
        if "failed" in res and res.get("compiling") is not True:
            return {"ok": not res["failed"],
                    "errors": [str(e) for e in (res.get("errors") or [])]}
    return last if last else {"ok": False, "errors": ["compile 轮询超时"]}


def execute(plan: EditPlan, snap: Optional[Snapshot], *,
            project_path: Optional[Path] = None,
            scene_path: Optional[Path] = None,
            root: Optional[Path] = None,
            human_approved: bool = False, approval_note: str = "") -> dict:
    """一个 attempt 的修复执行。返回执行报告（含回滚信息），永不抛半路异常。"""
    project_path = project_path or PROJECT
    scene_path = scene_path or (project_path / "Assets/UpiFixtures/Scenes/Scene_F-CPU-01.unity")
    rep = {"playbook_id": plan.playbook_id, "tier": plan.permission_tier,
           "status": "unknown"}

    # 权限闸（I3）
    if plan.permission_tier != "auto":
        if not human_approved:
            rep.update(status="permission_denied",
                       why=f"{plan.permission_tier} 档必须携带人工批准（human_approved=True）")
            return rep
        if not approval_note.strip():
            rep.update(status="permission_denied",
                       why="批准记录为空——批了什么都没说，视同未批")
            return rep

    # G4：无快照 / 快照失效 → 禁写入（任何 root 下一视同仁，无跳过分支）
    if snap is None:
        rep.update(status="no_snapshot", why="I4/G4：无 attempt 快照禁止写入")
        return rep
    try:
        pre_execute_check(snap, project_path=project_path)
    except Exception as e:
        rep.update(status="snapshot_stale", why=str(e)[:200])
        return rep

    try:
        rep["applied"] = apply_diffs(plan, root=root)
    except FixError as e:
        rep.update(status="diff_rejected", why=str(e)[:200],
                   rollback="未发生（整 plan 中止，无落盘残留）")
        return rep
    except Exception as e:  # noqa: BLE001 — IO 类失败同样要成报告，不半路抛
        rep.update(status="apply_io_error",
                   why=f"{type(e).__name__}: {str(e)[:180]}",
                   rollback="apply_diffs 内建回退已执行")
        return rep

    cg = compile_gate(project=project_path)
    rep["compile"] = cg
    if not cg["ok"]:
        rb = rollback(snap, project_path, scene_path)
        rep.update(status="compile_failed_rolled_back",
                   rollback=rb, why="G8：编译失败即回滚，不消耗采样")
        return rep

    rep.update(status="applied",
               approval_note=approval_note if plan.permission_tier != "auto" else "")
    return rep
