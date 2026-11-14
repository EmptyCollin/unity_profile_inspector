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


def _isolate_compile(patches: dict[str, PatchRef], apply: ApplyFn,
                     compile_fn: CompileFn, rollback: RollbackFn) -> list[str]:
    """二分定位可编译子集；返回可保留的 unit。假定补丁间无强依赖。"""
    items = list(patches.items())
    for _, p in items:
        rollback(p)
    good: list[str] = []
    candidates = items
    while candidates:
        half = candidates[:max(1, len(candidates) // 2)]
        for uid, p in half:
            apply(p)
        if compile_fn():
            good.extend(uid for uid, _ in half)
            candidates = candidates[len(half):]
        else:
            for uid, p in half:
                rollback(p)
            candidates = half
            if len(half) == 1:
                break
    for uid in good:
        apply(patches[uid])
    return good


def run_batch(units: list, *, plan: PlanFn, apply: ApplyFn,
              compile_fn: CompileFn, capture: CaptureFn,
              verify: VerifyFn, rollback: RollbackFn,
              round_no: int = 1) -> BatchResult:
    res = BatchResult(round=round_no)
    patches: dict[str, PatchRef] = {}
    planned_units: list = []
    # 先全部 plan（快照必须都指向同一基线），再统一 apply
    for u in units:
        p = plan(u)
        if p is None:
            res.failed[getattr(u, "unit_id", str(u))] = "no_plan"
            continue
        patches[getattr(u, "unit_id", str(u))] = p
        planned_units.append(u)
    for u in planned_units:
        uid = getattr(u, "unit_id", str(u))
        if not apply(patches[uid]):
            res.failed[uid] = "apply_failed"
            continue
        res.applied.append(uid)
    if not res.applied:
        return res
    units = [u for u in planned_units
             if getattr(u, "unit_id", str(u)) in res.applied]
    res.compile_ok = bool(compile_fn())
    if not res.compile_ok:
        good = _isolate_compile(patches, apply, compile_fn, rollback)
        bad = [uid for uid in patches if uid not in good]
        for uid in bad:
            res.failed[uid] = "compile_failed"
            res.rolled_back.append(uid)
        if not good:
            return res
        res.compile_ok = True
        res.applied = list(good)
        units = [u for u in units if getattr(u, "unit_id", "") in good]
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
