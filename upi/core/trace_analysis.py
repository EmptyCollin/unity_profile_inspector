"""I26：从一次 capture 构建 trace / window 指标（离线）。"""
from __future__ import annotations

import json
from pathlib import Path

from upi.core.target_metrics import metric_series, stats

METRICS = ("frame_time", "gc_alloc", "memory", "drawcalls")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _window_stats(counters: dict) -> dict:
    out = {}
    for m in METRICS:
        vals = metric_series(counters, m)
        s = stats(vals)
        if s:
            out[m] = s
    return out


def _site_metrics(base: Path, windows: list[dict]) -> list[dict]:
    """聚合每个 window 的 evidence_v2.functions，产出 site 级 self/call。"""
    by: dict[tuple, dict] = {}
    for w in windows:
        idx = w["index"]
        d = base.parent / f"{base.name}_a{idx}" / "evidence" / "evidence_v2.json"
        if not d.is_file():
            continue
        doc = _read_json(d)
        for fn in doc.get("functions") or []:
            src = fn.get("source") or {}
            key = (str(fn.get("symbol") or ""), str(src.get("file") or ""),
                   int(src.get("start_line") or 0))
            slot = by.setdefault(key, {
                "symbol": key[0], "file": key[1], "line": key[2],
                "calls": 0, "self_ms": 0.0, "total_ms": 0.0,
                "frames": 0, "windows": set(),
            })
            slot["calls"] += int(fn.get("calls") or 0)
            slot["self_ms"] += float(fn.get("self_ms") or 0.0)
            slot["total_ms"] += float(fn.get("total_ms") or 0.0)
            slot["frames"] += 1
            slot["windows"].add(idx)
    out = []
    for slot in by.values():
        slot["windows"] = sorted(slot["windows"])
        calls = max(1, slot["calls"])
        slot["self_ms_per_call"] = round(slot["self_ms"] / calls, 6)
        out.append(slot)
    return sorted(out, key=lambda x: x["self_ms"], reverse=True)


def build_trace(cap_id: str, root: Path | str = "runs") -> dict:
    base = Path(root) / cap_id
    timeline = _read_json(base / "pass1_timeline.json")
    if not isinstance(timeline, list):
        timeline = []
    windows = []
    for d in sorted(base.parent.glob(f"{cap_id}_a*")):
        name = d.name
        try:
            idx = int(name.rsplit("_a", 1)[1])
        except (ValueError, IndexError):
            continue
        counters = _read_json(d / "counters.json")
        windows.append({
            "index": idx,
            "action": (timeline[idx].get("type")
                       if idx < len(timeline) else ""),
            "frame": (timeline[idx].get("frame")
                      if idx < len(timeline) else None),
            "metrics": _window_stats(counters),
            "counters_present": bool(counters.get("counters")),
        })
    candidates = _read_json(base / "merged_candidates.json")
    roots = sorted({str(c.get("root_cause", "")).split(":", 1)[-1]
                    for c in (candidates or [])})
    return {
        "run_id": cap_id,
        "timeline": timeline,
        "windows": windows,
        "roots": roots,
        "site_metrics": _site_metrics(base, windows),
        "n_windows": len(windows),
    }


def write_trace(cap_id: str, root: Path | str = "runs") -> Path:
    doc = build_trace(cap_id, root)
    p = Path(root) / cap_id / "trace.json"
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p
