"""I25：checkpoint 状态树与预算。"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.repair_tree import (Budget, RepairTree, load_budget,  # noqa: E402
                                  restore_checkpoint, save_tree)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


# 1. 改善保留
t = RepairTree(finding_id="f", budget=Budget(max_attempts_per_state=2,
                                             max_attempts_per_finding=4))
ok("root 初始", t.current_id == "root")
ok("improved_partial 保留", t.record("improved_partial", patch_id="p1") == "keep")
ok("创建子状态", t.current.depth == 1 and t.current.parent_id == "root")

# 2. 子状态失败两次 → 回退父
ok("子失败1 retry", t.record("no_effect") == "retry")
ok("子失败2 backtrack", t.record("no_effect") == "backtrack")
ok("回到子状态父", t.current_id == "root")

# 3. 根失败达到 finding 配额
t2 = RepairTree(finding_id="f", budget=Budget(max_attempts_per_state=2,
                                              max_attempts_per_finding=3))
r = [t2.record("no_effect") for _ in range(3)]
ok("根失败前两次 retry", r[:2] == ["retry", "retry"], r)
ok("第三次 finding_failed", r[2] == "finding_failed", r)
ok("root_failed", t2.root_failed() is True)

# 4. infra 不扣配额
t3 = RepairTree(finding_id="f", budget=Budget(max_attempts_per_finding=1))
ok("infra 返回重试", t3.record("infra_failure") == "infra_retry")
ok("infra 不扣 finding 失败", t3.finding_failures == 0, t3.finding_failures)

# 5. 全局预算
t4 = RepairTree(finding_id="f", budget=Budget(max_total_attempts=2,
                                              max_attempts_per_finding=99,
                                              max_attempts_per_state=99))
t4.record("no_effect")
ok("第二次预算耗尽", t4.record("no_effect") == "budget_exhausted")

# 6. 深度上限
t5 = RepairTree(finding_id="f", budget=Budget(max_depth=0))
ok("深度耗尽", t5.record("improved_partial") == "depth_exhausted")

# 7. 预算 YAML
b = load_budget()
ok("读取 max_attempts_per_finding", b.max_attempts_per_finding == 6, b)
ok("读取 max_captures", b.max_captures == 24, b)
ok("读取 max_tokens", b.max_tokens == 200000, b)

p = save_tree(t, Path(tempfile.mkdtemp()) / "tree.json")
ok("状态树落盘", p.is_file() and '"states"' in p.read_text(encoding="utf-8"))

# 8. git_ref 真实回滚
repo = Path(tempfile.mkdtemp())
subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
(repo / "a.txt").write_text("v1", encoding="utf-8")
subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
subprocess.run(["git", "-C", str(repo), "commit", "-qm", "v1"], check=True)
ref = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                     capture_output=True, text=True).stdout.strip()
tg = RepairTree(finding_id="f", project_root=repo)
tg.record("improved_partial", patch_id="p1", git_ref=ref)
(repo / "a.txt").write_text("v2", encoding="utf-8")
r = restore_checkpoint(tg, "s1")
ok("git_ref 回滚", r["pointer_only"] is False and r["git_ref"] == ref, r)
ok("文件恢复 v1", (repo / "a.txt").read_text(encoding="utf-8") == "v1")

print(f"\n==== I25 状态树测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
