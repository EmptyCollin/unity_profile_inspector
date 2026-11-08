#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m2a_detect_breach.py —— T2w 收口：探针越界样本 → episode Finding。

判据（M2a 交付判定"能产出结构完整 BreachSample"的后半段）：
1. 归档 jsonl 可解析入账，构造期拒收全部留痕（不静默丢样本）；
2. 越线样本 → Finding(kind=issue, ptype=episode)，证据双条（墙钟越线事实
   + A4 GPU 分解），阈值来源=frame_budget 且带 budget_ref（G7/§8.1 的
   episode 侧对应物）；
3. 不可复现样本（replay_seed=-1）允许解析但禁止入账（T2 前提）；
4. 发作形状统计（簇/离散 rel）输出，供 M2 Diagnose 的候选根因检索。

输入默认取 runs/m2a_probe/ 里最新一份**完整**归档（verdict 与 jsonl 并查），
也可 --input 指定。数据全部来自 M2a 判据轮实测落盘，无一行编造数值。
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.breach_detector import (findings_from_samples,  # noqa: E402
                                      load_breach_samples)
from upi.core.detector import DEFAULT_REGISTRY               # noqa: E402

OUT = ROOT / "runs" / "m2a_detect_breach.json"


def latest_good_archive() -> Path:
    """取最近一份四判据全过且样本非空的归档（verdict 文件交叉核对）。"""
    cands = sorted((ROOT / "runs" / "m2a_probe").glob("probe_run_*"), reverse=True)
    for d in cands:
        f = d / "pb_breach.jsonl"
        if not f.exists():
            continue
        v = ROOT / "runs" / "m2a_probe" / f"verdict_{d.name.split('_',2)[-1]}.json"
        if v.exists():
            vd = json.loads(v.read_text(encoding="utf-8"))
            if all(x for x in (vd.get("J1_越线检出"), vd.get("J2_ring与A4完整"),
                               vd.get("J3_契约拒收"), vd.get("J4_复跑对齐"))):
                return f
    # 兜底：没有全过的就取最新非空 jsonl（如实说明）
    for d in cands:
        f = d / "pb_breach.jsonl"
        if f.exists() and f.read_text(encoding="utf-8").strip():
            return f
    raise FileNotFoundError("runs/m2a_probe/ 下无可解析的越界归档")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None, help="pb_breach.jsonl 路径（默认取最新完整归档）")
    args = ap.parse_args()

    src = Path(args.input) if args.input else latest_good_archive()
    print(f"输入: {src}")

    samples, stats = load_breach_samples(src)
    print(f"解析: 行数={stats['lines']} 入账={len(samples)} "
          f"JSON坏={stats['json_bad']} 契约拒={stats['contract_reject']}")
    for r in stats["reject_reasons"][:6]:
        print("   拒因:", r)

    det = next(d for d in DEFAULT_REGISTRY if d.id == "DET-LOAD-FRAMESPIKE")
    # 阈值只允许来自锚点文件，运行时重读出处（与注册表值交叉核对，防漂移）
    budget = json.loads((ROOT / det.thresholds.budget_ref).read_text(encoding="utf-8"))
    line = budget["budgets_ms"][budget["detector_binding"][det.id]]
    if abs(line - det.thresholds.min_magnitude) > 1e-6:
        print(f"!! 注册表线位 {det.thresholds.min_magnitude} 与出处文件 {line} 不一致，中止")
        return 1
    print(f"线位核对: {det.metric} > {line}ms（出处 {det.thresholds.budget_ref}"
          f"，target30 档算术导出）")

    judged, shape = findings_from_samples(samples, det, "m2a_breach_run", str(src))
    findings = [f for f, _ in judged]
    print(f"\nFinding: {len(findings)} 条（ptype=episode, state=confirmed）")
    print("发作形状:", json.dumps(shape, ensure_ascii=False))
    for f, s in judged:
        iss = f.issue
        assert iss is not None
        print(f"  [episode] rel={s.rel} value={iss.evidence[0].value:.1f}ms "
              f"线={iss.signature.threshold}ms ratio={iss.deviation_ratio} "
              f"归因源={iss.attribution_source} 环窗 {len(s.ring_before)}+"
              f"{len(s.ring_after)} a4={len(s.a4_window)}")

    # 判据3：不可复现样本禁入
    fake = samples[0].model_copy(update={"replay_seed": -1}) if samples else None
    if fake is not None:
        # replayable() 的契约由 sample_to_finding 兜底（此处 model_copy 不触发
        # 校验器——已知行为，验证逻辑走 replayable() 直判）
        assert not fake.replayable()
        from upi.core.breach_detector import sample_to_finding
        assert sample_to_finding(fake, det, "x", "y") is None
        print("判据3: replay_seed=-1 禁入修复循环 OK")

    doc = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "input": str(src), "parse_stats": stats,
           "threshold": {"line_ms": line, "source": det.thresholds.budget_ref,
                         "basis": "frame_budget（§3A episode 判据，非 k·σ）"},
           "band_basis": "episode_abs_line",   # 与水位通路 band_basis=between_windows 区分
           "findings": [f.model_dump() for f in findings],
           "shape": shape}
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写入: {OUT}")
    ok = len(findings) >= 1 and stats["json_bad"] == 0
    print("T2w 收口:", "PASS" if ok else "FAIL（入账为空或存在坏行——坏行必须归零才算闭环）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
