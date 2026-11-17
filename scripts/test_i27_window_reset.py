"""I27 window_reset：临时 git 仓验证 reset → 重放 → 编译/回退。"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.window_reset import reset_and_replay  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def git(p, *args):
    return subprocess.run(["git", "-C", str(p), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def make_repo():
    p = Path(tempfile.mkdtemp())
    git(p, "init")
    git(p, "config", "user.email", "t@example.com")
    git(p, "config", "user.name", "t")
    (p / "a.txt").write_text("base", encoding="utf-8")
    git(p, "add", "-A")
    git(p, "commit", "-m", "base")
    return p, git(p, "rev-parse", "HEAD").stdout.strip()


def reset_fn(p, ref):
    return git(p, "reset", "--hard", ref).returncode == 0


def apply_fn(patch):
    Path(patch["file"]).write_text(patch["content"], encoding="utf-8")
    return True


def rollback_noop(patch):
    return True


p, base = make_repo()
pa = {"file": str(p / "a.txt"), "content": "A"}
pb = {"file": str(p / "a.txt"), "content": "B"}
apply_fn(pa)
apply_fn(pb)
r = reset_and_replay(p, base, [("u1", pa)], reset=reset_fn, apply=apply_fn,
                     compile_fn=lambda: True, rollback=rollback_noop)
ok("reset+重放 verified", r["verified"] == ["u1"], r)
ok("重放后文件为 A", (p / "a.txt").read_text(encoding="utf-8") == "A",
   (p / "a.txt").read_text(encoding="utf-8"))

apply_fn(pb)
r2 = reset_and_replay(p, base, [("u1", pa)], reset=reset_fn, apply=apply_fn,
                      compile_fn=lambda: False, rollback=rollback_noop)
ok("重放编译失败清空", r2["applied"] == [] and r2["verified"] == [], r2)
ok("编译失败回 base", (p / "a.txt").read_text(encoding="utf-8") == "base",
   (p / "a.txt").read_text(encoding="utf-8"))
ok("编译失败记 failed", r2["failed"].get("u1") == "window_reapply_compile_failed",
   r2)

r3 = reset_and_replay(p, base, [("u1", pa)],
                      reset=lambda proj, ref: False, apply=apply_fn,
                      compile_fn=lambda: True, rollback=rollback_noop)
ok("reset 失败走 fallback", r3["fallback"] and r3["verified"] == [], r3)
ok("fallback 记 failed", r3["failed"].get("u1") == "group_reset_failed", r3)

r4 = reset_and_replay(p, "", [("u1", pa)], reset=reset_fn, apply=apply_fn,
                      compile_fn=lambda: True, rollback=rollback_noop)
ok("无 group_base 走 fallback", r4["fallback"] and r4["failed"], r4)

print(f"\n==== I27 window_reset: {len(PASS)} ok, {len(FAIL)} failed")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
