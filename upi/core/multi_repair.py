"""I16：多病例修复执行器（回调式）。

单病例失败只回滚该病例；不影响其它病例。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from upi.core.repair_queue import CaseState, RepairQueue

PlanFn = Callable[[CaseState], bool]
ApplyFn = Callable[[CaseState], bool]
VerifyFn = Callable[[CaseState], str]
RollbackFn = Callable[[CaseState], bool]


@dataclass
class MultiRepairResult:
    queue: RepairQueue
    events: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return self.queue.summary()


def run_queue(queue: RepairQueue, *, plan: PlanFn, apply: ApplyFn,
              verify: VerifyFn, rollback: RollbackFn,
              max_cases: Optional[int] = None) -> MultiRepairResult:
    """按队列逐病例执行；一个病例失败只回滚它自己。"""
    res = MultiRepairResult(queue)
    limit = max_cases or len(queue.cases)
    attempts = 0
    max_attempts = limit * 4
    while attempts < max_attempts:
        case = queue.next_pending()
        if case is None:
            break
        attempts += 1
        case.attempts += 1
        if not plan(case):
            queue.mark(case.case_id, "failed", reason="no_plan")
            res.events.append({"case": case.case_id, "status": "no_plan"})
            continue
        if not apply(case):
            queue.mark(case.case_id, "failed", reason="apply_failed")
            res.events.append({"case": case.case_id, "status": "apply_failed"})
            continue
        status = str(verify(case) or "failed")
        if status == "verified":
            queue.mark(case.case_id, "verified", verdict="verified")
            res.events.append({"case": case.case_id, "status": "verified"})
            continue
        # 未通过：只回滚这个病例
        rollback(case)
        if case.attempts < case.max_attempts:
            queue.mark(case.case_id, "diagnosed",
                       reason=f"{status};retry",
                       verdict=status)
            res.events.append({"case": case.case_id, "status": status,
                               "retry": case.attempts + 1})
            continue
        queue.mark(case.case_id,
                   "partial_fix" if status == "partial_fix" else "failed",
                   reason=status, verdict=status)
        res.events.append({"case": case.case_id, "status": status})
    return res
