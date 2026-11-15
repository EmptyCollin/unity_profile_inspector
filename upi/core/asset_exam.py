"""I26：单个资产 unit 的 attempt。"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from upi.core.asset_candidate import (apply_candidate, propose_for_unit,
                                      rollback_candidate)


def attempt_asset_unit(project: Path | str, unit, *,
                       measure_before: Callable[[], float] | None = None,
                       measure_after: Callable[[], float] | None = None,
                       eval_fn=None) -> dict:
    p = propose_for_unit(project, unit)
    if p is None:
        return {"status": "no_plan", "reason": "no_asset_proposal"}
    before = measure_before() if measure_before else None
    done = apply_candidate(project, p, eval_fn=eval_fn)
    after = measure_after() if measure_after else None
    if before is None or after is None:
        rollback_candidate(project, done)
        return {"status": "unmeasured", "proposal": p, "done": done,
                "before": before, "after": after, "rolled_back": True}
    if after >= before:
        rollback_candidate(project, done)
        return {"status": "no_effect", "proposal": p, "done": done,
                "before": before, "after": after, "rolled_back": True}
    return {"status": "improved", "proposal": p, "done": done,
            "before": before, "after": after, "rolled_back": False}
