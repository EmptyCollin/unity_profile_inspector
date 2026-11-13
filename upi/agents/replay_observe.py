"""证据模式的 replay 前后观测（不是 verified）。

一次 before/一次 after 只能回答“目标还在不在、分数变了多少”；
G21 的 verified 还要 3 次基线 + 目标消失 + 无新 cluster + 守护不劣化，
所以本模块只产 `observed`，由调用方记 `verification_status=unverified`。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from upi.core.findings import normalize_file, normalize_method


def _cand_anchor(c: dict) -> tuple[str, str, float]:
    src = (c.get("sources") or [{}])[0]
    return (normalize_file(str(src.get("file") or "")),
            normalize_method(str(src.get("symbol") or "")),
            float(c.get("score") or 0.0))


def _anchor_target(anchor: str) -> tuple[str, str]:
    if "::" in anchor:
        f, m = anchor.split("::", 1)
        return normalize_file(f), normalize_method(m)
    f, _, line = anchor.rpartition(":")
    return normalize_file(f), ("line:" + line if line.isdigit() else "")


def observe(cands: list[dict], anchors: list[str]) -> Optional[dict]:
    """在捕获到的候选里找目标锚点；返回 {file, method, score, mechanism}。"""
    targets = [_anchor_target(a) for a in anchors if a]
    for c in cands:
        f, m, score = _cand_anchor(c)
        for tf, tm in targets:
            if f != tf:
                continue
            if not tm or tm == m or tm.startswith("line:"):
                return {"file": f, "method": m, "score": score,
                        "mechanism": str(c.get("mechanism") or "")}
    return None


def judge(before: Optional[dict], after: Optional[dict]) -> tuple[str, str]:
    """返回 (observed, 说明)。observed ∈ absent|reduced|present|unknown。"""
    if before is None and after is None:
        return "unknown", "前后都没有捕获到目标锚点"
    if before is None:
        return "present", "改前未捕获、改后出现（无法归因）"
    if after is None:
        return "absent", f"改后目标不再出现（before score={before['score']:.3f}）"
    if before["score"] > 0 and after["score"] < before["score"] * 0.9:
        return "reduced", (f"目标仍在但分数下降 "
                           f"{before['score']:.3f} → {after['score']:.3f}")
    return "present", (f"目标仍在且分数未明显下降 "
                       f"{before['score']:.3f} → {after['score']:.3f}")


def capture(project: Path, replay_path: str, tag: str, limit: int = 8) -> list[dict]:
    """跑一次 replay + 滚动证据捕获；返回候选（dict 列表）。

    复用能力链的采集实现（scripts/capability_run），不另建一套采样。
    """
    import scripts.capability_run as CR
    return CR.run_and_capture(Path(project), tag, limit, Path(replay_path))
