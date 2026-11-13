"""I19：单轮批量修复。

一次 apply 多个补丁 → 编译一次 → 一次 capture → 验全部 target。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from upi.core.mechanism_batch import PatchRef

PlanFn = Callable[[object], Optional[PatchRef]]
ApplyFn = Callable[[PatchRef], bool]
CompileFn = Callable[[], bool]
CaptureFn = Callable[[], object]
VerifyFn = Callable[[object, object], str]
RollbackFn = Callable[[PatchRef], bool]


@dataclass
class BatchResult:
    round: int = 1
    applied: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    rolled_back: list[str] = field(default_factory=list)
    compile_ok: bool = True
    capture_ok: bool = True


def run_batch(units: list, *, plan: PlanFn, apply: ApplyFn,
              compile_fn: CompileFn, capture: CaptureFn,
              verify: VerifyFn, rollback: RollbackFn,
              round_no: int = 1) -> BatchResult:
    res = BatchResult(round=round_no)
    patches: dict[str, PatchRef] = {}
    for u in units:
        p = plan(u)
        if p is None:
            res.failed[getattr(u, "unit_id", str(u))] = "no_plan"
            continue
        patches[getattr(u, "unit_id", str(u))] = p
        if not apply(p):
            res.failed[getattr(u, "unit_id", str(u))] = "apply_failed"
            continue
        res.applied.append(getattr(u, "unit_id", str(u)))
    if res.failed:
        # 有补丁没应用：先回滚已应用的，保证工程干净
        for uid in list(res.applied):
            if rollback(patches[uid]):
                res.rolled_back.append(uid)
        return res
    res.compile_ok = bool(compile_fn())
    if not res.compile_ok:
        for uid in list(res.applied):
            if rollback(patches[uid]):
                res.rolled_back.append(uid)
        res.failed["__compile__"] = "compile_failed"
        return res
    try:
        obs = capture()
    except Exception as e:  # noqa: BLE001
        for uid in list(res.applied):
            rollback(patches[uid])
        res.capture_ok = False
        res.failed["__capture__"] = f"{type(e).__name__}: {e}"
        return res
    for u in units:
        uid = getattr(u, "unit_id", str(u))
        if uid not in patches:
            continue
        verdict = str(verify(u, obs) or "failed")
        if verdict == "verified":
            res.verified.append(uid)
        else:
            res.failed[uid] = verdict
            if rollback(patches[uid]):
                res.rolled_back.append(uid)
    return res
