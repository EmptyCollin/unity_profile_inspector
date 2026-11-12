"""c01 overlay runner 测试：同路径替换、base 不动、资产 .meta 约束。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.case_overlay import apply_case  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


PROJECT = ROOT / "sim" / "project"
CASE = ROOT / "sim" / "cases" / "c01"
base_before = (PROJECT / "Assets/DevSim/Runtime/BoardModel.cs").read_bytes()
out = Path(tempfile.mkdtemp(prefix="upi_c01_")) / "project"
try:
    run = apply_case(PROJECT, CASE, out)
    ok("case 应用", run.case_id == "c01" and len(run.files) == 1, run)
    applied = (out / "Assets/DevSim/Runtime/BoardModel.cs").read_text(encoding="utf-8")
    ok("overlay 已覆盖", "var hit = new bool[N, N];" in applied)
    ok("优化已回退", "_hitBuffer" not in applied and "_perColorBuffer" not in applied)
    ok("base 未被修改",
       (PROJECT / "Assets/DevSim/Runtime/BoardModel.cs").read_bytes() == base_before)
    # 同路径但 base 哈希被篡改必须拒绝
    bad = Path(tempfile.mkdtemp(prefix="upi_c01_bad_")) / "case"
    shutil.copytree(CASE, bad)
    m = json.loads((bad / "manifest.json").read_text(encoding="utf-8"))
    m["base_hash"] = "deadbeef"
    (bad / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    try:
        apply_case(PROJECT, bad, out.parent / "bad")
        ok("base 哈希不匹配拒绝", False)
    except ValueError:
        ok("base 哈希不匹配拒绝", True)
finally:
    shutil.rmtree(out.parent, ignore_errors=True)

print(f"\n==== c01 overlay 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
