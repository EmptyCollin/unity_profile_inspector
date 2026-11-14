"""I21：LLM 计划重复采样后的多数选择。"""
from __future__ import annotations

import hashlib
import json


def plan_key(plan) -> str:
    if plan is None:
        return ""
    inv = getattr(plan, "invariant", {}) or {}
    inv_key = None
    if inv:
        inv_key = {
            "file": inv.get("file", ""),
            "method": inv.get("method", ""),
            "expect": int(inv.get("expect", 0)),
        }
    payload = {
        "metric": getattr(plan, "metric", ""),
        # 同文件/方法/expect 视为同一机制；pattern 粗细不算不同方案
        "invariant": inv_key,
        "diffs": [] if inv else getattr(plan, "diffs", []),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def choose_consensus(plans: list, min_votes: int = 2):
    """返回 (selected_plan, stable, counts)。票数不足返回 (None, False, counts)。"""
    counts: dict[str, int] = {}
    by_key: dict[str, object] = {}
    for p in plans:
        if p is None:
            continue
        k = plan_key(p)
        counts[k] = counts.get(k, 0) + 1
        by_key.setdefault(k, p)
    if not counts:
        return None, False, counts
    best = max(counts, key=lambda k: (counts[k], k))
    if counts[best] >= min_votes:
        return by_key[best], True, counts
    return None, False, counts
