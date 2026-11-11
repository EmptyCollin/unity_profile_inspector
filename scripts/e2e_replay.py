"""I4b2b2b2：夹具 Unity 自动 replay 三次基线 E2E。

真实执行 scene open → Play → 探针采样 → Stop，共 3 次；输出基线与计划。
不要求有缺陷 cluster；夹具已修复时计划应为 no_stable_cluster。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.regression import (RegressionPlan, RegressionRunner,  # noqa: E402
                                 RegressionStore, ReplaySuite)
from upi.core.replay_driver import UnityReplayDriver  # noqa: E402
from upi.tooling.console import force_utf8_streams  # noqa: E402

force_utf8_streams()

TMP = Path(tempfile.mkdtemp(prefix="upi_replay_e2e_"))
checks: list[dict] = []


def ok(name: str, cond: bool, detail: str = ""):
    checks.append({"name": name, "ok": bool(cond), "detail": str(detail)[:400]})
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {str(detail)[:180]}"))


def main() -> int:
    prof_project = ROOT / "fixtures" / "upi-fixture"
    suite = ReplaySuite(
        suite_id="e2e-replay", name="e2e replay",
        project=str(prof_project),
        scene="Assets/UpiFixtures/Scenes/Scene_F-CPU-01.unity",
        protocol_fingerprint="fp-replay-e2e", warmup_s=2, window_s=2,
        quality_tier="urp-balanced")
    store = RegressionStore(TMP)
    store.save_suite(suite)
    drv = UnityReplayDriver(warmup_s=2, window_s=2)
    runner = RegressionRunner(suite, store, drv.run_once, align=True)
    mtx = runner.collect_baseline(3)
    ok("三次基线", len(mtx.runs) == 3, f"{len(mtx.runs)}")
    ok("每次都有探针事件", all(r.event_count > 0 for r in mtx.runs),
       str([r.event_count for r in mtx.runs]))
    ok("协议一致", mtx.protocol_ok(), mtx.protocol())
    plan = RegressionPlan.from_matrix(mtx)
    ok("计划生成", plan.status in ("ready", "no_stable_cluster"), plan.status)
    ok("计划有 protocol", bool(plan.protocol_fingerprint), plan.protocol_fingerprint)
    out = {
        "ok": all(c["ok"] for c in checks),
        "suite": suite.suite_id,
        "runs": [{"index": r.run_index, "events": r.event_count,
                  "clusters": len(r.clusters), "guards": r.guards,
                  "protocol": r.protocol_fingerprint} for r in mtx.runs],
        "plan": plan.model_dump(),
        "checks": checks,
    }
    p = ROOT / "runs" / f"e2e_replay_{time.strftime('%Y%m%d_%H%M%S')}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n==== replay 3 基线 {'通过' if out['ok'] else '失败'}: "
          f"{sum(c['ok'] for c in checks)}/{len(checks)}；证据 {p}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
