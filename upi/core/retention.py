"""Profile .data 保留与容量清理。"""
from __future__ import annotations

import time
from pathlib import Path


def cleanup_profiles(root: Path | str, days: int = 3,
                     max_bytes: int | None = None,
                     now: float | None = None) -> dict:
    """按时间和/或容量清理 .data；容量从最旧文件开始删。"""
    base = Path(root)
    stamp = now if now is not None else time.time()
    cutoff = stamp - max(0, days) * 86400
    removed, bytes_freed, total = 0, 0, 0
    files: list[tuple[float, int, Path]] = []
    for p in base.glob("*/profiles/**/*.data"):
        try:
            st = p.stat()
        except OSError:
            continue
        total += st.st_size
        if days > 0 and st.st_mtime < cutoff:
            bytes_freed += st.st_size
            try:
                p.unlink()
                removed += 1
                total -= st.st_size
            except OSError:
                pass
            continue
        files.append((st.st_mtime, st.st_size, p))
    if max_bytes is not None and max_bytes > 0 and total > max_bytes:
        for _, size, p in sorted(files):
            if total <= max_bytes:
                break
            try:
                p.unlink()
                removed += 1
                bytes_freed += size
                total -= size
            except OSError:
                continue
    return {"removed": removed, "bytes_freed": bytes_freed,
            "bytes_remaining": total, "max_bytes": max_bytes,
            "days": days, "cutoff": cutoff, "at": stamp}
