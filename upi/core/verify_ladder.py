"""验证阶梯（I11e）：单次 replay 只作门禁，3 基线后才允许 verified。"""
from __future__ import annotations

from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict

from upi.core.mode_caps import caps


class LadderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str
    status: str            # unverified | observed | verified | needs_recheck
    reason: str = ""
    replay_ok: Optional[bool] = None
    target_gone: Optional[bool] = None
    new_clusters: list[str] = []


def evaluate(mode: str, *,
             replay_once: Optional[Callable[[], dict]] = None,
             baselines: Optional[Callable[[], bool]] = None) -> LadderResult:
    """按模式走验证阶梯；常驻永不 verified。"""
    c = caps(mode)
    if not c.allow_replay:
        return LadderResult(mode="resident", status="unverified",
                            reason="常驻无可复现 replay：只能观察，不判 verified")
    if replay_once is None:
        return LadderResult(mode=mode, status="unverified",
                            reason="未提供 replay 采集器")
    try:
        once = replay_once() or {}
    except Exception as e:  # noqa: BLE001
        return LadderResult(mode=mode, status="unverified",
                            reason=f"replay 执行失败：{type(e).__name__}: {e}")
    if once.get("protocol_ok") is False:
        return LadderResult(mode=mode, status="unverified",
                            replay_ok=False,
                            reason="协议指纹不一致，禁止对比")
    if once.get("not_reproduced"):
        return LadderResult(mode=mode, status="not_reproduced",
                            replay_ok=True,
                            reason="同 replay 未复现目标：记 not_reproduced，不硬报")
    target_gone = bool(once.get("target_gone"))
    new = [str(x) for x in (once.get("new_clusters") or [])]
    guards_ok = bool(once.get("guards_ok", True))
    if not target_gone or new or not guards_ok:
        return LadderResult(mode=mode, status="needs_recheck", replay_ok=True,
                            target_gone=target_gone, new_clusters=new,
                            reason=("目标仍在/出现新问题/守护劣化：回检查或重规划"))
    # 单次 replay 干净：才值得花 3 次基线
    if baselines is None:
        return LadderResult(mode=mode, status="observed", replay_ok=True,
                            target_gone=True,
                            reason="单次 replay 干净；未跑基线，不能判 verified")
    try:
        ok = bool(baselines())
    except Exception as e:  # noqa: BLE001
        return LadderResult(mode=mode, status="observed", replay_ok=True,
                            target_gone=True,
                            reason=f"基线失败：{type(e).__name__}: {e}")
    if ok:
        from upi.core.mode_caps import assert_verdict_allowed
        assert_verdict_allowed(mode, "verified")
        return LadderResult(mode=mode, status="verified", replay_ok=True,
                            target_gone=True, reason="同 replay + 目标消失 + 无新问题 + 守护不劣化")
    return LadderResult(mode=mode, status="observed", replay_ok=True,
                        target_gone=True, reason="基线未确认，降为 observed")
