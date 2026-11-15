"""I26-ext E4：worktree 消融，目标工程零改动。"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.ablation_worktree import run  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
proj = TMP / "proj"
proj.mkdir()
(proj / "A.cs").write_text("void Update() { Work(); }\n", encoding="utf-8")
subprocess.run(["git", "-C", str(proj), "init"], check=True, capture_output=True)
subprocess.run(["git", "-C", str(proj), "config", "user.email", "a@b.c"],
               check=True, capture_output=True)
subprocess.run(["git", "-C", str(proj), "config", "user.name", "a"],
               check=True, capture_output=True)
subprocess.run(["git", "-C", str(proj), "add", "."], check=True, capture_output=True)
subprocess.run(["git", "-C", str(proj), "commit", "-m", "init"], check=True,
               capture_output=True)

seen = {}


def run_fn(path):
    seen["text"] = (path / "A.cs").read_text(encoding="utf-8")
    seen["path"] = path
    return {"ok": True}


r = run(proj, patch={"file": "A.cs", "find": "Work();", "replace": ""},
        run_fn=run_fn)
ok("mode worktree", r["mode"] == "worktree", r)
ok("worktree 内已 patch", "Work();" not in seen["text"], seen)
ok("原工程未变", "Work();" in (proj / "A.cs").read_text(encoding="utf-8"))
ok("worktree 已清理", not seen["path"].exists(), seen["path"])
ok("原工程 git status 干净",
   subprocess.run(["git", "-C", str(proj), "status", "--porcelain"],
                  capture_output=True, text=True).stdout.strip() == "")

print(f"\n==== I26-ext worktree 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
