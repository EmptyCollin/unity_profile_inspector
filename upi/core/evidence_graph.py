"""I8b：证据图。把帧、函数、源码、调用栈、分配点连起来。

不引入 networkx；节点/边是稳定 JSON 结构，方便落盘和测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from upi.core.evidence_v2 import EvidenceBundleV2, SourceSpan


@dataclass
class GraphNode:
    id: str
    kind: str
    label: str
    data: dict = field(default_factory=dict)


@dataclass
class GraphEdge:
    src: str
    dst: str
    kind: str


class EvidenceGraph:
    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = Path(project_root).resolve() if project_root else None
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []

    def add(self, node: GraphNode) -> None:
        self.nodes.setdefault(node.id, node)

    def link(self, src: str, dst: str, kind: str) -> None:
        if src in self.nodes and dst in self.nodes:
            self.edges.append(GraphEdge(src, dst, kind))

    def project_file(self, file: str) -> bool:
        if not file or self.project_root is None:
            return False
        try:
            p = Path(file)
            if not p.is_absolute():
                p = self.project_root / p
            return p.resolve().is_relative_to(self.project_root)
        except (OSError, ValueError):
            return False

    def span_project(self, span: Optional[SourceSpan]) -> bool:
        return bool(span and self.project_file(span.file))

    @classmethod
    def from_bundle(cls, bundle: EvidenceBundleV2, project_root: Optional[Path] = None) -> "EvidenceGraph":
        g = cls(project_root)
        for fi in bundle.frames:
            g.add(GraphNode(f"frame:{fi}", "frame", f"frame {fi}", {"frame": fi}))

        for idx, fn in enumerate(bundle.functions):
            fid = f"fn:{idx}:{fn.symbol}"
            g.add(GraphNode(fid, "function", fn.symbol, {
                "calls": fn.calls, "total_ms": fn.total_ms,
                "self_ms": fn.self_ms, "frame": fn.frame_idx,
                "source": fn.source.model_dump() if fn.source else None,
            }))
            g.link(f"frame:{fn.frame_idx}", fid, "contains")
            if fn.source:
                sid = f"src:{fn.source.file}:{fn.source.start_line}"
                g.add(GraphNode(sid, "source", fn.source.symbol or fn.symbol,
                                fn.source.model_dump()))
                g.link(fid, sid, "defined_at")

        for idx, cs in enumerate(bundle.callstacks):
            cid = f"stack:{idx}:{cs.frame_idx}"
            g.add(GraphNode(cid, "callstack", f"stack {idx}", {
                "frame": cs.frame_idx, "thread": cs.thread,
                "weights": cs.weight, "frames": len(cs.frames),
            }))
            g.link(f"frame:{cs.frame_idx}", cid, "contains")
            for j, fr in enumerate(cs.frames):
                fid = f"stackframe:{idx}:{j}"
                g.add(GraphNode(fid, "stackframe", fr.symbol, {
                    "symbol": fr.symbol, "native": fr.native,
                    "source": fr.source.model_dump() if fr.source else None,
                }))
                if j > 0:
                    g.link(f"stackframe:{idx}:{j-1}", fid, "calls")
                if fr.source:
                    sid = f"src:{fr.source.file}:{fr.source.start_line}"
                    g.add(GraphNode(sid, "source", fr.source.symbol or fr.symbol,
                                    fr.source.model_dump()))
                    g.link(fid, sid, "defined_at")

        for idx, site in enumerate(bundle.allocations):
            aid = f"alloc:{idx}:{site.frame_idx}"
            g.add(GraphNode(aid, "allocation", f"alloc {site.bytes}B", {
                "frame": site.frame_idx, "bytes": site.bytes, "count": site.count,
                "source": site.source.model_dump() if site.source else None,
            }))
            g.link(f"frame:{site.frame_idx}", aid, "contains")
            for j, fr in enumerate(site.stack):
                sid = f"src:{fr.source.file}:{fr.source.start_line}" if fr.source else ""
                if sid and sid in g.nodes:
                    g.link(aid, sid, "allocated_at")
                    break
        return g

    def project_functions(self) -> list[GraphNode]:
        return [n for n in self.nodes.values()
                if n.kind == "function"
                and self.span_project(SourceSpan(**n.data["source"]) if n.data.get("source") else None)]

    def to_dict(self) -> dict:
        return {"nodes": [vars(n) for n in self.nodes.values()],
                "edges": [vars(e) for e in self.edges],
                "project_root": str(self.project_root) if self.project_root else ""}
