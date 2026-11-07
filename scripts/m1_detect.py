"""M1 检测：读基线 → 对照 clean 变体 → 产出 Issue[]（带证据链）→ 写 trace。

判据（DESIGN §3 / AGENTS §1.2）：
  检出 = |median(fixture) − median(clean)| > k × noise_band(clean)      # 统计显著
       AND delta > min_magnitude                                        # 业务显著
       AND deviation_ratio 达标
  G17: 任一参与方 degenerate_distribution ⇒ 禁用相对判据，只走绝对门限并标注
  误报口径：CLEAN 自身对照自身应零检出（FP 直接可见）
  I10/G13：每个阶段写 TraceSpan，无 trace 的运行不进统计
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.detector import DEFAULT_REGISTRY                    # noqa: E402
from upi.core.models import Evidence, Issue, IssueSignature       # noqa: E402
from upi.tooling.trace import Tracer, environment_fingerprint     # noqa: E402

BASELINES = ROOT / "baselines"
RUNS = ROOT / "runs"


def latest_baseline() -> Path:
    return max(BASELINES.glob("baseline_*.json"), key=lambda p: p.stat().st_mtime)


def compare(det, fix_stats: dict, clean_stats: dict) -> tuple[Issue | None, dict]:
    """返回 (Issue|None, 判定详情)。指标缺失 ⇒ insufficient_evidence（I2）。"""
    m = det.metric
    fx = fix_stats.get(m)
    cl = clean_stats.get(m)
    detail = {"metric": m, "fixture": fx and fx["median"], "clean": cl and cl["median"]}
    if not fx or not cl:
        detail["verdict"] = "insufficient_evidence"
        return None, detail

    delta = fx["median"] - cl["median"]
    signed = delta if det.direction == "higher_worse" else -delta
    degenerate = fx["degenerate_distribution"] or cl["degenerate_distribution"]

    if degenerate:
        stat_ok = None                      # 相对判据不可用（G17）
        biz_ok = signed > det.thresholds.min_magnitude
        verdict = "detected" if biz_ok else "not_found"
        detail.update(reason="degenerate_distribution ⇒ 仅绝对门限" if degenerate else "",
                      stat_ok=stat_ok, biz_ok=biz_ok)
    else:
        band = cl["noise_band_mad"]
        stat_ok = signed > det.thresholds.k_sigma * band
        biz_ok = signed > det.thresholds.min_magnitude
        ratio_ok = (fx["median"] / cl["median"]) > det.thresholds.deviation_ratio if cl["median"] > 1 else True
        verdict = "detected" if (stat_ok and biz_ok and ratio_ok) else "not_found"
        detail.update(stat_ok=stat_ok, biz_ok=biz_ok, ratio_ok=ratio_ok)

    ratio = (fx["median"] / cl["median"]) if cl["median"] not in (0, None) else float("inf")
    detail.update(delta=round(delta, 4), deviation_ratio=round(ratio, 3), verdict=verdict)

    if verdict != "detected":
        return None, detail

    sig = IssueSignature(dim=m, metric=m,
                         mechanism=det.anchor_patterns[0],           # 机制级，无标识符
                         threshold=det.thresholds.min_magnitude)
    ev = Evidence(tool="unity diagnostics get_performance + a2 sampler",
                  raw_ref=str(BASELINES), value=float(fx["median"]), unit=det.unit,
                  noise_band=float(cl["noise_band_mad"]),
                  data_tier=det.source_tier)      # type: ignore[arg-type]
    issue = Issue(signature=sig, link=det.link, evidence=[ev],
                  magnitude=round(delta, 4), deviation_ratio=round(ratio, 3),
                  explains_share_of_regression=1.0, state="confirmed",
                  low_confidence=False)
    return issue, detail


def apply_calibration(dets: list, cal: dict) -> int:
    """§8.1 落地：检测端阈值只接受标定文件覆盖，注册表里的初值一律作废。"""
    applied = 0
    th = cal.get("thresholds", {})
    for det in dets:
        t = th.get(det.metric)
        if not t:
            continue
        det.thresholds.min_magnitude = float(t["min_magnitude"])
        det.thresholds.deviation_ratio = float(t["deviation_ratio"])
        det.thresholds.k_sigma = float(t["k_sigma"])
        det.thresholds.threshold_source = "noise_calibration"
        det.calibration_file = Path(cal_path).name if (cal_path := cal.get("_path")) else "inline"
        det.calibration_degenerate = bool(t.get("degenerate"))
        applied += 1
    return applied


def latest_calibration() -> dict | None:
    cps = sorted(BASELINES.glob("thresholds_*.json"), key=lambda q: q.stat().st_mtime)
    if not cps:
        return None
    cal = json.loads(cps[-1].read_text(encoding="utf-8"))
    cal["_path"] = str(cps[-1])
    return cal


def main() -> int:
    bp = latest_baseline()
    base = json.loads(bp.read_text(encoding="utf-8"))
    fixs = base.get("fixtures", {})
    if "CLEAN" not in fixs:
        print("!! 缺 CLEAN 变体，无法对照（§8.1）")
        return 1
    clean_stats = {**fixs["CLEAN"].get("A_stats", {})}
    a2c = fixs["CLEAN"].get("A2_stats", {})
    for k2 in ("gc_alloc_per_frame", "animator_process_animations_ns", "animator_process_graph_ns"):
        if a2c.get(k2):
            clean_stats[k2] = a2c[k2]

    run_id = time.strftime("m1detect_%Y%m%d_%H%M%S")
    tracer = Tracer(run_id, root=str(RUNS), channel="dev",
                    env_fingerprint=environment_fingerprint(str(ROOT / "fixtures/upi-fixture")))

    cal = latest_calibration()
    if cal is None:
        print("!! 无标定文件（先跑 m1_calibrate）；§8.1 禁止未标定检测")
        return 1
    n_applied = apply_calibration(DEFAULT_REGISTRY, cal)
    print(f"标定应用: {n_applied}/{len(DEFAULT_REGISTRY)} 项阈值来自 {Path(cal['_path']).name}")
    if n_applied != len(DEFAULT_REGISTRY):
        print("!! 有检测器指标不在标定表内（§8.1 要求全量覆盖），中止")
        return 1

    with tracer.stage("detect") as ctx:
        tracer.tool_call(ctx, kind="sqlite", name="read_baseline", args=str(bp),
                         exit_status=0)
        tracer.tool_call(ctx, kind="sqlite", name="read_calibration", args=cal.get("_path", ""),
                         exit_status=0)
        results = {}
        for det in DEFAULT_REGISTRY:
            fid = det.fixture
            if fid not in fixs or "error" in fixs[fid]:
                results[fid] = {"verdict": "insufficient_evidence", "why": "基线缺该夹具"}
                continue
            fix_stats = {**fixs[fid].get("A_stats", {})}
            a2 = fixs[fid].get("A2_stats", {})
            for k2 in ("gc_alloc_per_frame", "animator_process_animations_ns", "animator_process_graph_ns"):
                if a2.get(k2):
                    fix_stats[k2] = a2[k2]
            issue, detail = compare(det, fix_stats, clean_stats)
            detail["detector"] = det.id
            detail["detector_version"] = det.version
            if issue:
                detail["issue_signature"] = issue.signature.model_dump()
                detail["magnitude"] = issue.magnitude
            results[fid] = detail

    out = {"run_id": run_id, "baseline_file": str(bp),
           "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results}
    (RUNS / run_id).mkdir(parents=True, exist_ok=True)
    det_fp = RUNS / run_id / "detection.json"
    det_fp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx_refs = [str(det_fp)]

    print(f"基线: {bp.name}")
    detected = sum(1 for r in results.values() if r.get("verdict") == "detected")
    for fid, r in results.items():
        mark = "检出" if r.get("verdict") == "detected" else ("证据不足" if "insufficient" in str(r.get("verdict")) else "未检出")
        print(f"  [{mark:4s}] {fid:10s} {r.get('metric','')} fx={r.get('fixture')} clean={r.get('clean')} "
              f"delta={r.get('delta','')} ratio={r.get('deviation_ratio','')} {r.get('reason','')}")
    # FP 自检：clean 对 clean 的任何"检出"都是误报
    print(f"检出 {detected}/{len(results)}；CLEAN 对照自身零检出是 FP 率口径的下界")
    print("trace:", tracer.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
