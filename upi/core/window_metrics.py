"""I24：按 action window 读取 capture counters。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def window_counters(cap_id: str, root: Path | str | None = None) -> dict[int, dict]:
    """返回 {command_index: counters_json}。

    两种采集布局：
    - 滚动采集：目录名形如 <cap_id>_a<i>，每个动作一份 counters.json；
    - 全趟浅采（--full-shallow）：<cap_id>/action_windows.json，按
      cmd-<i>-<type> 记录每窗 counters（含 series）。
    """
    out: dict[int, dict] = {}
    base = Path(root or ROOT) / "runs"
    if not base.is_dir():
        return out
    for d in base.glob(f"{cap_id}_a*"):
        if not d.is_dir():
            continue
        try:
            idx = int(d.name.rsplit("_a", 1)[1])
        except (ValueError, IndexError):
            continue
        f = d / "counters.json"
        if not f.is_file():
            continue
        try:
            out[idx] = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
    if out:
        return out
    aw = base / str(cap_id) / "action_windows.json"
    if not aw.is_file():
        return out
    try:
        wins = json.loads(aw.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    for w in wins if isinstance(wins, list) else []:
        wid = str((w or {}).get("window_id") or "")
        if not wid.startswith("cmd-"):
            continue
        try:
            idx = int(wid.split("-")[1])
        except (ValueError, IndexError):
            continue
        out[idx] = {"counters": (w or {}).get("counters") or {}}
    return out


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


def exact_index(window_id: str) -> int | None:
    """从 cmd-<i>-<action> 取精确 index；非法返回 None。"""
    parts = str(window_id or "").split("-", 2)
    if len(parts) != 3 or parts[0] != "cmd":
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def pick_control_index(action_idx: dict[int, str], target_indices,
                       *window_lists) -> int | None:
    """选一个未被 target 覆盖、且在全部 before/after 列表里都有数据的窗口。

    优先 wait；没有 wait 再退到任意非 target 索引。control 没有 series 时
    acceptance 的 environment 轴会变成 unassessed，所以先保证有数据。
    """
    target = set(int(x) for x in (target_indices or []))

    def _has(wl, i: int) -> bool:
        if isinstance(wl, dict):
            return wl.get(i) is not None
        return any(isinstance(w, dict) and w.get(i) for w in wl)

    for want_wait in (True, False):
        for i in sorted(action_idx):
            if i in target:
                continue
            if want_wait and action_idx[i] != "wait":
                continue
            if all(_has(wl, i) for wl in window_lists):
                return i
    return None


def index_for_window(indices: dict[int, str], window: str) -> int | None:
    hits = [i for i, t in indices.items() if t == window]
    return hits[-1] if hits else None
