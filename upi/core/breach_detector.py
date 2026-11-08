"""T2w 越界样本 → Finding 判定通路（M2a 收尾，DESIGN §2.4 / §3A / AGENTS §1.2）。

输入 = 探针落盘的 BreachSample[]；输出 = Finding[]（episode 类 Issue）。
与水位通路（m1_detect 的窗口对照）平行、互不混算：同一指标两种证据形态
分别成条，合并展示时各自标注来源。

判定口径（写死在这里，不散落）：
- 单帧值 × Detector.thresholds.min_magnitude（= 外部绝对线）；
  不用 k·σ——逐帧越线的统计意义在"越过工程外生线"，不在"比自己均值高"。
- 线位离常态足够远（30fps 档离 60fps 常态 ≥100%），warmup 豁免在探针侧，
  replayable() 判据在这里兜底。
- deviation_ratio 口径 = value / 绝对线（与水位通路的 fx/clean 不同义，
  字段同名、来源不同，detection.json 里用 band_basis 区分）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from upi.core.detector import Detector
from upi.core.models import (A4Frame, BreachSample, Evidence, Finding,
                             Issue, IssueSignature)


def load_breach_samples(jsonl_path: str | Path) -> tuple[list[BreachSample], dict]:
    """解析探针 jsonl 归档。返回 (可入账样本, 拒收统计)。

    单行非法/构造拒收 = 计数不崩（数据格式问题不拖垮整批），
    但每一笔拒收都必须进 stats——静默丢样本按 I1/G13 同口径禁止。
    """
    kept: list[BreachSample] = []
    stats = {"lines": 0, "json_bad": 0, "contract_reject": 0,
             "reject_reasons": []}
    p = Path(jsonl_path)
    if not p.exists():
        stats["missing_file"] = str(p)
        return kept, stats
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        stats["lines"] += 1
        try:
            row = json.loads(ln)
        except json.JSONDecodeError as e:
            stats["json_bad"] += 1
            stats["reject_reasons"].append(f"json: {e.msg}@col{e.pos}")
            continue
        try:
            row = dict(row)
            row["a4_window"] = [A4Frame(**x) for x in row.get("a4_window", [])]
            kept.append(BreachSample(**row))
        except Exception as e:  # noqa: BLE001 — 构造期拒收（D17/G19/哨兵），必须留痕
            stats["contract_reject"] += 1
            reason = str(e).splitlines()[0][:160]
            stats["reject_reasons"].append(f"contract: {reason}")
    return kept, stats


def sample_to_finding(sample: BreachSample, det: Detector,
                      run_id: str, raw_ref: str) -> Optional[Finding]:
    """单样本 → episode Issue。证据不足或不可复现 → None（不强行给结论，I2）。"""
    thr = det.thresholds.min_magnitude
    if sample.value <= thr:
        return None                      # 线以下不配叫发作
    if not sample.replayable():
        return None                      # 不可复现样本允许记录，禁进修复循环（T2）

    ev = [Evidence(tool="probe_upibreachprobe", raw_ref=raw_ref,
                   value=sample.value, unit=det.unit,
                   noise_band=0.0, data_tier=sample.data_tier),
          Evidence(tool="probe_frame_timing_a4", raw_ref=raw_ref,
                   value=max((f.gpu_frame_time_ms for f in sample.a4_window
                              if f.gpu_frame_time_ms is not None), default=0.0),
                   unit="ms(gpu, A4 窗口内最大)", data_tier="A4")]
    sig = IssueSignature(dim="frame_time_ms", metric="frame_time_ms",
                         mechanism=det.anchor_patterns[0],
                         threshold=thr, threshold_source="frame_budget")
    issue = Issue(signature=sig, link=det.link, evidence=ev,
                  magnitude=round(sample.value - thr, 4),
                  deviation_ratio=round(sample.value / thr, 3),
                  state="confirmed", ptype="episode",
                  attribution_source=det.attribution_source)
    return Finding(kind="issue", issue=issue)


def findings_from_samples(samples: list[BreachSample], det: Detector,
                          run_id: str, raw_ref: str) -> tuple[list[tuple[Finding, BreachSample]], dict]:
    """批量判定 + 归组统计（发作形状：孤立/成簇/周期，供 Diagnose 使用）。

    返回 (judged, shape)：judged = [(Finding, 对应样本)] 成对，位置匹配由判定
    循环维护——消费方不得按列表下标自行配对（实测踩过：过滤后的 findings
    与全量 samples 下标错位）。
    """
    judged: list[tuple[Finding, BreachSample]] = []
    rels = []
    for s in samples:
        if s.metric != det.metric:
            continue
        f = sample_to_finding(s, det, run_id, raw_ref)
        if f and f.issue:
            judged.append((f, s))
            rels.append(s.rel)
    rels.sort()
    # 形状分类（定性，进证据不进结论）：相邻 ≤20 rel 归并为一簇
    clusters: list[list[int]] = []
    for r in rels:
        if clusters and r - clusters[-1][-1] <= 20:
            clusters[-1].append(r)
        else:
            clusters.append([r])
    shape = {"n_episodes": len(rels), "n_clusters": len(clusters),
             "cluster_rel_ranges": [(c[0], c[-1]) for c in clusters],
             "max_deviation_ratio": (max((f.issue.deviation_ratio for f, _ in judged
                                          if f.issue), default=None))}
    return judged, shape


def det_fixture_seed(samples: list[BreachSample]) -> int:
    s = {x.replay_seed for x in samples if x.replayable()}
    return next(iter(s)) if len(s) == 1 else -1
