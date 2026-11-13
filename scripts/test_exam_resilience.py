"""I14：考试采集自愈（重试、错误落盘、checkpoint 跳过）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from exam_live import _capture_once  # noqa: E402
from upi.core.resilience import Checkpoint  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp(prefix="upi_exam_res_"))
run_dir = TMP / "run"
run_dir.mkdir()
cp = Checkpoint(run_dir)

# checkpoint 已完成 + 合并候选存在 → 跳过采集（不启动 Unity）
(run_dir / "merged_candidates.json").write_text("[]", encoding="utf-8")
cp.save(step="baseline#1", status="done")
okres, _ = _capture_once(ROOT / "no_such_project", "x", "bad.json",
                         run_dir, "baseline#1", checkpoint=cp)
ok("已完成采集直接跳过", okres and not (run_dir / "tool_errors.jsonl").exists())

# 无 checkpoint：失败重试 1 次，落 2 条错误
okres2, _ = _capture_once(ROOT / "no_such_project", "x", "bad.json",
                          run_dir, "post-capture#x", retries=1)
errs = (run_dir / "tool_errors.jsonl").read_text(encoding="utf-8").splitlines()
ok("失败重试一次", okres2 is False and len(errs) == 2, len(errs))
ok("错误含命令返回码", '"returncode"' in errs[0], errs[0][:120])

print(f"\n==== 考试采集自愈测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
