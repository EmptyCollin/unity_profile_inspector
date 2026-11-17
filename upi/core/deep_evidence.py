"""I27：从窗口 .data 解包结果生成病证摘要，用于 candidate 加权/排序。

ProfileWindow 输出（windows/<id>.json）是 samples 列表：
  {frame, name, path, bytes, time_ns}
本模块不产 root cause，只做：
- allocation path 聚合（bytes/count）
- 游戏函数 time 聚合（ms）
- 按 deep 摘要给已有 candidate 排序/加权
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


def load_window(run_dir: Path | str, window_id: str) -> dict:
    p = Path(run_dir) / "windows" / f"{window_id}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def summarize(doc: dict, *, top_n: int = 10) -> dict:
    samples = [x for x in (doc.get("samples") or []) if isinstance(x, dict)]
    alloc: dict[str, dict] = {}
    hot: dict[str, dict] = {}
    for x in samples:
        name = str(x.get("name") or "")
        path = str(x.get("path") or "")
        if "GC.Alloc" in name:
            segments = [s for s in path.split(">") if s]
            if len(segments) >= 2 and segments[-1].startswith("GC.Alloc"):
                symbol = segments[-2]
            elif segments:
                symbol = segments[-1]
            else:
                symbol = name
            key = (segments[-2] + ">GC.Alloc") if len(segments) >= 2 else symbol
            row = alloc.setdefault(key, {"path": path, "symbol": symbol,
                                         "bytes": 0, "count": 0})
            row["bytes"] += int(x.get("bytes") or 0)
            row["count"] += 1
        elif ("ChromaGame" in name or "BoardModel" in name
              or "DevSim" in name):
            row = hot.setdefault(name, {"symbol": name, "time_ns": 0,
                                        "calls": 0})
            row["time_ns"] += int(x.get("time_ns") or 0)
            row["calls"] += 1
    allocs = sorted(alloc.values(), key=lambda r: r["bytes"], reverse=True)
    for r in allocs:
        r["kbytes"] = round(r["bytes"] / 1024.0, 1)
    hots = sorted(hot.values(), key=lambda r: r["time_ns"], reverse=True)
    for r in hots:
        r["ms"] = round(r["time_ns"] / 1e6, 3)
    return {"first": doc.get("first"), "last": doc.get("last"),
            "allocs": allocs[:top_n], "hot": hots[:top_n],
            "samples": len(samples),
            "alloc_bytes": sum(r["bytes"] for r in allocs)}


def _short(root: str) -> str:
    return str(root or "").split(":")[-1].split(".")[-1]


def merge_summaries(items: Iterable[dict]) -> dict:
    """合并多个窗口的 deep summary（同 symbol 累加）。"""
    alloc: dict[str, dict] = {}
    hot: dict[str, dict] = {}
    count = 0
    for s in items or []:
        count += 1
        for r in (s.get("allocs") or []):
            key = str(r.get("symbol") or r.get("path") or "")
            row = alloc.setdefault(key, {"symbol": key, "path": r.get("path", ""),
                                         "bytes": 0, "count": 0})
            row["bytes"] += int(r.get("bytes") or 0)
            row["count"] += int(r.get("count") or 0)
        for r in (s.get("hot") or []):
            key = str(r.get("symbol") or "")
            row = hot.setdefault(key, {"symbol": key, "time_ns": 0, "calls": 0})
            row["time_ns"] += int(r.get("time_ns") or 0)
            row["calls"] += int(r.get("calls") or 0)
    allocs = sorted(alloc.values(), key=lambda r: r["bytes"], reverse=True)
    hots = sorted(hot.values(), key=lambda r: r["time_ns"], reverse=True)
    for r in allocs:
        r["kbytes"] = round(r["bytes"] / 1024.0, 1)
    for r in hots:
        r["ms"] = round(r["time_ns"] / 1e6, 3)
    return {"allocs": allocs, "hot": hots,
            "alloc_bytes": sum(r["bytes"] for r in allocs),
            "windows": count}


def rank_candidates(cands: Iterable, summary: dict) -> list:
    """按 deep hot/alloc 给已有 candidate 排序；没有 deep 命中保持原序。"""
    hot = {str(r.get("symbol") or "").split(".")[-1]: r
           for r in (summary.get("hot") or [])}
    alloc = {str(r.get("symbol") or "").split(".")[-1]: r
             for r in (summary.get("allocs") or [])}
    scored = []
    for i, c in enumerate(cands):
        root = str(getattr(c, "root_cause", "") or "")
        short = _short(root)
        h = hot.get(short)
        a = alloc.get(short)
        boost = 0.0
        if h:
            boost += float(h.get("time_ns") or 0) / 1e6
        if a:
            boost += min(50.0, float(a.get("bytes") or 0) / (1024.0 * 1024.0))
        scored.append((bool(h or a), boost, i, c))
    scored.sort(key=lambda x: (x[0], x[1], -x[2]), reverse=True)
    return [x[3] for x in scored]


def candidates_from_summary(summary: dict, project_root, *, window_id: str,
                            limit: int = 8) -> list[dict]:
    """把 deep summary 的 symbol 映射到源码锚点，生成新的 CandidateV2 dict。

    没有源码锚点的 symbol 不生成候选（禁止无位置结论）。
    """
    from upi.core.candidates_v2 import CandidateV2
    from upi.core.evidence_v2 import SourceSpan
    from upi.core.static_index import StaticIndex

    try:
        idx = StaticIndex(project_root)
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    seen: set[str] = set()

    def _span(sym: str) -> tuple[str, str, int, int] | None:
        hits = idx.find_symbol(sym.split(".")[-1])
        if not hits:
            return None
        h = hits[0]
        return (str(h["file"]), str(h["method"]), int(h["line"]),
                int(h.get("end_line") or h["line"]))

    for r in (summary.get("hot") or [])[:limit]:
        sym = str(r.get("symbol") or "")
        hit = _span(sym)
        if not hit:
            continue
        file, method, line, end = hit
        root = f"hot_function:{sym}"
        if root in seen:
            continue
        seen.add(root)
        ms = float(r.get("ms") or 0.0)
        c = CandidateV2(
            id=f"deep-hot-{len(out)}", root_cause=root,
            mechanism="hot_path", kind="function",
            code_anchors=[f"{file}:{line}"],
            sources=[SourceSpan(file=file, symbol=method,
                                start_line=line, end_line=end)],
            metric_value=ms, metric_unit="ms", self_ms=ms, total_ms=ms,
            score=ms, evidence_n=int(r.get("calls") or 1),
            window_id=window_id)
        out.append(c.model_dump())

    for r in (summary.get("allocs") or [])[:limit]:
        sym = str(r.get("symbol") or "")
        hit = _span(sym)
        if not hit:
            continue
        file, method, line, end = hit
        root = f"allocation_site:{sym}:{line}"
        if root in seen:
            continue
        seen.add(root)
        b = int(r.get("bytes") or 0)
        c = CandidateV2(
            id=f"deep-alloc-{len(out)}", root_cause=root,
            mechanism="per_frame_allocation", kind="allocation",
            code_anchors=[f"{file}:{line}"],
            sources=[SourceSpan(file=file, symbol=method,
                                start_line=line, end_line=end)],
            metric_value=float(b), metric_unit="bytes",
            score=round(b / 1024.0, 1), evidence_n=int(r.get("count") or 1),
            window_id=window_id)
        out.append(c.model_dump())
    return out
