"""I26-ext V1：从 replay/case manifest 解析目标 window。"""
from __future__ import annotations

import json
from pathlib import Path

WINDOW_ACTION = {
    "board": "resolve_board",
    "hud": "resolve",
    "shop": "open_shop",
}


def action_indices(replay_path: Path | str) -> dict[int, str]:
    try:
        spec = json.loads(Path(replay_path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for i, c in enumerate(spec.get("commands") or []):
        t = str(c.get("type") or "")
        if t not in ("wait", "stop"):
            out[i] = t
    return out


def index_for(window: str, indices: dict[int, str]) -> int | None:
    action = WINDOW_ACTION.get(window)
    if not action:
        return None
    hits = [i for i, t in indices.items() if t == action]
    return hits[-1] if hits else None


def case_windows(case_dir: Path | str) -> list[str]:
    p = Path(case_dir) / "manifest.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return list((d.get("expects") or {}).get("windows") or [])


def resolve(case_dir: Path | str, replay_path: Path | str) -> dict:
    idx = action_indices(replay_path)
    for w in case_windows(case_dir):
        i = index_for(w, idx)
        if i is not None:
            return {"window": w, "index": i, "status": "ok"}
    return {"window": "", "index": None, "status": "window_missing"}
