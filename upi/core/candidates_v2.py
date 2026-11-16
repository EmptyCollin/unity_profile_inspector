"""I8b：从证据图生成候选根因。

硬边界：
- 候选来自函数/分配/调用栈证据，不来自 case_id、legacy flag 或正则命中；
- 指纹只作为 prior，未命中不拒绝；
- 每个候选必须能落到源码 span 或明确标 unknown。
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
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
    metric_value: float = 0.0
    metric_unit: str = ""
    self_ms: float = 0.0
    total_ms: float = 0.0
    self_share: float = -1.0
    peak_frame: int = 0
    score_interval_low: float = 0.0
    score_interval_high: float = 0.0
    interval_basis: str = ""
    evidence_n: int = 0
    prior: float = 0.0
    source_kind: str = "evidence"


def _short_symbol(symbol: str) -> str:
    """把原生/程序集符号归一成 `Type.Method`，避免同一函数重复占候选。"""
    s = (symbol or "").strip()
    if "!" in s:
        s = s.split("!")[-1]
    s = s.replace("::", ".")
    s = s.replace("()", "").strip()
    parts = s.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else s


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
        sym = _short_symbol(sym)
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
            metric_value=max(a["vals"] or [0.0]),
            metric_unit="bytes",
            score_interval_low=ci["low"], score_interval_high=ci["high"],
            interval_basis=ci["basis"], evidence_n=ci["n"],
        ))

    # 2) 热点函数：按 total_ms 聚合，只保留工程源码内的函数。
    fn_by_sym: dict[tuple, dict] = defaultdict(lambda: {"total": 0.0, "calls": 0,
                                                         "self": 0.0,
                                                         "has_self": False,
                                                         "peak": 0.0, "peak_frame": 0,
                                                         "span": None, "ids": [],
                                                         "vals": [], "peaks": []})
    for i, fn in enumerate(bundle.functions):
        if fn.source is None or not g.span_project(fn.source):
            continue
        key = (_short_symbol(fn.symbol), fn.source.file, fn.source.start_line)
        e = fn_by_sym[key]
        e["total"] += fn.total_ms
        e["self"] += fn.self_ms
        if getattr(fn, "time_semantics", "") == "exclusive":
            e["has_self"] = True
        e["calls"] += fn.calls
        e["span"] = fn.source
        e["ids"].append(f"fn:{i}:{fn.symbol}")
        e["vals"].append(float(fn.total_ms) * 1000.0 + float(fn.calls))
        e["peaks"].append(float(fn.total_ms))
        if float(fn.total_ms) > e["peak"]:
            e["peak"] = float(fn.total_ms)
            e["peak_frame"] = int(fn.frame_idx)
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
            metric_value=max(e["peaks"] or [0.0]),
            metric_unit="ms",
            self_ms=float(e["self"]),
            total_ms=float(e["total"]),
            self_share=((float(e["self"]) / float(e["total"]))
                        if e["total"] and e["has_self"] else -1.0),
            peak_frame=int(e.get("peak_frame") or 0),
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
            key = (_short_symbol(fr.symbol), fr.source.file, fr.source.start_line)
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
            metric_value=float(e["weight"]),
            metric_unit="ms",
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

    if bundle.anomalies:
        from upi.core.anomaly_candidates import candidates_from_anomalies
        have = {c.root_cause for c in out}
        for c in candidates_from_anomalies(bundle, project_root, limit=limit):
            if c.root_cause not in have:
                out.append(c)
                have.add(c.root_cause)

    out.sort(key=lambda c: (c.score + c.score * c.prior, c.prior), reverse=True)
    # 资产证据（度量，不是代码模式）：Resources 下超大文件，放最后避免主导函数排序
    try:
        if project_root:
            res = Path(project_root) / "Assets" / "Resources"
            if res.is_dir():
                for p in sorted(res.rglob("*")):
                    if not p.is_file() or p.suffix.lower() not in (
                            ".txt", ".bytes", ".json", ".asset"):
                        continue
                    size = p.stat().st_size
                    if size <= 128 * 1024:
                        continue
                    rel = p.relative_to(project_root).as_posix()
                    out.append(CandidateV2(
                        id=f"cand-asset-{len(out)}",
                        root_cause=f"oversized_resource_asset:{rel}",
                        mechanism="oversized_resource_asset", kind="asset",
                        code_anchors=[rel],
                        sources=[SourceSpan(file=rel, start_line=1, end_line=1)],
                        evidence_ids=[], score=float(size) / 1024.0,
                        metric_value=float(size), metric_unit="bytes",
                        score_interval_low=float(size) / 1024.0,
                        score_interval_high=float(size) / 1024.0,
                        interval_basis="file_size", evidence_n=1))
    except Exception:  # noqa: BLE001 — 资产扫描失败不影响代码候选
        pass
    edges = (bundle.meta or {}).get("edges") or []
    return drop_containers(out, edges, project_root=project_root)[:limit]


def _short_name(s: str) -> str:
    s = (s or "").split("::")[-1].split("[")[0]
    s = s.replace("()", "").replace(" [Invoke]", "").strip()
    return s.split(".")[-1] if "." in s else s


def _has_direct_work(c: CandidateV2, project_root=None) -> bool:
    """self≈0 但函数体有 new/loop 等直接工作 → 不是纯 facade。"""
    if not c.sources or project_root is None:
        return True
    s = c.sources[0]
    try:
        p = Path(project_root) / s.file
        lines = p.read_text(encoding="utf-8").splitlines()
        lo = max(1, s.start_line) - 1
        hi = min(len(lines), max(s.end_line, s.start_line + 60))
        body = "\n".join(lines[lo:hi])
        import re
        return bool(re.search(r"\b(new|for|while|foreach)\b", body))
    except Exception:  # noqa: BLE001
        return True


def drop_containers(cands: list[CandidateV2], edges: list[dict] | None = None,
                    eps: float = 0.001, project_root=None) -> list[CandidateV2]:
    """self/total≈0 的函数是纯容器/入口，不产出 finding；子函数保留。

    有候选父节点、或函数体有直接工作的容器保留（例如 c06 的 ResolveAllSync）；
    只删没有候选父节点且纯委托的入口 facade。
    """
    names = {_short_name(c.root_cause.split(":")[-1]) for c in cands}
    parents: dict[str, set[str]] = {}
    for e in edges or []:
        p = _short_name(str(e.get("parent") or ""))
        ch = _short_name(str(e.get("child") or ""))
        if p and ch and p in names and ch in names:
            parents.setdefault(ch, set()).add(p)
    out = []
    for c in cands:
        n = _short_name(c.root_cause.split(":")[-1])
        container = (c.kind == "function" and c.total_ms > 0
                     and 0.0 <= c.self_share <= eps)
        if container and not parents.get(n) and not _has_direct_work(c, project_root):
            continue
        out.append(c)
    return out
