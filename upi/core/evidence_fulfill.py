"""I27：detect 侧补证执行器。

diagnose 只发 EvidenceRequest；本模块把 request 映射到已有文件或采集动作：
- window_series / control_window：读 action_windows.json，不碰 Unity；
- deep_window：调用 profile_windows.py 只采该窗口，返回 .data refs；
- paired_repeat：调用注入的 capture_fn（不在本模块里直接跑 replay）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from upi.core.evidence_request import EvidenceDelta, EvidenceRequest

ROOT = Path(__file__).resolve().parents[2]
METRIC_DEFAULT = "CPU Main Thread Frame Time"


def _load_windows(run_dir: Path) -> list[dict]:
    p = run_dir / "action_windows.json"
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return [w for w in rows if isinstance(w, dict) and w.get("window_id")]


def _series_rows(windows: list[dict], window_ids: list[str],
                 metric: str) -> dict:
    rows = {}
    for w in windows:
        wid = str(w.get("window_id") or "")
        if window_ids and wid not in window_ids:
            continue
        node = (w.get("counters") or {}).get(metric) or {}
        series = node.get("series") or []
        rows[wid] = {"median": node.get("median"),
                     "max": node.get("max"),
                     "n": node.get("n"),
                     "series": series}
    return rows


def fulfill(
    request: EvidenceRequest,
    *,
    run_dir: Path | str,
    project: Optional[Path | str] = None,
    replay: Optional[Path | str] = None,
    capture_fn: Optional[Callable[[EvidenceRequest], Optional[dict]]] = None,
    deep_fn: Optional[Callable[[EvidenceRequest], Optional[dict]]] = None,
) -> EvidenceDelta:
    """按 request.kind 分派；失败一律 available=False + reason，不抛给 diagnose。"""
    run = Path(run_dir)
    kind = request.kind
    if kind in ("window_series", "control_window"):
        windows = _load_windows(run)
        if not windows:
            return EvidenceDelta(available=False, kind=kind,
                                 request_id=request.request_id,
                                 reason="action_windows.json 不存在")
        metric = str(request.metric or METRIC_DEFAULT)
        rows = _series_rows(windows, list(request.window_ids or []), metric)
        if not rows:
            return EvidenceDelta(available=False, kind=kind,
                                 request_id=request.request_id,
                                 reason="请求窗口没有该 metric 的 series")
        return EvidenceDelta(
            available=True, kind=kind, request_id=request.request_id,
            refs=[str(run / "action_windows.json")], data_tier="A2",
            rows=rows, summary=f"{len(rows)} window(s) / {metric}")
    if kind == "deep_window":
        if deep_fn is not None:
            payload = deep_fn(request) or {}
        else:
            if not project or not replay:
                return EvidenceDelta(available=False, kind=kind,
                                     request_id=request.request_id,
                                     reason="deep_window 需要 project/replay")
            payload = _run_deep(request, project, replay)
        if not payload:
            return EvidenceDelta(available=False, kind=kind,
                                 request_id=request.request_id,
                                 reason="deep_window 未产出 .data")
        refs = [str(x) for x in (payload.get("refs") or [])]
        return EvidenceDelta(
            available=bool(refs), kind=kind, request_id=request.request_id,
            refs=refs, data_tier="A3", rows=payload.get("rows") or {},
            summary=str(payload.get("summary") or ""),
            reason="" if refs else "deep_window 无 refs")
    if kind == "paired_repeat":
        if capture_fn is None:
            return EvidenceDelta(available=False, kind=kind,
                                 request_id=request.request_id,
                                 reason="未注入 paired_repeat capture_fn")
        payload = capture_fn(request) or {}
        refs = [str(x) for x in (payload.get("refs") or [])]
        return EvidenceDelta(
            available=bool(refs), kind=kind, request_id=request.request_id,
            refs=refs, data_tier=str(payload.get("data_tier") or "A2"),
            rows=payload.get("rows") or {},
            summary=str(payload.get("summary") or ""),
            reason="" if refs else "paired_repeat 未产出 refs")
    return EvidenceDelta(available=False, kind=kind,
                         request_id=request.request_id,
                         reason=f"fulfill 不支持 kind={kind}")


def _run_deep(request: EvidenceRequest, project: Path | str,
              replay: Path | str) -> dict:
    windows = ",".join(str(x) for x in request.window_ids if x)
    if not windows:
        return {}
    run_id = "probe_" + (request.request_id or "evidence").replace("/", "_")
    cmd = [sys.executable, str(ROOT / "scripts" / "profile_windows.py"),
           "--project", str(project), "--replay", str(replay),
           "--run-id", run_id, "--windows", windows]
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900)
    except Exception as e:  # noqa: BLE001
        return {"summary": f"{type(e).__name__}: {e}"}
    meta = ROOT / "runs" / run_id / "profiles" / "profile_windows.json"
    refs = []
    rows = {}
    if meta.is_file():
        try:
            docs = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            docs = []
        for d in docs:
            if d.get("raw_status") == "ok" and d.get("path"):
                refs.append(str(d["path"]))
                wid = str(d.get("window_id") or "")
                # 把 .data 解包成 windows/<id>.json，并生成 candidate 排序摘要。
                dest = ROOT / "runs" / run_id / "windows" / f"{wid}.json"
                if wid and not dest.is_file():
                    try:
                        subprocess.run(
                            [sys.executable,
                             str(ROOT / "scripts" / "profile_window.py"),
                             "--run-id", run_id, "--window-id", wid,
                             "--project", str(project)],
                            cwd=str(ROOT), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=300)
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    from upi.core.deep_evidence import (candidates_from_summary,
                                                        load_window, summarize)
                    doc = load_window(ROOT / "runs" / run_id, wid)
                    if doc:
                        d["deep"] = summarize(doc)
                        d["deep"]["candidates"] = candidates_from_summary(
                            d["deep"], project, window_id=wid)
                except Exception:  # noqa: BLE001
                    pass
                rows[wid] = d
    return {"refs": refs, "rows": rows,
            "summary": f"rc={getattr(p, 'returncode', -1)} profiles={len(refs)}"}
