"""I19：单轮批量修复。

一次 apply 多个补丁 → 编译一次 → 一次 capture → 验全部 target。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, TypedDict

from langgraph.graph import END, StateGraph

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
    partial: list[str] = field(default_factory=list)
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
              round_no: int = 1, tree=None,
              on_failure=None) -> BatchResult:
    """LangGraph 版：plan → apply → compile → capture → verify。"""
    class S(TypedDict, total=False):
        units: list
        patches: dict
        planned: list
        applied: list
        failed: dict
        rolled: list
        compile_ok: bool
        capture_ok: bool
        obs: object
        result: BatchResult

    res = BatchResult(round=round_no)
    res.failed = {}

    def plan_node(st: S) -> S:
        patches: dict = {}
        planned: list = []
        for u in st.get("units") or []:
            p = plan(u)
            if p is None:
                res.failed[getattr(u, "unit_id", str(u))] = "no_plan"
                continue
            patches[getattr(u, "unit_id", str(u))] = p
            planned.append(u)
        return {"patches": patches, "planned": planned, "result": res}

    def apply_node(st: S) -> S:
        applied: list = []
        for u in st.get("planned") or []:
            uid = getattr(u, "unit_id", str(u))
            if not apply(st["patches"][uid]):
                res.failed[uid] = "apply_failed"
                continue
            applied.append(uid)
        res.applied = applied
        return {"applied": applied, "result": res}

    def compile_node(st: S) -> S:
        if not st.get("applied"):
            return {"compile_ok": False, "result": res}
        ok = bool(compile_fn())
        if not ok:
            good = _isolate_compile(st["patches"], apply, compile_fn, rollback)
            bad = [uid for uid in st["patches"] if uid not in good]
            for uid in bad:
                res.failed[uid] = "compile_failed"
                res.rolled_back.append(uid)
            ok = bool(good)
            res.applied = list(good)
        return {"compile_ok": ok, "result": res}

    def capture_node(st: S) -> S:
        try:
            obs = capture()
        except Exception as e:  # noqa: BLE001
            for uid in list(res.applied):
                rollback(st["patches"][uid])
            res.capture_ok = False
            res.failed["__capture__"] = f"{type(e).__name__}: {e}"
            return {"capture_ok": False, "result": res}
        return {"obs": obs, "capture_ok": True, "result": res}

    def verify_node(st: S) -> S:
        good = set(st.get("applied") or [])
        for u in st.get("planned") or []:
            uid = getattr(u, "unit_id", str(u))
            if uid not in good or uid not in st["patches"]:
                continue
            verdict = str(verify(u, st.get("obs")) or "failed")
            if tree is not None and len(units) == 1:
                action = tree.record(verdict, patch_id=uid)
                if action == "keep":
                    if verdict == "verified":
                        res.verified.append(uid)
                    else:
                        res.partial.append(uid)
                    continue
                if action == "finding_failed" and on_failure is not None:
                    on_failure(uid, verdict, tree)
                res.failed[uid] = verdict
                if action not in ("infra_retry",) and rollback(st["patches"][uid]):
                    res.rolled_back.append(uid)
                continue
            if verdict == "verified":
                res.verified.append(uid)
            else:
                res.failed[uid] = verdict
                if rollback(st["patches"][uid]):
                    res.rolled_back.append(uid)
        return {"result": res}

    g = StateGraph(S)
    g.add_node("plan", plan_node)
    g.add_node("apply", apply_node)
    g.add_node("compile", compile_node)
    g.add_node("capture", capture_node)
    g.add_node("verify", verify_node)
    g.set_entry_point("plan")
    g.add_conditional_edges(
        "plan", lambda st: "apply" if st.get("patches") else END,
        {"apply": "apply", END: END})
    g.add_conditional_edges(
        "apply", lambda st: "compile" if st.get("applied") else END,
        {"compile": "compile", END: END})
    g.add_conditional_edges(
        "compile", lambda st: "capture" if st.get("compile_ok") else END,
        {"capture": "capture", END: END})
    g.add_conditional_edges(
        "capture", lambda st: "verify" if st.get("capture_ok") else END,
        {"verify": "verify", END: END})
    g.add_edge("verify", END)
    out = g.compile().invoke({"units": list(units), "result": res})
    return out.get("result", res)
