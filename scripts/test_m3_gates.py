"""M3 闭环机制件负例测试（G4/G8/G9/I3/I4 的构造与执行期拦截验证）。

运行：C:/Python314/python.exe scripts/test_m3_gates.py

手法：临时 git 仓库 + 临时"工程"目录（Assets/ProjectSettings 结构），
纯逻辑面（快照/权限闸/diff 中止/回滚复位）不需要编辑器在线；
G8 编译失败→回滚的**真实编辑器路径**由 m3_rehearsal.py 彩排覆盖。
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from upi.agents import fixer
from upi.core.models import EditPlan
from upi.tooling import snapshot as snapmod

PASS, FAIL = [], []


def chk(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + ("  " + extra if extra else ""))


def make_repo():
    """临时仓库：fixtures/proj/{Assets,ProjectSettings} + 初始提交。"""
    root = Path(tempfile.mkdtemp(prefix="upi_m3t_"))
    proj = root / "fixtures" / "proj"
    (proj / "Assets").mkdir(parents=True)
    (proj / "ProjectSettings").mkdir(parents=True)
    (proj / "Assets" / "A.cs").write_text("int x = NEW();\nint y = NEW();\n", encoding="utf-8")
    (proj / "ProjectSettings" / "P.asset").write_text("v: 1\n", encoding="utf-8")
    scene = proj / "scene.unity"
    scene.write_text("scene-body\n", encoding="utf-8")
    env_git = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t"}
    import os
    def g(*a):
        subprocess.run(["git", *a], cwd=str(root), check=True, capture_output=True, env={**os.environ, **env_git})
    g("init", "-q", "-b", "main")
    # 仓库本地身份（不碰全局配置）：否则 snapshot 的 git commit 因身份缺失失败
    g("config", "user.email", "upi-test@example.invalid")
    g("config", "user.name", "upi-test")
    g("add", "-A")
    g("commit", "-q", "-m", "init")
    return root, proj, scene


PLAN_OK = EditPlan(playbook_id="PB-TEST", permission_tier="blocked",
                   diffs=[{"file": "fixtures/proj/Assets/A.cs",
                           "find": "NEW()", "replace": "REUSED", "expect_count": 2}])
PLAN_BAD_COUNT = EditPlan(playbook_id="PB-TEST", permission_tier="blocked",
                          diffs=[{"file": "fixtures/proj/Assets/A.cs",
                                  "find": "NEW()", "replace": "REUSED", "expect_count": 5}])
PLAN_MISSING = EditPlan(playbook_id="PB-TEST", permission_tier="blocked",
                        diffs=[{"file": "fixtures/proj/Assets/A.cs",
                                "find": "DOES_NOT_EXIST", "replace": "X"}])


def main():
    root, proj, scene = make_repo()
    cs = proj / "Assets" / "A.cs"
    fp = "fingerprint-v1"

    # 1) 快照拒绝脏工作区（夹具路径内有未提交改动）
    (proj / "Assets" / "dirty.cs").write_text("junk\n", encoding="utf-8")
    try:
        snapmod.snapshot("A-dirty", proj, scene, fp, root=root)
        chk("① 脏区建快照 → 拒绝", False)
    except snapmod.SnapshotError as e:
        chk("① 脏区建快照 → 拒绝", "拒绝建立快照" in str(e))
    (proj / "Assets" / "dirty.cs").unlink()
    subprocess.run(["git", "add", "-A"], cwd=str(root), capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "clean up"], cwd=str(root), capture_output=True)

    # 2) G4：无快照执行 → 拒
    rep = fixer.execute(PLAN_OK, None, project_path=proj, scene_path=scene, root=root,
                        human_approved=True, approval_note="测试批准")
    chk("② 无快照 → no_snapshot 且未落盘", rep["status"] == "no_snapshot"
        and cs.read_text(encoding="utf-8") == "int x = NEW();\nint y = NEW();\n")

    # 3) I3：semi/blocked 无人批 → 拒；有人批无记录 → 拒
    snap = snapmod.snapshot("A1", proj, scene, fp, root=root)
    rep = fixer.execute(PLAN_OK, snap, project_path=proj, scene_path=scene, root=root,
                        human_approved=False)
    chk("③a blocked 未批 → permission_denied", rep["status"] == "permission_denied")
    rep = fixer.execute(PLAN_OK, snap, project_path=proj, scene_path=scene, root=root,
                        human_approved=True, approval_note="   ")
    chk("③b 批准记录空白 → permission_denied", rep["status"] == "permission_denied")

    # 4) diff 不精确命中 → plan 中止且**无半成品残留**
    before = cs.read_text(encoding="utf-8")
    rep = fixer.execute(PLAN_BAD_COUNT, snap, project_path=proj, scene_path=scene, root=root,
                        human_approved=True, approval_note="批注")
    chk("④a expect_count 不符 → diff_rejected", rep["status"] == "diff_rejected")
    rep = fixer.execute(PLAN_MISSING, snap, project_path=proj, scene_path=scene, root=root,
                        human_approved=True, approval_note="批注")
    chk("④b find 不命中 → diff_rejected", rep["status"] == "diff_rejected")
    chk("④c 中止后工作区与快照面一致（无半成品）", cs.read_text(encoding="utf-8") == before)

    # 5) G4 运行时：快照后发生未登记写入 → 拒执行
    (proj / "Assets" / "B.cs").write_text("sneaky\n", encoding="utf-8")
    rep = fixer.execute(PLAN_OK, snap, project_path=proj, scene_path=scene, root=root,
                        human_approved=True, approval_note="批注")
    chk("⑤ 快照后 HEAD 被移动 → snapshot_stale", rep["status"] in ("snapshot_stale", "no_snapshot"),
        rep["status"])
    # 恢复到快照点位再继续测
    subprocess.run(["git", "checkout", "-q", snap.git_ref, "--", "fixtures"], cwd=str(root))
    subprocess.run(["git", "clean", "-fd", "--", "fixtures"], cwd=str(root), capture_output=True)

    # 6) 回滚（G9 纯 git）：apply 后回滚，资产哈希与场景复位
    fixer.apply_diffs(PLAN_OK, root=root)
    changed = cs.read_text(encoding="utf-8")
    chk("⑥a apply_diffs 生效", changed == "int x = REUSED;\nint y = REUSED;\n")
    rb = snapmod.rollback(snap, proj, scene)
    chk("⑥b 回滚后资产哈希复位", rb["asset_manifest_restored"], str(rb)[:120])
    chk("⑥c 回滚后内容==快照", cs.read_text(encoding="utf-8") == before)
    chk("⑥d 回滚从未碰 Editor", rb["editor_touched"] is False)

    # 7) auto 档无需批准记录（正向对照）
    plan_auto = PLAN_OK.model_copy(update={"permission_tier": "auto"})
    snap2 = snapmod.snapshot("A2", proj, scene, fp, root=root)
    # execute 在编译门禁处需要编辑器——单测环境没有：用 monkeypatch 桩
    orig_cg = fixer.compile_gate
    fixer.compile_gate = lambda **kw: {"ok": True, "errors": []}
    try:
        rep = fixer.execute(plan_auto, snap2, project_path=proj, scene_path=scene, root=root)
        chk("⑦ auto 档直接执行 → applied", rep["status"] == "applied")
    finally:
        fixer.compile_gate = orig_cg
        snapmod.rollback(snap2, proj, scene)

    # 8) 编译失败 → 回滚（G8 路径用桩模拟编译失败结果）
    snap3 = snapmod.snapshot("A3", proj, scene, fp, root=root)
    fixer.compile_gate = lambda **kw: {"ok": False, "errors": ["error CS1002: ; expected"]}
    try:
        rep = fixer.execute(PLAN_OK, snap3, project_path=proj, scene_path=scene, root=root,
                            human_approved=True, approval_note="批注")
        chk("⑧a 编译失败 → compile_failed_rolled_back",
            rep["status"] == "compile_failed_rolled_back")
        chk("⑧b 失败后资产已复位（不进入采样）",
            cs.read_text(encoding="utf-8") == before
            and rep["rollback"]["asset_manifest_restored"])
    finally:
        fixer.compile_gate = orig_cg

    print(f"\nM3 门禁测试 {len(PASS)}/{len(PASS) + len(FAIL)}"
          + (f"，失败项: {FAIL}" if FAIL else "，全部通过"))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
