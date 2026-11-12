"""B 模式最小闭环验证：探针自动触发 → dump 前后窗口 → 直接进候选管道。

流程：Lab 组合/应用/replay → 跑 probe/upi_auto_evidence.cs（浅档常开+滚动清帧）
      → 读 auto_evidence.json → collect_evidence → evidence_candidates → 断言 HUD/item 候选。

用法：python scripts/i8_auto_evidence.py [--cases c02,c03]
结果：runs/lab_auto/evidence/candidates.json + runs/auto_evidence_<ts>.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from i8_accept import call  # noqa: E402

PROBE_DIR = ROOT / "runs" / "_probe"
CFG = PROBE_DIR / "auto_cfg.json"
OUT = PROBE_DIR / "auto_evidence.json"
STATUS = PROBE_DIR / "auto_status.json"
PROBE = ROOT / "probe" / "upi_auto_evidence.cs"
PROJECT = ROOT / "sim" / "project"
BUNDLE = ROOT / "runs" / "lab_auto" / "evidence" / "evidence_v2.json"
CANDS = ROOT / "runs" / "lab_auto" / "evidence" / "candidates.json"


def step(name: str, r: dict) -> bool:
    ok = bool(r.get("ok"))
    print(f"[{name}] {'ok' if ok else 'FAIL'}"
          + ("" if ok else " :: " + str(r.get("error"))[:160]))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="c02,c03")
    ap.add_argument("--trigger-bytes", type=int, default=64)
    ap.add_argument("--after-frames", type=int, default=10)
    ap.add_argument("--duration", type=float, default=30.0)
    a = ap.parse_args()
    cases = [c.strip() for c in a.cases.split(",") if c.strip()]

    report: dict = {"cases": cases, "steps": []}
    ok = True
    ok &= step("preview", call("/api/preview", {"mode": "diagnostic", "cases": cases}))
    ok &= step("apply", call("/api/apply", {"mode": "diagnostic"}))
    ok &= step("replay", call("/api/replay", {"mode": "diagnostic"}))
    if not ok:
        call("/api/restore", {"mode": "diagnostic"})
        return 1

    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    CFG.write_text(json.dumps({"trigger_bytes": a.trigger_bytes,
                               "after_frames": a.after_frames,
                               "duration_s": a.duration,
                               "threads": 8, "budget_ms": 4000}), encoding="utf-8")
    for f in (OUT, STATUS):
        try:
            f.unlink()
        except OSError:
            pass

    try:
        p = subprocess.run(["unity", "command", "script eval", "--project-path", str(PROJECT),
                            "--file", str(PROBE), "--timeout", "120", "--format", "json"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        d = json.loads(p.stdout)
        inner = ((d.get("data") or {}).get("result") or {})
        report["eval"] = {"outer": d.get("success"), "inner": inner.get("success"),
                          "errors": d.get("errors") or inner.get("errors")}
        if not d.get("success") or not inner.get("success", True):
            raise RuntimeError("auto probe eval 失败：" +
                               json.dumps(d.get("errors") or inner.get("errors"),
                                          ensure_ascii=False)[:300])
        deadline = time.time() + a.duration + 60
        while time.time() < deadline and not OUT.exists():
            time.sleep(0.4)
        if not OUT.exists():
            st = json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.exists() else {}
            if st.get("state") == "no_trigger":
                print("未触发：窗口内没有越过 trigger_bytes 的帧")
                report["result"] = {"triggered": False}
                return 2
            raise RuntimeError("auto_evidence.json 未落盘")
    finally:
        # 兜底：无论成败都关 profiler
        call("/api/restore", {"mode": "diagnostic"})
        subprocess.run(["unity", "command", "script eval", "--project-path", str(PROJECT),
                        "--code", ("UnityEditorInternal.ProfilerDriver.deepProfiling=false;"
                                   "UnityEditorInternal.ProfilerDriver.enabled=false;"
                                   "UnityEngine.Profiling.Profiler.enabled=false;"
                                   "UnityEditorInternal.ProfilerDriver.ClearAllFrames();"
                                   "return \"off\";"),
                        "--timeout", "30", "--format", "json"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)

    raw = json.loads(OUT.read_text(encoding="utf-8"))
    meta = raw.get("meta") or {}
    subprocess.run([sys.executable, str(ROOT / "scripts" / "collect_evidence.py"),
                    "--project", str(PROJECT), "--evidence", str(OUT),
                    "--run-id", "lab_auto", "--scene", "Assets/Scenes/00_Bootstrap.unity"],
                   cwd=str(ROOT), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "evidence_candidates.py"),
                    "--project", str(PROJECT), "--evidence", str(BUNDLE),
                    "--out", str(CANDS), "--limit", "20"],
                   cwd=str(ROOT), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60)
    cands = json.loads(CANDS.read_text(encoding="utf-8")).get("candidates", []) if CANDS.exists() else []
    hit = [c for c in cands
           if "UpdateHud" in c.get("root_cause", "") or "RefreshItemLabels" in c.get("root_cause", "")]
    report["result"] = {
        "triggered": True,
        "breach_prof_frame": meta.get("breach_prof_frame"),
        "breach_value": meta.get("breach_value"),
        "shape": meta.get("shape"),
        "frames": raw.get("frames"),
        "function_count": len(raw.get("functions") or []),
        "candidate_count": len(cands),
        "hud_item_hits": [c.get("root_cause") for c in hit],
        "candidates": [c.get("root_cause") for c in cands[:8]],
    }
    p = ROOT / "runs" / f"auto_evidence_{time.strftime('%Y%m%d_%H%M%S')}.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"triggered breach_frame={meta.get('breach_prof_frame')} "
          f"value={meta.get('breach_value')} shape={meta.get('shape')}")
    print(f"frames={raw.get('frames')} functions={len(raw.get('functions') or [])} "
          f"candidates={len(cands)}")
    print(f"HUD/item hits: {report['result']['hud_item_hits']}")
    print(f"EVIDENCE {p}")
    return 0 if hit else 3


if __name__ == "__main__":
    raise SystemExit(main())
