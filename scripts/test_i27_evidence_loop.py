"""I27 evidence_loop：diagnose → request → fulfill → diagnose 回边。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.evidence_loop import request_hash, run_evidence_loop  # noqa: E402
from upi.core.evidence_request import EvidenceDelta, EvidenceRequest  # noqa: E402
from upi.core.finding import Finding, WindowRef  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


W = WindowRef.from_id("cmd-10-open_shop")
F = Finding(finding_id="F1", window=W, metric="frame_time",
            evidence_refs=["counters.json"])


def req(reason="need alloc", kind="deep_window", missing=None):
    return EvidenceRequest(
        kind=kind, reason=reason, finding_id="F1",
        window_ids=["cmd-10-open_shop"],
        missing=missing or ["alloc_site"],
        expected="alloc site in window")


def delta(r, available=True, refs=(".data",), summary="alloc"):
    return EvidenceDelta(available=available, kind=r.kind, refs=list(refs),
                         summary=summary, data_tier="A2",
                         request_id=r.request_hash or request_hash(r))


# 1) request -> delta -> hypothesis
calls = {"n": 0}


def diag1(finding, deltas):
    calls["n"] += 1
    if calls["n"] == 1:
        return {"requests": [req()]}
    return {"hypotheses": [{"root_cause": "alloc_site:X"}]}


res = run_evidence_loop(F, diagnose=diag1, fulfill=lambda r: delta(r))
ok("回边成功", res.status == "hypothesized", res.status)
ok("两轮诊断", res.rounds == 2, res.rounds)
ok("拿到 delta", len(res.deltas) == 1 and res.deltas[0].available, res.deltas)
ok("trace 记录请求/补证", len(res.trace) >= 4, res.trace)

# 2) duplicate request stops
calls2 = {"n": 0}


def diag2(finding, deltas):
    calls2["n"] += 1
    return {"requests": [req(reason="same")]}


res2 = run_evidence_loop(F, diagnose=diag2, fulfill=lambda r: delta(r))
ok("重复请求停止", res2.status == "duplicate_request", res2.status)
ok("重复只补一次", len(res2.deltas) == 1, len(res2.deltas))

# 3) unavailable delta stops
res3 = run_evidence_loop(F, diagnose=lambda f, d: {"requests": [req(reason="x")]},
                         fulfill=lambda r: delta(r, available=False))
ok("补证不可用停止", res3.status == "insufficient_evidence", res3.status)
ok("不可用无 hypothesis", not res3.hypotheses, res3.hypotheses)

# 4) max_rounds
seq = {"n": 0}


def diag4(finding, deltas):
    seq["n"] += 1
    return {"requests": [req(reason=f"round{seq['n']}", kind="window_series")]}


res4 = run_evidence_loop(F, diagnose=diag4,
                         fulfill=lambda r: delta(r, summary=r.reason),
                         max_rounds=2)
ok("max_rounds 停止", res4.status == "max_rounds", res4.status)
ok("max_rounds 轮数", res4.rounds == 2, res4.rounds)

# 5) deep budget
deep = {"n": 0}


def diag5(finding, deltas):
    deep["n"] += 1
    if deep["n"] == 1:
        return {"requests": [req(reason="deep-a", missing=["alloc_site"]),
                             req(reason="deep-b", missing=["callstack"])]}
    return {"hypotheses": [{"root_cause": "x"}]}


res5 = run_evidence_loop(F, diagnose=diag5, fulfill=lambda r: delta(r),
                         max_deep=1)
ok("deep 预算下仍可结案", res5.status == "hypothesized", res5.status)
ok("只补一个 deep", len(res5.deltas) == 1, len(res5.deltas))
ok("trace 标 deep_budget",
   any("deep_budget" in (t.get("skipped") or []) for t in res5.trace),
   res5.trace)


def diag_err(finding, deltas):
    raise RuntimeError("llm empty")


res6 = run_evidence_loop(F, diagnose=diag_err, fulfill=lambda r: delta(r))
ok("diagnose 异常不冒泡", res6.status == "diagnose_error", res6.status)
ok("异常留 trace", any(t.get("kind") == "diagnose_error"
                    for t in res6.trace), res6.trace)

print(f"\n==== I27 evidence_loop: {len(PASS)} ok, {len(FAIL)} failed")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
