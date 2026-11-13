"""I9d：无干净基准也能标定（L0），并且阈值不再随仓库提交。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import os  # noqa: E402
os.environ.setdefault("UPI_PROFILE", "upi-fixture")  # m1_multiwin 导入期需要档案

from upi.agents import patrol as P                      # noqa: E402
from upi.config.project_profile import (ProjectProfile,  # noqa: E402
                                        register_profile, unregister_profile,
                                        use_profile)

PASS, FAIL = [], []


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp(prefix="upi_l0_"))
PROF = ProjectProfile(
    profile_id="l0-proj", project_path=TMP, git_root=TMP,
    subjects=["SampleScene"],
    clean_subject="",                      # 没有干净基准
    baselines_dir_rel="bl", cal_dir_rel="cal")
register_profile(PROF, activate=True)
use_profile(PROF.profile_id)

# ── calibrate_round：不拒标，走 L0（且不采样）─────────────────
bdir = TMP / "bl"
bdir.mkdir(parents=True, exist_ok=True)
seen = {}


def fake_resample(cmd, workdir, timeout_s, **kw):
    seen["cmd"] = list(cmd)
    return {"blindspot": None, "bundle": Path(workdir) / "multiwin_full.json"}


def fake_merge(cmd):
    return 0, "合并完成（L0：无噪声基准，仅绝对线）"


real_rs, real_mg = P.run_resample, P.run_merge
P.run_resample, P.run_merge = fake_resample, fake_merge
try:
    r = P.calibrate_round({"windows": 2}, caldir=TMP / "cal", baselines_dir=bdir)
finally:
    P.run_resample, P.run_merge = real_rs, real_mg
    unregister_profile(PROF.profile_id)

ok("无 clean 不拒标", r.get("blindspot") is None, str(r)[:160])
ok("标定记录 noise_basis=L0", r.get("noise_basis") == "L0")
ok("L0 不采样（省一轮编辑器占用）", "cmd" not in seen, str(seen)[:120])
ok("回读 L0 阈值文件", bool(r.get("thresholds"))
   and json.loads(Path(r["thresholds"]).read_text(encoding="utf-8"))["noise_basis"] == "L0")

# ── do_merge(l0)：没有 CLEAN 也写占位阈值 ─────────────────────
import scripts.m1_multiwin as M                          # noqa: E402

M.CALDIR = TMP / "cal2"
M.CALDIR.mkdir(parents=True, exist_ok=True)
M.ROOT = TMP
M._PROF = PROF
(M.CALDIR / "multiwin_full.json").write_text(
    json.dumps({"fixtures": {}, "protocol": {"x": 1}}), encoding="utf-8")
rc = M.do_merge(l0=True)
files = sorted((TMP / "bl").glob("thresholds_*.json"))
l0 = [f for f in files if json.loads(f.read_text(encoding="utf-8")).get("noise_basis") == "L0"]
ok("do_merge(l0) 返回 0", rc == 0, rc)
ok("写出 L0 占位阈值", len(l0) >= 1, [f.name for f in files])

# ── 仓库不得提交阈值/基线 ─────────────────────────────────────
import subprocess
out = subprocess.run(["git", "ls-files", "baselines"], cwd=str(ROOT),
                     capture_output=True, text=True, encoding="utf-8",
                     errors="replace").stdout.strip()
ok("baselines/ 未被 git 跟踪", out == "", out[:120])

print(f"\n==== 无基准标定/L0 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
