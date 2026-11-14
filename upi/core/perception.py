"""I23：截图感知门禁。无 Pillow 时退化为文件哈希 + 人工复核。"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

try:
    from PIL import Image, ImageChops
except Exception:  # noqa: BLE001 — 可选依赖
    Image = ImageChops = None


def capture(project: Path | str, out: Path | str,
            view: str = "game") -> Path:
    root = Path(project).resolve()
    path = Path(out)
    if not path.is_absolute():
        path = root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(
        ["unity", "command", "screenshot", "--view", view,
         "--output-path", str(path), "--project-path", str(root),
         "--format", "json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-400:] or p.stdout[-400:])
    if not path.is_file():
        raise RuntimeError(f"截图未落盘: {path}")
    return path


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(a: Path | str, b: Path | str) -> dict:
    pa, pb = Path(a), Path(b)
    if Image is not None:
        ia = Image.open(pa).convert("RGB")
        ib = Image.open(pb).convert("RGB")
        if ia.size != ib.size:
            ib = ib.resize(ia.size)
        diff = ImageChops.difference(ia, ib)
        hist = diff.histogram()
        total = sum(hist) or 1
        weighted = sum(i % 256 * v for i, v in enumerate(hist))
        mean_abs = weighted / total / 255.0
        return {"mean_abs": round(mean_abs, 6),
                "changed": mean_abs > 0.01,
                "requires_human": mean_abs > 0.01,
                "size": list(ia.size)}
    same = _hash(pa) == _hash(pb)
    return {"mean_abs": None, "changed": not same,
            "requires_human": not same, "size": None,
            "mode": "hash_fallback"}


def gate(a: Path | str, b: Path | str, threshold: float = 0.01) -> dict:
    r = compare(a, b)
    if r.get("mean_abs") is not None:
        r["passed"] = r["mean_abs"] <= threshold
    else:
        r["passed"] = not r["changed"]
    return r
