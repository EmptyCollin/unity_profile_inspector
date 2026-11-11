"""事件聚类（I2）：把探针事件收成 IssueCluster，并算跨 run 复现率。

单位纪律（DESIGN §14.3 / AGENTS §8.5）：
- 一个 replay/巡检里可能有多条相同或不同问题；不能按“一次探针响 = 一个问题”；
- 先按“指标 + 相对帧邻域 + 波形”聚成 cluster，再分类；
- cluster 是修复、复现率、验证的唯一单位。

本模块只做纯计算（输入事件，输出 cluster/复现率），不判断根因、不写文件。
"""
from __future__ import annotations

import hashlib
import math
import statistics
from typing import Literal

from pydantic import BaseModel, Field

from upi.core.probe_events import ProbeEvent

Shape = Literal["spike", "step", "periodic", "cluster", "unknown"]


class ClusterOccurrence(BaseModel):
    event_id: str
    kind: str
    metric: str
    rel_frame: int
    value: float
    data_tier: str
    series_n: int = 0
    ring_after_n: int = 0
    stats: dict = Field(default_factory=dict)


class IssueCluster(BaseModel):
    cluster_id: str
    metric: str
    shape: Shape
    kind: str
    data_tier: str
    occurrences: list[ClusterOccurrence] = Field(default_factory=list)
    first_rel: int = 0
    last_rel: int = 0
    count: int = 0
    ambiguous: str = ""

    def summary(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "metric": self.metric,
            "shape": self.shape,
            "kind": self.kind,
            "data_tier": self.data_tier,
            "first_rel": self.first_rel,
            "last_rel": self.last_rel,
            "count": self.count,
            "ambiguous": self.ambiguous,
            "values": [o.value for o in self.occurrences[:8]],
        }

    def features(self) -> dict:
        """指纹特征：机制级 + 时间桶；不含 run_id/文件名/类名。"""
        return {
            "metric": self.metric,
            "shape": self.shape,
            "kind": self.kind,
            "data_tier": self.data_tier,
            "rel_bucket": self.first_rel // 30,
        }


def _rel(e: ProbeEvent) -> int:
    if e.rel_frame >= 0:
        return e.rel_frame
    if e.frame_idx >= 0:
        return e.frame_idx
    return 0


def _shape_of(e: ProbeEvent) -> Shape:
    if e.kind == "breach":
        before = [float(x) for x in e.ring_before if x is not None]
        after = [float(x) for x in e.ring_after if x is not None]
        base = statistics.median(before) if before else 0.0
        if not after:
            return "unknown"
        peak = max(after)
        if base > 0 and peak > base * 1.3:
            # 回落到基线附近 = 单次尖峰；持续抬升 = 台阶
            return "spike" if after[-1] <= base * 1.2 else "step"
        return "unknown"

    vals = [float(x) for x in e.series if x is not None]
    if len(vals) < 3:
        return "unknown"
    med = statistics.median(vals)
    s = sorted(vals)
    p95 = s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]
    peak = max(vals)
    if med <= 0:
        return "spike" if peak > 0 else "unknown"
    ratio = p95 / med
    if peak > med * 1.5 and ratio > 1.2:
        return "spike"
    if ratio <= 1.2:
        return "step"
    return "cluster"


def _periodic(rels: list[int]) -> bool:
    if len(rels) < 3:
        return False
    gaps = [b - a for a, b in zip(rels, rels[1:])]
    if not gaps or any(g <= 0 for g in gaps):
        return False
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return False
    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    return math.sqrt(var) / mean < 0.2


def _cid(metric: str, shape: str, first_rel: int) -> str:
    raw = f"{metric}|{shape}|{first_rel // 30}"
    return "cl-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def cluster_events(events: list[ProbeEvent], *, merge_gap: int = 30,
                   min_occurrences: int = 1) -> list[IssueCluster]:
    """同指标 + 同波形 + 相对帧相邻的事件 → 一个 cluster。

    merge_gap 单位是回放相对帧。breach 通常 15~30；sample 窗口可按调用方放大。
    """
    buckets: dict[tuple[str, str, str], list[ProbeEvent]] = {}
    for e in events:
        if e.kind not in ("breach", "sample") or not e.metric:
            continue
        buckets.setdefault((e.metric, e.kind, _shape_of(e)), []).append(e)

    out: list[IssueCluster] = []
    for (metric, kind, shape), group in buckets.items():
        group.sort(key=_rel)
        cur: list[ProbeEvent] = []
        for e in group:
            if not cur:
                cur = [e]
                continue
            if _rel(e) - _rel(cur[-1]) <= max(1, merge_gap):
                cur.append(e)
            else:
                out.append(_build(metric, shape, cur, min_occurrences))
                cur = [e]
        if cur:
            out.append(_build(metric, shape, cur, min_occurrences))

    return sorted([c for c in out if c.count >= max(1, min_occurrences)],
                  key=lambda c: (c.metric, c.first_rel, c.shape))


def _build(metric: str, shape: Shape, group: list[ProbeEvent],
           min_occurrences: int) -> IssueCluster:
    rels = [_rel(e) for e in group]
    final_shape: Shape = shape
    if len(group) >= 3 and _periodic(rels):
        final_shape = "periodic"
    elif len(group) >= 3 and (max(rels) - min(rels)) <= 30:
        final_shape = "cluster"
    occ = [ClusterOccurrence(
        event_id=e.event_id, kind=e.kind, metric=e.metric,
        rel_frame=_rel(e), value=float(e.value), data_tier=e.data_tier,
        series_n=len(e.series), ring_after_n=len(e.ring_after),
        stats=dict(e.stats or {}),
    ) for e in group]
    return IssueCluster(
        cluster_id=_cid(metric, final_shape, min(rels)),
        metric=metric, shape=final_shape, kind=group[0].kind,
        data_tier=group[0].data_tier,
        occurrences=occ, first_rel=min(rels), last_rel=max(rels),
        count=len(group),
    )


class ClusterMatch(BaseModel):
    cluster: IssueCluster
    run_hits: list[int] = Field(default_factory=list)
    reproduction_rate: float = 0.0
    is_stable: bool = False


def _match_one(ref: IssueCluster, run: list[IssueCluster],
               rel_tol: int) -> IssueCluster | None:
    best = None
    best_d = 10 ** 9
    for c in run:
        if (c.metric != ref.metric or c.shape != ref.shape
                or c.kind != ref.kind or c.data_tier != ref.data_tier):
            continue
        d = abs(c.first_rel - ref.first_rel)
        if d <= rel_tol and d < best_d:
            best, best_d = c, d
    return best


def match_across_runs(runs: list[list[IssueCluster]], *,
                      rel_tol: int = 15,
                      stable_rate: float = 2.0 / 3.0) -> list[ClusterMatch]:
    """以第 1 个 run 为参照，匹配后续 run，算每个 cluster 的复现率。

    只按机制级特征 + 相对帧容差匹配；data_tier 不同不匹配（G7 同源要求）。
    """
    if not runs:
        return []
    out: list[ClusterMatch] = []
    seen: list[IssueCluster] = []

    def add(ref: IssueCluster, hits: list[int]):
        rate = round(len(hits) / len(runs), 3) if runs else 0.0
        out.append(ClusterMatch(cluster=ref, run_hits=hits,
                                reproduction_rate=rate,
                                is_stable=rate >= stable_rate))

    for ref in runs[0]:
        hits = [i for i, run in enumerate(runs) if _match_one(ref, run, rel_tol)]
        add(ref, hits)
        seen.append(ref)

    # 后续 run 独有的 cluster 也要进表（0/N 或部分复现），否则会“没看见”
    for i in range(1, len(runs)):
        for c in runs[i]:
            if any(s.metric == c.metric and s.shape == c.shape and s.kind == c.kind
                   and abs(s.first_rel - c.first_rel) <= rel_tol for s in seen):
                continue
            hits = [j for j, run in enumerate(runs) if _match_one(c, run, rel_tol)]
            add(c, hits)
            seen.append(c)

    return sorted(out, key=lambda m: (m.cluster.metric, m.cluster.first_rel,
                                      m.cluster.shape))


def align_clusters(clusters: list[IssueCluster]) -> list[IssueCluster]:
    """把 run 内最早 cluster 的相对帧归零，消 replay 起步抖动。

    只做**时间原点**对齐，不证明轨迹等价；cluster_id 保持不变，避免跨 run
    目标 ID 因平移而漂移。"""
    if not clusters:
        return []
    origin = min(c.first_rel for c in clusters)
    if origin == 0:
        return clusters
    out = []
    for c in clusters:
        occ = [o.model_copy(update={"rel_frame": o.rel_frame - origin})
               for o in c.occurrences]
        out.append(c.model_copy(update={
            "first_rel": c.first_rel - origin,
            "last_rel": c.last_rel - origin,
            "occurrences": occ,
        }))
    return out
