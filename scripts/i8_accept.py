"""I8 Lab 验收驱动：走 Web API（与页面按钮同一条路径），分场景落盘证据。

用法：
    python scripts/i8_accept.py --scenario b     # c02+c03：HUD/item 真实诊断
    python scripts/i8_accept.py --scenario a     # c01：BoardModel 真实诊断
    python scripts/i8_accept.py --scenario c     # c04：资产检测
    python scripts/i8_accept.py --scenario d     # 组合自测：叠加/编译/replay/覆盖

前提：Lab 已在 127.0.0.1:8787 运行（python scripts/i8_lab.py）。
证据：runs/i8_lab/accept/<scenario>_<ts>.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8787"
OUT = ROOT / "runs" / "i8_lab" / "accept"


def call(path: str, body: dict | None = None, timeout: int = 600) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def step(report: dict, name: str, path: str, body: dict | None = None,
         timeout: int = 600) -> dict:
    t0 = time.time()
    r = call(path, body, timeout)
    ms = int((time.time() - t0) * 1000)
    report["steps"].append({"step": name, "ms": ms, "ok": bool(r.get("ok")),
                            "path": path, "response": r})
    print(f"[{name}] {'ok' if r.get('ok') else 'FAIL'} {ms}ms"
          + ("" if r.get("ok") else " :: " + str(r.get("error"))[:160]))
    return r


def scenario_b(report: dict) -> None:
    cases = ["c02", "c03"]
    pv = step(report, "preview", "/api/preview", {"mode": "diagnostic", "cases": cases})
    if not pv.get("ok"):
        return
    step(report, "editor", "/api/editor")
    ap = step(report, "apply", "/api/apply", {"mode": "diagnostic"})
    if not ap.get("ok"):
        return
    rp = step(report, "replay", "/api/replay", {"mode": "diagnostic"})
    if not rp.get("ok"):
        return
    cap = step(report, "capture", "/api/capture", {"mode": "diagnostic", "cases": cases})
    if not cap.get("ok"):
        return
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "b_capture_before.json").write_text(
        json.dumps(cap, ensure_ascii=False, indent=2), encoding="utf-8")
    step(report, "diagnose", "/api/diagnose", {"mode": "diagnostic"}, timeout=300)
    step(report, "repair_plans", "/api/repair_plans", {"mode": "diagnostic"})
    rp2 = step(report, "repair", "/api/repair", {"mode": "diagnostic"}, timeout=600)
    if rp2.get("ok"):
        step(report, "replay_after_fix", "/api/replay", {"mode": "diagnostic"})
        cap2 = step(report, "capture_after_fix", "/api/capture",
                    {"mode": "diagnostic", "cases": cases})
        if cap2.get("ok"):
            (OUT / "b_capture_after.json").write_text(
                json.dumps(cap2, ensure_ascii=False, indent=2), encoding="utf-8")
    step(report, "restore", "/api/restore", {"mode": "diagnostic"})


def scenario_a(report: dict) -> None:
    cases = ["c01"]
    pv = step(report, "preview", "/api/preview", {"mode": "diagnostic", "cases": cases})
    if not pv.get("ok"):
        return
    step(report, "editor", "/api/editor")
    ap = step(report, "apply", "/api/apply", {"mode": "diagnostic"})
    if not ap.get("ok"):
        return
    rp = step(report, "replay", "/api/replay", {"mode": "diagnostic"})
    if not rp.get("ok"):
        return
    cap = step(report, "capture", "/api/capture", {"mode": "diagnostic", "cases": cases})
    if cap.get("ok"):
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "a_capture.json").write_text(
            json.dumps(cap, ensure_ascii=False, indent=2), encoding="utf-8")
    step(report, "diagnose", "/api/diagnose", {"mode": "diagnostic"}, timeout=300)
    step(report, "repair_plans", "/api/repair_plans", {"mode": "diagnostic"})
    step(report, "restore", "/api/restore", {"mode": "diagnostic"})


def scenario_c(report: dict) -> None:
    cases = ["c04"]
    pv = step(report, "preview", "/api/preview", {"mode": "diagnostic", "cases": cases})
    if not pv.get("ok"):
        return
    step(report, "editor", "/api/editor")
    ap = step(report, "apply", "/api/apply", {"mode": "diagnostic"})
    if not ap.get("ok"):
        return
    rp = step(report, "replay", "/api/replay", {"mode": "diagnostic"})
    if rp.get("ok"):
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "c_replay.json").write_text(
            json.dumps(rp, ensure_ascii=False, indent=2), encoding="utf-8")
    cap = step(report, "capture_asset_check", "/api/capture",
               {"mode": "diagnostic", "cases": cases})
    if cap.get("ok"):
        (OUT / "c_capture.json").write_text(
            json.dumps(cap, ensure_ascii=False, indent=2), encoding="utf-8")
    step(report, "restore", "/api/restore", {"mode": "diagnostic"})


def scenario_d(report: dict) -> None:
    cases = ["c01", "c02", "c03", "c04"]
    pv = step(report, "preview", "/api/preview", {"mode": "combo", "cases": cases})
    if not pv.get("ok"):
        return
    step(report, "editor", "/api/editor")
    ap = step(report, "apply", "/api/apply", {"mode": "combo"})
    if not ap.get("ok"):
        return
    step(report, "replay", "/api/replay", {"mode": "combo"})
    cov = step(report, "coverage", "/api/coverage", {"mode": "combo"})
    # 边界负例：组合模式不得产候选 / Agent / 修复
    for name, path in (("combo_capture_rejected", "/api/capture"),
                       ("combo_diagnose_rejected", "/api/diagnose"),
                       ("combo_repair_rejected", "/api/repair")):
        r = step(report, name, path, {"mode": "combo", "cases": cases})
        report.setdefault("guards", []).append(
            {"step": name, "rejected": not r.get("ok"), "error": r.get("error")})
    step(report, "restore", "/api/restore", {"mode": "combo"})
    report["coverage"] = cov


SCENARIOS = {"a": scenario_a, "b": scenario_b, "c": scenario_c, "d": scenario_d}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    a = ap.parse_args()
    st = call("/api/editor")
    if not st.get("ok"):
        print("Lab 未运行或 Unity 不可达：", st.get("error"))
        return 2
    report = {"scenario": a.scenario, "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "steps": []}
    try:
        SCENARIOS[a.scenario](report)
    finally:
        report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        report["all_ok"] = all(s["ok"] for s in report["steps"])
        OUT.mkdir(parents=True, exist_ok=True)
        p = OUT / f"{a.scenario}_{time.strftime('%Y%m%d_%H%M%S')}.json"
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"EVIDENCE {p}")
        print(f"ALL_OK {report['all_ok']} steps={len(report['steps'])}")
    return 0 if report.get("all_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
