"""I27：diagnose ↔ detect 补证回边。

diagnose 不直接采集；它产出 EvidenceRequest，由 orchestrator 调 fulfill，
再把 EvidenceDelta 喂回 diagnose。本模块只负责循环、去重、预算、trace。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Callable

from upi.core.evidence_request import EvidenceDelta, EvidenceRequest
from upi.core.finding import Finding

DiagnoseFn = Callable[[Finding, list[EvidenceDelta]], dict]
FulfillFn = Callable[[EvidenceRequest], EvidenceDelta]

_DEEP_KINDS = {"deep_window"}


def request_hash(req: EvidenceRequest) -> str:
    if req.request_hash:
        return req.request_hash
    payload = {
        "finding_id": req.finding_id,
        "window_ids": sorted(req.window_ids),
        "kind": req.kind,
        "missing": sorted(req.missing),
        "reason": req.reason,
        "expected": req.expected,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return "req-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _delta_key(delta: EvidenceDelta) -> str:
    payload = {
        "available": delta.available,
        "kind": delta.kind,
        "refs": sorted(delta.refs),
        "summary": delta.summary,
        "text": delta.text[:200],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return "ev-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class LoopResult:
    status: str
    hypotheses: list[dict] = field(default_factory=list)
    deltas: list[EvidenceDelta] = field(default_factory=list)
    rounds: int = 0
    requests: list[EvidenceRequest] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    reason: str = ""


def run_evidence_loop(
    finding: Finding,
    *,
    diagnose: DiagnoseFn,
    fulfill: FulfillFn,
    max_rounds: int = 2,
    max_deep: int = 1,
    max_wallclock_s: float = 0.0,
) -> LoopResult:
    """max_rounds 是 diagnose 调用次数（通常 2：初诊 + 补证后复诊）。"""
    started = time.time()
    deltas: list[EvidenceDelta] = []
    trace: list[dict] = []
    requests: list[EvidenceRequest] = []
    seen_req: set[str] = set()
    seen_ev: set[str] = set()
    deep_used = 0
    max_rounds = max(1, int(max_rounds))

    for round_no in range(1, max_rounds + 1):
        if max_wallclock_s and time.time() - started > max_wallclock_s:
            return LoopResult("budget", deltas=deltas, rounds=round_no - 1,
                              requests=requests, trace=trace,
                              reason="wallclock")
        out = diagnose(finding, deltas) or {}
        hypotheses = list(out.get("hypotheses") or [])
        reqs = list(out.get("requests") or [])
        trace.append({"round": round_no, "kind": "diagnose",
                      "hypotheses": len(hypotheses),
                      "requests": len(reqs)})
        if hypotheses:
            return LoopResult("hypothesized", hypotheses=hypotheses,
                              deltas=deltas, rounds=round_no,
                              requests=requests, trace=trace)
        if not reqs:
            return LoopResult("insufficient_evidence", deltas=deltas,
                              rounds=round_no, requests=requests, trace=trace,
                              reason="diagnose 既无 hypothesis 也无 request")
        new_reqs: list[EvidenceRequest] = []
        skipped: list[str] = []
        for req in reqs:
            if not req.finding_id:
                req.finding_id = finding.finding_id
            if not req.window_ids:
                req.window_ids = [finding.window.window_id]
            rh = request_hash(req)
            if not req.request_hash:
                req.request_hash = rh
            if rh in seen_req:
                skipped.append("duplicate")
                continue
            if req.kind in _DEEP_KINDS:
                if deep_used >= max_deep:
                    skipped.append("deep_budget")
                    continue
                deep_used += 1
            seen_req.add(rh)
            new_reqs.append(req)
        trace.append({"round": round_no, "kind": "requests",
                      "new": len(new_reqs), "skipped": skipped})
        if not new_reqs:
            status = "duplicate_request" if "duplicate" in skipped else "budget"
            return LoopResult(status, deltas=deltas, rounds=round_no,
                              requests=requests, trace=trace,
                              reason="no new request")
        requests.extend(new_reqs)
        new_evidence = 0
        for req in new_reqs:
            delta = fulfill(req)
            if not isinstance(delta, EvidenceDelta):
                delta = EvidenceDelta(available=False, kind=req.kind,
                                      request_id=req.request_id,
                                      reason="fulfill 返回类型非法")
            if not delta.request_id:
                delta.request_id = req.request_id or req.request_hash
            deltas.append(delta)
            ev_key = _delta_key(delta)
            if delta.available and ev_key not in seen_ev:
                seen_ev.add(ev_key)
                new_evidence += 1
            trace.append({"round": round_no, "kind": "delta",
                          "request": req.request_hash,
                          "available": delta.available,
                          "refs": len(delta.refs)})
        if not new_evidence:
            return LoopResult("insufficient_evidence", deltas=deltas,
                              rounds=round_no, requests=requests, trace=trace,
                              reason="补证不可用或没有新增证据")
    return LoopResult("max_rounds", deltas=deltas, rounds=max_rounds,
                      requests=requests, trace=trace, reason="max_rounds")
