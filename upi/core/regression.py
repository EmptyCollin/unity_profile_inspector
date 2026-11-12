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
import os
import statistics
import time
from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

from upi.core import events as E
from upi.core.cluster import (ClusterMatch, IssueCluster, align_clusters,
                              cluster_events, match_across_runs)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "runs" / "_regression"


class ReplaySuite(BaseModel):
    suite_id: str
    project_id: str = ""
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
    project_id: str = ""
    run_index: int
    protocol_fingerprint: str
    clusters: list[IssueCluster] = Field(default_factory=list)
    guards: dict = Field(default_factory=dict)
    event_count: int = 0
    status: Literal["collected", "failed"] = "collected"
    collected_at: str = ""


def guards_from_events(events: list) -> dict:
    """同 run 各指标的窗口中位；只用于 guard 对比，不当 finding。"""
    by: dict[str, list[float]] = {}
    for e in events:
        try:
            if getattr(e, "kind", "") == "sample" and getattr(e, "series", None):
                v = statistics.median(float(x) for x in e.series if x is not None)
            elif getattr(e, "kind", "") == "breach" and getattr(e, "value", None) is not None:
                v = float(e.value)
            else:
                continue
        except (TypeError, ValueError, statistics.StatisticsError):
            continue
        by.setdefault(str(getattr(e, "metric", "")), []).append(v)
    return {k: round(statistics.median(v), 4) for k, v in by.items() if v}


def _norm_protocol(s: str) -> str:
    return (s or "").strip()


def merge_gap_for(suite: ReplaySuite) -> int:
    """sample 窗口按帧合并：默认 5s×60fps=300，至少 30。"""
    return max(30, int(round(float(suite.window_s or 5) * 60)))


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


class RegressionRunner:
    """可注入的 replay 执行器：真正的 Unity 驱动在 I4b2b2 接。
    run_once(suite) -> (protocol_fingerprint, events)
    """
    def __init__(self, suite: ReplaySuite, store: "RegressionStore",
                 run_once: Callable[[ReplaySuite], tuple[str, list]],
                 *, align: bool = False):
        self.suite = suite
        self.store = store
        self.run_once = run_once
        self.align = align

    def collect_baseline(self, n: int = 3) -> BaselineMatrix:
        if n < 3:
            raise ValueError("baseline 至少 3 次")
        m = BaselineMatrix(suite_id=self.suite.suite_id, min_runs=n)
        for i in range(n):
            proto, events = self.run_once(self.suite)
            cs = cluster_events(events, merge_gap=merge_gap_for(self.suite),
                                merge_sample=True)
            if self.align:
                cs = align_clusters(cs)
            m.runs.append(RegressionRun(
                run_id=f"{self.suite.suite_id}-r{i}", suite_id=self.suite.suite_id,
                project_id=self.suite.project_id, run_index=i,
                protocol_fingerprint=proto or self.suite.protocol_fingerprint,
                clusters=cs, guards=guards_from_events(events),
                event_count=len(events), collected_at=E.now()))
        self.store.save_matrix(m)
        return m


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
        ) for x in m.stable_matches() if x.cluster.kind == "breach"]
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
    unassessed_guards: list[str] = Field(default_factory=list)
    reason: str = ""


class VerifiedRecord(BaseModel):
    record_id: str
    suite_id: str
    plan_id: str
    target_cluster_id: str
    verdict: RegressionVerdict
    created_at: str


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
    unassessed: list[str] = []
    # G20/AGENTS §1.5：记忆体窗口值不是净占用，不能用 1% 容差判劣化。
    # 没有 fresh GC（post_gc）时只能标“未评估盲区”，不能冒充清白或劣化。
    window_memory = {
        "Memory/TotalUsedBytes", "Memory/MonoUsedBytes",
        "Memory/TotalReservedBytes", "Memory/TotalAllocatedBytes",
    }
    if guards_before and guards_after:
        for k, before in guards_before.items():
            if k not in guards_after:
                continue
            if k in window_memory:
                unassessed.append(k)
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
                                 unassessed_guards=unassessed,
                                 reason="target cluster still present")
    if new:
        return RegressionVerdict(status="new_cluster",
                                 verification_status="unverified",
                                 target_cluster_id=target.cluster_id,
                                 new_clusters=new, guard_regressions=regs,
                                 unassessed_guards=unassessed,
                                 reason="new cluster(s) appeared")
    if regs:
        return RegressionVerdict(status="guard_regressed",
                                 verification_status="unverified",
                                 target_cluster_id=target.cluster_id,
                                 guard_regressions=regs,
                                 unassessed_guards=unassessed,
                                 reason="guard metric regressed")
    return RegressionVerdict(status="verified", verification_status="verified",
                             target_cluster_id=target.cluster_id,
                             unassessed_guards=unassessed,
                             reason="target gone, no new cluster, guards ok, protocol same")


class RegressionStore:
    def __init__(self, root: Optional[Path | str] = None):
        self.root = Path(root) if root else Path(
            os.environ.get("UPI_REGRESSION_DIR") or DEFAULT_DIR)
        self.suites = self.root / "suites"
        self.matrices = self.root / "matrices"
        self.verdicts = self.root / "verdicts"
        self.suites.mkdir(parents=True, exist_ok=True)
        self.matrices.mkdir(parents=True, exist_ok=True)
        self.verdicts.mkdir(parents=True, exist_ok=True)

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

    def list_suites(self) -> list[ReplaySuite]:
        out = []
        for p in sorted(self.suites.glob("*.json"), key=lambda x: x.stat().st_mtime,
                        reverse=True):
            try:
                out.append(ReplaySuite(**json.loads(p.read_text(encoding="utf-8"))))
            except Exception:  # noqa: BLE001
                continue
        return out

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

    def record_run(self, sid: str, event_log, *,
                   protocol: Optional[str] = None) -> Optional[RegressionRun]:
        """把当前探针事件记成一次 baseline run；重复 3 次即可出计划。
        不启动 replay、不推断协议：protocol 由 suite 或调用方给。"""
        suite = self.load_suite(sid)
        if suite is None:
            return None
        events, _bad = event_log.read_all(compat=True)
        cs = cluster_events(events, merge_gap=merge_gap_for(suite),
                            merge_sample=True)
        m = self.load_matrix(sid) or BaselineMatrix(suite_id=sid)
        idx = len(m.runs)
        r = RegressionRun(
            run_id=f"{sid}-r{idx}", suite_id=sid,
            project_id=suite.project_id, run_index=idx,
            protocol_fingerprint=(protocol or suite.protocol_fingerprint or "unspecified"),
            clusters=cs, guards=guards_from_events(events),
            event_count=len(events), collected_at=E.now())
        m.runs.append(r)
        self.save_matrix(m)
        return r

    def verify_last(self, sid: str) -> dict:
        """用前 3 次作基线、最后一次作 after，跑 verified 判定并落账本。
        不启动 replay；调用方必须先记录第 4 次（修复后）事件。"""
        m = self.load_matrix(sid)
        if m is None or len(m.runs) < 4:
            return {"ok": False, "error": "需要至少 4 次记录（3 基线 + 1 修复后）"}
        base_runs = m.runs[:3]
        base = BaselineMatrix(suite_id=sid, runs=base_runs,
                              min_runs=3, stable_rate=m.stable_rate)
        plan = RegressionPlan.from_matrix(base)
        if plan.status != "ready" or not plan.items:
            return {"ok": False, "error": f"计划不可用：{plan.status}"}
        item = plan.items[0]
        target = next((c for r in base_runs for c in r.clusters
                       if c.cluster_id == item.cluster_id), None)
        if target is None:
            return {"ok": False, "error": "目标 cluster 不在基线中"}
        after = m.runs[-1]
        guards_before = {}
        for r in base_runs:
            for k, v in (r.guards or {}).items():
                guards_before.setdefault(k, []).append(float(v))
        guards_before = {k: statistics.median(v) for k, v in guards_before.items() if v}
        v = verify_after(base, target, after.clusters,
                         after_protocol=after.protocol_fingerprint,
                         guards_before=guards_before,
                         guards_after=after.guards or {})
        rec = VerifiedRecord(
            record_id="vr-" + hashlib.sha256(
                f"{sid}|{plan.plan_id}|{after.run_id}".encode()).hexdigest()[:10],
            suite_id=sid, plan_id=plan.plan_id,
            target_cluster_id=target.cluster_id, verdict=v, created_at=E.now())
        self.save_verdict(rec)
        return {"ok": True, "record": rec.model_dump(),
                "verified": v.verification_status == "verified"}

    def save_verdict(self, rec: VerifiedRecord) -> Path:
        p = self.verdicts / f"{rec.record_id}.json"
        E.atomic_write_text(p, rec.model_dump_json(indent=2))
        return p

    def list_verdicts(self, sid: Optional[str] = None) -> list[VerifiedRecord]:
        out = []
        for p in sorted(self.verdicts.glob("vr-*.json"),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                rec = VerifiedRecord(**json.loads(p.read_text(encoding="utf-8")))
                if sid is None or rec.suite_id == sid:
                    out.append(rec)
            except Exception:  # noqa: BLE001
                continue
        return out

    def summary(self, sid: str) -> dict:
        suite = self.load_suite(sid)
        m = self.load_matrix(sid) or BaselineMatrix(suite_id=sid)
        plan = RegressionPlan.from_matrix(m)
        vs = self.list_verdicts(sid)
        return {
            "suite_id": sid,
            "name": suite.name if suite else "",
            "command": suite.command if suite else "",
            "protocol_fingerprint": m.protocol(),
            "runs": len(m.runs),
            "min_runs": m.min_runs,
            "stable_clusters": len(m.stable_matches()),
            "plan": plan.model_dump(),
            "last_verdict": (vs[0].verdict.model_dump() if vs else None),
            "verified_count": sum(1 for x in vs
                                  if x.verdict.verification_status == "verified"),
        }
