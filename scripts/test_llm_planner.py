"""I10d/e：LLM 规划器的容错与回喂（离线，打桩客户端）。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _compose import compose  # noqa: E402
from upi.agents import llm_planner as LP  # noqa: E402
from upi.config.settings import load_config  # noqa: E402
from upi.core.models import Evidence, Issue  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

        class Comp:
            def __init__(self, outer):
                self.outer = outer

            def create(self, **kw):
                self.outer.calls += 1
                r = self.outer.replies.pop(0)
                if isinstance(r, Exception):
                    raise r
                msg = SimpleNamespace(content=r)
                usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                                       usage=usage)

        self.chat = SimpleNamespace(completions=Comp(self))


PROJECT = compose(["c02"])
cfg = load_config(ROOT / "llm_config.yaml")
issue = Issue(signature={"dim": "gc", "metric": "gc_alloc_per_frame",
                         "mechanism": "per_frame_allocation",
                         "threshold": 1000.0, "threshold_source": "test"},
              link="cpu", evidence=[Evidence(tool="probe", value=1.0,
                                             unit="count")],
              magnitude=1.0, deviation_ratio=2.0,
              explains_share_of_regression=1.0, state="confirmed")
hyp = {"root_cause": "hot_function:ChromaGame.UpdateHud",
       "code_anchors": ["Assets/DevSim/Runtime/ChromaGame.cs::ChromaGame.UpdateHudBody"]}

GOOD = json.dumps({"why": "cache text",
                   "edits": [{"file": "Assets/DevSim/Runtime/ChromaGame.cs",
                              "find": "private void UpdateHudBody()",
                              "replace": "private void UpdateHudBody()  // cached"}]})

# 1) 坏 JSON → 自动重试成功
cli = FakeClient(["not json", GOOD])
plan, meta = LP.plan_issue(cfg, issue, hyp, PROJECT, client=cli)
ok("坏 JSON 后重试成功", plan is not None and meta["rounds"] == 2, meta)
ok("plan 走 PB-LLM", plan is not None and plan.playbook_id == "PB-LLM")

# 2) find 不命中 → 回喂源码后成功
BAD = json.dumps({"why": "x", "edits": [{"file": "Assets/DevSim/Runtime/ChromaGame.cs",
                                          "find": "NOPE", "replace": "Y"}]})
cli2 = FakeClient([BAD, GOOD])
plan2, meta2 = LP.plan_issue(cfg, issue, hyp, PROJECT, client=cli2)
ok("find 不命中 → 回喂后成功", plan2 is not None and meta2["rounds"] == 2, meta2)

# 3) 明确不足 → 不硬编
cli3 = FakeClient([json.dumps({"why": "insufficient_evidence", "edits": []})])
plan3, meta3 = LP.plan_issue(cfg, issue, hyp, PROJECT, client=cli3)
ok("insufficient_evidence → 无方案", plan3 is None
   and meta3.get("error") == "insufficient_evidence", meta3)

# 3b) I15 伪优化：只改数字/循环上界 → 拒绝
NUM = json.dumps({"why": "reduce count",
                  "edits": [{"file": "Assets/DevSim/Runtime/ChromaGame.cs",
                             "find": "for (int i = 0; i < 32; i++)",
                             "replace": "for (int i = 0; i < 16; i++)"}]})
cli3b = FakeClient([NUM])
plan3b, meta3b = LP.plan_issue(cfg, issue, hyp, PROJECT, client=cli3b,
                               max_rounds=1)
ok("1000→10 类调参补丁被拒", plan3b is None
   and "pseudo_optimization" in str(meta3b.get("error")), meta3b)

# 4) 连续网络错误 → 有界失败，不抛
cli4 = FakeClient([RuntimeError("timeout"), RuntimeError("timeout"),
                   RuntimeError("timeout")])
plan4, meta4 = LP.plan_issue(cfg, issue, hyp, PROJECT, client=cli4)
ok("网络错误有界失败（不抛）", plan4 is None and cli4.calls == 3, meta4)
ok("失败原因带异常类型", "RuntimeError" in str(meta4.get("error")), meta4)

# 5) 提示词泄漏筛查 fail-closed
from upi.agents.factor_judge import JudgeError, _screen_prompt  # noqa: E402
try:
    _screen_prompt("this is test_project evidence")
    ok("泄漏词被拒发", False)
except JudgeError:
    ok("泄漏词被拒发", True)

print(f"\n==== LLM 规划容错测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
