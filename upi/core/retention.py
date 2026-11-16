"""Profile .data 保留与清理。"""
from __future__ import annotations

import time
from pathlib import Path


def cleanup_profiles(root: Path | str, days: int = 3,
                     now: float | None = None) -> dict:
    base = Path(root)
    cutoff = (now if now is not None else time.time()) - max(1, days) * 86400
    removed, bytes_freed = 0, 0
    for p in base.glob("*/profiles/**/*.data"):
        try:
            st = p.stat()
            if st.st_mtime >= cutoff:
                continue
            bytes_freed += st.st_size
            p.unlink()
            removed += 1
        except OSError:
            continue
    return {"removed": removed, "bytes_freed": bytes_freed,
            "days": days, "cutoff": cutoff}
