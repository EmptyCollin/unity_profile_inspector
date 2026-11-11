"""回归形态（I4a）：replay 套件、3 次基线、复现率、批量计划、verified 判定。

纪律（AGENTS §8.3）：
- 只有回归形态能记 `verified`；
- 判据 = 目标 cluster 消失 + 无新 cluster + 守护不劣化 + 同协议指纹；
- `final=aligned` 只证明轨迹对齐，不证明性能可重复；
- 同一 replay 里可能多 cluster；批量计划只收“复现率达标 + 分类明确”的 cluster。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from upi.core import events as E
from upi.core.cluster import ClusterMatch, IssueCluster, match_across_runs

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "runs" / "_regression"


class ReplaySuite(BaseModel):
    suite_id: str
    name: str = ""
    project: str = ""
    scene: str = ""
    replay_script: str = ""
    quality_tier: str = ""
    warmup_s: int = 0
    window_s: int = 0
    budget: dict = Field(default_factory=dict)
    command: str = ""
    protocol_fingerprint: str = ""
    created_at: str = ""


class RegressionRun(BaseModel):
    run_id: str
    suite_id: str
    run_index: int
    protocol_fingerprint: str
    clusters: list[IssueCluster] = Field(default_factory=list)
    event_count: int = 0
    status: Literal["collected", "failed"] = "collected"
    collected_at: str = ""


def _norm_protocol(s: str) -> str:
    return (s or "").strip()


class BaselineMatrix(BaseModel):
    suite_id: str
    runs: list[RegressionRun] = Field(default_factory=list)
    min_runs: int = 3
    stable_rate: float = 2.0 / 3.0
    created_at: str = ""

    def protocol(self) -> str:
        vals = {_norm_protocol(r.protocol_fingerprint) for r in self.runs}
        return vals.pop() if len(vals) == 1 else ""

    def protocol_ok(self) -> bool:
        return bool(self.protocol())

    def matches(self) -> list[ClusterMatch]:
        if not self.runs:
            return []
        return match_across_runs([r.clusters for r in self.runs],
                                 stable_rate=self.stable_rate)

    def stable_matches(self) -> list[ClusterMatch]:
        return [m for m in self.matches() if m.is_stable and not m.cluster.ambiguous]


class RegressionPlanItem(BaseModel):
    cluster_id: str
    metric: str
    shape: str
    kind: str
    data_tier: str
    first_rel: int
    reproduction_rate: float
    status: Literal["pending", "skipped"] = "pending"


class RegressionPlan(BaseModel):
    plan_id: str
    suite_id: str
    protocol_fingerprint: str
    created_at: str
    items: list[RegressionPlanItem] = Field(default_factory=list)
    status: Literal["ready", "no_stable_cluster", "insufficient_runs",
                    "protocol_mismatch"] = "ready"

    @classmethod
    def from_matrix(cls, m: BaselineMatrix) -> "RegressionPlan":
        if len(m.runs) < m.min_runs:
            return cls(plan_id="rp-" + hashlib.sha256(
                (m.suite_id + str(time.time())).encode()).hexdigest()[:10],
                suite_id=m.suite_id, protocol_fingerprint=m.protocol(),
                created_at=E.now(), status="insufficient_runs")
        if not m.protocol_ok():
            return cls(plan_id="rp-" + hashlib.sha256(
                (m.suite_id + str(time.time())).encode()).hexdigest()[:10],
                suite_id=m.suite_id, protocol_fingerprint="",
                created_at=E.now(), status="protocol_mismatch")
        items = [RegressionPlanItem(
            cluster_id=x.cluster.cluster_id, metric=x.cluster.metric,
            shape=x.cluster.shape, kind=x.cluster.kind,
            data_tier=x.cluster.data_tier, first_rel=x.cluster.first_rel,
            reproduction_rate=x.reproduction_rate,
        ) for x in m.stable_matches()]
        pid = "rp-" + hashlib.sha256(
            f"{m.suite_id}|{m.protocol()}|{len(items)}".encode()).hexdigest()[:10]
        return cls(plan_id=pid, suite_id=m.suite_id,
                   protocol_fingerprint=m.protocol(), created_at=E.now(),
                   items=items,
                   status="ready" if items else "no_stable_cluster")


VerdictStatus = Literal["verified", "target_present", "new_cluster",
                        "guard_regressed", "protocol_mismatch", "insufficient"]


class RegressionVerdict(BaseModel):
    status: VerdictStatus
    verification_status: Literal["verified", "unverified", "failed"]
    target_cluster_id: str
    target_present: bool = False
    new_clusters: list[str] = Field(default_factory=list)
    guard_regressions: list[str] = Field(default_factory=list)
    reason: str = ""


def _match(c: IssueCluster, ref: IssueCluster, rel_tol: int = 15) -> bool:
    return (c.metric == ref.metric and c.shape == ref.shape
            and c.kind == ref.kind and c.data_tier == ref.data_tier
            and abs(c.first_rel - ref.first_rel) <= rel_tol)


def verify_after(matrix: BaselineMatrix, target: IssueCluster,
                 after_clusters: list[IssueCluster], *,
                 after_protocol: str,
                 guards_before: Optional[dict] = None,
                 guards_after: Optional[dict] = None,
                 rel_tol: int = 15) -> RegressionVerdict:
    if not matrix.runs:
        return RegressionVerdict(status="insufficient", verification_status="failed",
                                 target_cluster_id=target.cluster_id,
                                 reason="no baseline runs")
    if _norm_protocol(after_protocol) != matrix.protocol():
        return RegressionVerdict(status="protocol_mismatch",
                                 verification_status="failed",
                                 target_cluster_id=target.cluster_id,
                                 reason="protocol fingerprint mismatch")

    present = any(_match(c, target, rel_tol) for c in after_clusters)
    baseline = [c for r in matrix.runs for c in r.clusters]
    new = [c.cluster_id for c in after_clusters
           if not any(_match(c, b, rel_tol) for b in baseline)]
    regs: list[str] = []
    if guards_before and guards_after:
        for k, before in guards_before.items():
            if k not in guards_after:
                continue
            after = guards_after[k]
            try:
                if float(before) <= 0:
                    bad = float(after) > 0
                else:
                    bad = float(after) > float(before) * 1.01
            except (TypeError, ValueError):
                continue
            if bad:
                regs.append(k)

    if present:
        return RegressionVerdict(status="target_present",
                                 verification_status="unverified",
                                 target_cluster_id=target.cluster_id,
                                 target_present=True,
                                 new_clusters=new, guard_regressions=regs,
                                 reason="target cluster still present")
    if new:
        return RegressionVerdict(status="new_cluster",
                                 verification_status="unverified",
                                 target_cluster_id=target.cluster_id,
                                 new_clusters=new, guard_regressions=regs,
                                 reason="new cluster(s) appeared")
    if regs:
        return RegressionVerdict(status="guard_regressed",
                                 verification_status="unverified",
                                 target_cluster_id=target.cluster_id,
                                 guard_regressions=regs,
                                 reason="guard metric regressed")
    return RegressionVerdict(status="verified", verification_status="verified",
                             target_cluster_id=target.cluster_id,
                             reason="target gone, no new cluster, guards ok, protocol same")


class RegressionStore:
    def __init__(self, root: Optional[Path | str] = None):
        self.root = Path(root) if root else DEFAULT_DIR
        self.suites = self.root / "suites"
        self.matrices = self.root / "matrices"
        self.suites.mkdir(parents=True, exist_ok=True)
        self.matrices.mkdir(parents=True, exist_ok=True)

    def save_suite(self, s: ReplaySuite) -> Path:
        p = self.suites / f"{s.suite_id}.json"
        E.atomic_write_text(p, s.model_dump_json(indent=2))
        return p

    def load_suite(self, sid: str) -> Optional[ReplaySuite]:
        p = self.suites / f"{sid}.json"
        if not p.exists():
            return None
        try:
            return ReplaySuite(**json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return None

    def save_matrix(self, m: BaselineMatrix) -> Path:
        p = self.matrices / f"{m.suite_id}.json"
        E.atomic_write_text(p, m.model_dump_json(indent=2))
        return p

    def load_matrix(self, sid: str) -> Optional[BaselineMatrix]:
        p = self.matrices / f"{sid}.json"
        if not p.exists():
            return None
        try:
            return BaselineMatrix(**json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return None
