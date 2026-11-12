"""c04 真实资产 case：payload 放大 + .meta，和代码 case 可组合。"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.case_overlay import apply_case, apply_cases  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


PROJECT = ROOT / "sim" / "project"
CASE = ROOT / "sim" / "cases" / "c04"
base_payload = (PROJECT / "Assets/Resources/DevSim/payload_0.txt").read_bytes()
out = Path(tempfile.mkdtemp(prefix="upi_c04_")) / "p"
try:
    run = apply_case(PROJECT, CASE, out)
    ok("c04 应用", run.case_id == "c04", run)
    payload = out / "Assets/Resources/DevSim/payload_0.txt"
    ok("payload 已放大", payload.stat().st_size > 300000, payload.stat().st_size)
    ok("meta 已复制", (out / "Assets/Resources/DevSim/payload_0.txt.meta").exists())
    ok("base 未修改",
       (PROJECT / "Assets/Resources/DevSim/payload_0.txt").read_bytes() == base_payload)
finally:
    shutil.rmtree(out.parent, ignore_errors=True)

out2 = Path(tempfile.mkdtemp(prefix="upi_c04c_")) / "p"
try:
    run = apply_cases(PROJECT, [ROOT / "sim/cases/c01", ROOT / "sim/cases/c02",
                                ROOT / "sim/cases/c03", CASE], out2)
    ok("四 case 组合", run.case_ids == ["c01", "c02", "c03", "c04"], run.case_ids)
    ok("资产/代码共存", (out2 / "Assets/Resources/DevSim/payload_0.txt").stat().st_size > 300000
       and "lastHudRound" not in (out2 / "Assets/DevSim/Runtime/ChromaGame.cs").read_text(encoding="utf-8")
       and "_hitBuffer" not in (out2 / "Assets/DevSim/Runtime/BoardModel.cs").read_text(encoding="utf-8"))
finally:
    shutil.rmtree(out2.parent, ignore_errors=True)

print(f"\n==== c04 资产 case 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
