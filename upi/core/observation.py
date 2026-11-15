"""I26-ext E1：生产/考试共用的 site 证据卡。"""
from __future__ import annotations

from pathlib import Path

from upi.core.trace_analysis import build_trace

ROOT = Path(__file__).resolve().parents[2]


def site_card_for(capture_id: str, *, root: Path | str | None = None,
                  limit: int = 10) -> str:
    if not capture_id:
        return ""
    try:
        doc = build_trace(capture_id, root or (ROOT / "runs"))
    except Exception:  # noqa: BLE001
        return ""
    lines = []
    for s in (doc.get("site_metrics") or [])[:limit]:
        lines.append(
            f"{s.get('symbol')} | {s.get('file')}:{s.get('line')} "
            f"| self_ms={s.get('self_ms')} calls={s.get('calls')}")
    return "\n".join(lines)
