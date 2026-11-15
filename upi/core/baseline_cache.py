"""I26：baseline 缓存（按协议指纹）。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "runs" / "_cache" / "baselines"


def key(protocol_fingerprint: str) -> str:
    return hashlib.sha256(protocol_fingerprint.encode("utf-8")).hexdigest()[:16]


def get(protocol_fingerprint: str, root: Path | str | None = None) -> dict | None:
    p = Path(root or DEFAULT) / f"{key(protocol_fingerprint)}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def put(protocol_fingerprint: str, data: dict,
        root: Path | str | None = None) -> Path:
    p = Path(root or DEFAULT) / f"{key(protocol_fingerprint)}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"protocol_fingerprint": protocol_fingerprint,
                             "data": data}, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p
