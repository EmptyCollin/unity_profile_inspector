"""I8b：从证据图生成候选根因。

硬边界：
- 候选来自函数/分配/调用栈证据，不来自 case_id、legacy flag 或正则命中；
- 指纹只作为 prior，未命中不拒绝；
- 每个候选必须能落到源码 span 或明确标 unknown。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from pydantic import BaseModel, Field

from upi.core.evidence_graph import EvidenceGraph
from upi.core.evidence_v2 import EvidenceBundleV2, SourceSpan
from upi.core.intervals import bootstrap_ci


class CandidateV2(BaseModel):
    id: str
    root_cause: str
    mechanism: str
    kind: str
    code_anchors: list[str] = Field(default_factory=list)
    sources: list[SourceSpan] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    score: float = 0.0
    score_interval_low: float = 0.0
    score_interval_high: float = 0.0
    interval_basis: str = ""
    evidence_n: int = 0
    prior: float = 0.0
    source_kind: str = "evidence"


def _span_of(node_data: dict) -> Optional[SourceSpan]:
    s = node_data.get("source")
    return SourceSpan(**s) if s else None


def generate_candidates(bundle: EvidenceBundleV2,
                        graph: Optional[EvidenceGraph] = None,
                        project_root=None, limit: int = 12,
                        fingerprint_store=None) -> list[CandidateV2]:
    g = graph or EvidenceGraph.from_bundle(bundle, project_root)
    out: list[CandidateV2] = []
    seen: set[tuple] = set()

    # 1) 分配点：同源码位置聚合 bytes×count；这是 GC 类最直接的证据。
    alloc_by_span: dict[tuple, dict] = defaultdict(lambda: {"bytes": 0, "count": 0,
                                                            "symbol": "", "evidence": [],
                                                            "vals": []})
    for i, site in enumerate(bundle.allocations):
        span = site.source
        if span is None and site.stack:
            span = site.stack[0].source
        if span is None or not g.span_project(span):
            continue
        sym = next((f.symbol for f in site.stack
                    if f.source and g.span_project(f.source)), span.symbol)
        key = (span.file, span.start_line)
        alloc_by_span[key]["bytes"] += site.bytes
        alloc_by_span[key]["count"] += site.count or 1
        alloc_by_span[key]["symbol"] = sym or alloc_by_span[key]["symbol"]
        alloc_by_span[key]["evidence"].append(f"alloc:{i}")
        alloc_by_span[key]["vals"].append(float(site.bytes * max(1, site.count or 1)))

    for (file, line), a in alloc_by_span.items():
        key = ("alloc", file, line)
        if key in seen:
            continue
        seen.add(key)
        span = SourceSpan(file=file, start_line=line, end_line=line,
                          symbol=a["symbol"])
        ci = bootstrap_ci(a["vals"], statistic=sum)
        out.append(CandidateV2(
            id=f"cand-alloc-{len(out)}",
            root_cause=f"allocation_site:{a['symbol'] or file}:{line}",
            mechanism="per_frame_allocation",
            kind="allocation",
            code_anchors=[f"{file}:{line}"],
            sources=[span],
            evidence_ids=a["evidence"],
            score=float(sum(a["vals"])),
            score_interval_low=ci["low"], score_interval_high=ci["high"],
            interval_basis=ci["basis"], evidence_n=ci["n"],
        ))

    # 2) 热点函数：按 total_ms 聚合，只保留工程源码内的函数。
    fn_by_sym: dict[tuple, dict] = defaultdict(lambda: {"total": 0.0, "calls": 0,
                                                         "span": None, "ids": [],
                                                         "vals": []})
    for i, fn in enumerate(bundle.functions):
        if fn.source is None or not g.span_project(fn.source):
            continue
        key = (fn.symbol, fn.source.file, fn.source.start_line)
        e = fn_by_sym[key]
        e["total"] += fn.total_ms
        e["calls"] += fn.calls
        e["span"] = fn.source
        e["ids"].append(f"fn:{i}:{fn.symbol}")
        e["vals"].append(float(fn.total_ms) * 1000.0 + float(fn.calls))
    for (sym, file, line), e in fn_by_sym.items():
        key = ("fn", file, line)
        if key in seen:
            continue
        seen.add(key)
        ci = bootstrap_ci(e["vals"], statistic=sum)
        out.append(CandidateV2(
            id=f"cand-fn-{len(out)}",
            root_cause=f"hot_function:{sym}",
            mechanism="hot_path",
            kind="function",
            code_anchors=[f"{file}:{line}"],
            sources=[e["span"]],
            evidence_ids=e["ids"],
            score=float(e["total"]) * 1000.0 + float(e["calls"]),
            score_interval_low=ci["low"], score_interval_high=ci["high"],
            interval_basis=ci["basis"], evidence_n=ci["n"],
        ))

    # 3) 调用栈：按栈内第一个工程帧聚合，补足“函数统计看不到”的路径。
    stack_by_sym: dict[tuple, dict] = defaultdict(lambda: {"weight": 0.0,
                                                            "span": None, "ids": [],
                                                            "vals": []})
    for i, cs in enumerate(bundle.callstacks):
        for fr in cs.frames:
            if fr.source is None or not g.span_project(fr.source):
                continue
            key = (fr.symbol, fr.source.file, fr.source.start_line)
            e = stack_by_sym[key]
            e["weight"] += cs.weight
            e["span"] = fr.source
            e["ids"].append(f"stack:{i}")
            e["vals"].append(float(cs.weight) * 100.0)
            break
    for (sym, file, line), e in stack_by_sym.items():
        key = ("stack", file, line)
        if key in seen:
            continue
        seen.add(key)
        ci = bootstrap_ci(e["vals"], statistic=sum)
        out.append(CandidateV2(
            id=f"cand-stack-{len(out)}",
            root_cause=f"callstack_hot:{sym}",
            mechanism="callstack_hot_path",
            kind="callstack",
            code_anchors=[f"{file}:{line}"],
            sources=[e["span"]],
            evidence_ids=e["ids"],
            score=float(e["weight"]) * 100.0,
            score_interval_low=ci["low"], score_interval_high=ci["high"],
            interval_basis=ci["basis"], evidence_n=ci["n"],
        ))

    if fingerprint_store is not None:
        from upi.core.fingerprint import FingerprintFeatures, FingerprintStore
        store: FingerprintStore = fingerprint_store
        metric_of = {
            "per_frame_allocation": ("gc_alloc_per_frame", "step", "sample", "A2"),
            "hot_path": ("frame_time_ms", "spike", "breach", "A4"),
            "callstack_hot_path": ("frame_time_ms", "spike", "breach", "A4"),
        }
        for c in out:
            spec = metric_of.get(c.mechanism)
            if not spec:
                continue
            metric, shape, kind, tier = spec
            f = FingerprintFeatures(metric=metric, shape=shape, kind=kind,
                                    data_tier=tier, rel_bucket=-1)
            rec, score, _second = store.lookup(f)
            if rec is not None and score > 0:
                # 指纹只是 prior：只加分，不做准入门槛；没命中仍是 novel 候选。
                c.prior = round(float(score), 4)
                c.source_kind = "evidence+fingerprint"

    out.sort(key=lambda c: (c.score + c.score * c.prior, c.prior), reverse=True)
    return out[:limit]
