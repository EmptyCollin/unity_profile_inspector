"""I21：FlowRun 的决策策略；LLM 只做 retry/stop，不碰执行与验证。"""
from __future__ import annotations

import json


def rule_decide(failed: set[str], round_no: int, max_rounds: int) -> str:
    if not failed:
        return "done"
    if round_no >= max_rounds:
        return "max_rounds"
    return "retry"


def llm_decide(cfg, failed: set[str], round_no: int, max_rounds: int,
               *, client=None) -> str:
    """LLM 只能在 retry/stop 之间选；硬规则优先。"""
    if not failed:
        return "done"
    if round_no >= max_rounds:
        return "max_rounds"
    try:
        from upi.config.connectivity import make_client
        eff = cfg.effective_for("review")
        cli = client or make_client(cfg.providers[eff["provider"]])
        prompt = (
            "你是性能修复流程的调度器。只决定是否继续重试失败单元。\n"
            "返回 JSON: {\"decision\":\"retry|stop\",\"why\":\"...\"}\n"
            f"round={round_no}, max_rounds={max_rounds}, "
            f"failed={sorted(failed)}\n"
            "若有明确可重试路径选 retry；证据不足或重复失败选 stop。"
        )
        resp = cli.chat.completions.create(
            model=eff["model"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=200,
            response_format={"type": "json_object"})
        text = resp.choices[0].message.content or "{}"
        d = json.loads(text)
        return "retry" if d.get("decision") == "retry" else "stop"
    except Exception:  # noqa: BLE001 — 决策失败退回硬规则
        return "retry"
