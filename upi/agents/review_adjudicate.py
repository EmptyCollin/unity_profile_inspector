"""Review Agent 的模型化隔离裁定（#37 走 b：真接模型，M5/§1.6 落地）。

分工纪律（§1.6 + I5 + I1 的代码落点，与 factor_judge 同构）：
- **规则优先**：§4 硬门禁（G1 不显著、G2 劣化、G3 感知失败）的初始裁定由
  确定性规则给出（在 nodes.review 里算，不在本模块）；本模块只在规则给出
  "继续迭代 / 撤回重评"这类**未定**裁定时，让模型判断"本质是不是感知类改动、
  该不该立即转人工"。
- 模型**只能收紧**裁定（把 continue/rollback 改成 escalate），**绝不允许放宽**
  （把规则的 escalate 改成继续）——放宽等于让 LLM 推翻硬门禁，违反 I5。
  实现上：模型返回比规则更松的裁定 → 丢弃模型值、保留规则值、meta 记原因。
- 信息隔离（§1.6，DESIGN §2.3.5 明确为**弱隔离**：同模型 + 独立 prompt +
  独立温度 + 输入白名单裁剪）：
    · 输入只取白名单字段（Verdict 的 target/guards/conflicts/perception/
      realized_over_expected/compile_ok/status + Hypothesis 的 root_cause +
      EditPlan 的 side_effect/diff 摘要）；
    · **绝不**接收 Fix 的自我辩护、自评置信度、自然语言解释（这些根本不进
      prompt 构造函数，不是"传了但让模型忽略"）；
    · prompt_profile=review_isolated / temperature 从 llm_config 的 review 段
      经 effective_for("review") 真读真用（此前是孤儿配置，本模块是其唯一消费者）。
- 连通性门禁（I13）：knowledge/llm_connectivity.json 不存在或未过 → 抛错，
  由调用方降级回规则裁定，绝不静默发起推理烧积分。

本模块只做"隔离输入 → 模型 → 收紧后裁定 + 结构化 reason"，不碰文件、不回滚、
不做修复（回滚与配额仍由 nodes.review 依规则执行）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from upi.config.connectivity import RESULT_PATH_DEFAULT, make_client
from upi.config.settings import LlmConfig

# 三态裁定的"松→紧"全序：模型只能往更紧方向移动，不能更松。
_DECISION_RANK = {"continue_with_direction": 0, "rollback_and_replan": 1,
                  "escalate_human": 2}
DECISIONS = tuple(_DECISION_RANK)


class ReviewAdjudicateError(RuntimeError):
    """隔离裁定失败（门禁未过 / 非 JSON / 非法结构）。调用方须降级回规则。"""


def _connectivity_ok() -> tuple[bool, str]:
    """I13：连通性只在第一次使用时跑；本链只读结果，不重复触发推理。"""
    try:
        rec = json.loads(Path(RESULT_PATH_DEFAULT).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, "连通性测试从未跑过（verification_gate 未通过，禁止真实推理）"
    except json.JSONDecodeError:
        return False, "连通性结果文件损坏"
    if not rec.get("all_passed"):
        return False, f"连通性测试未通过（provider={rec.get('provider')}）"
    return True, ""


def _whitelist_input(*, status: str, target: dict, guards: list[dict],
                     conflicts: list[dict], perception: dict,
                     compile_ok: bool, realized: float,
                     root_cause: str, side_effect: str,
                     diff_files: list[str]) -> dict:
    """§1.6 输入白名单的唯一构造点：不在这里出现的字段，模型就看不到。

    刻意排除（写成缺席而非显式忽略，杜绝"传进去又让模型别看"的假隔离）：
    Fix 的自我辩护文本、Fix 自评置信度、EditPlan 的自然语言说明、hypothesis
    的 confidence 数值（那是公式产物、非审查输入）。
    """
    regressed = [g.get("metric") for g in guards if g.get("regressed")]
    return {
        "verdict_status": status,
        "target": {k: target.get(k) for k in
                   ("metric", "delta", "sigma", "significant") if k in target},
        "guard_regressed_metrics": regressed,
        "conflict_rule_ids": [c.get("rule_id") for c in conflicts],
        "perception": {k: perception.get(k) for k in
                       ("render_diff_score", "anim_event_diff",
                        "replay_divergence", "passed") if k in perception},
        "compile_ok": compile_ok,
        "realized_over_expected": realized,
        "hypothesis_root_cause": root_cause,   # 机制描述，非置信度
        "edit_side_effect": side_effect,
        "edit_files": diff_files,
    }


_SYSTEM = (
    "你是性能修复的独立审查裁定器。你只看到客观验证数据（改前后指标、守护劣化、"
    "矛盾规则、感知门禁、触碰的文件），看不到修复方的自我辩护或自评置信度。"
    "规则已给出初始裁定，你只判断一件事：这次未解决的修复是否本质上是感知类改动"
    "（画面/手感，G3/§1.6），从而应'立即转人工、不烧审查配额'。"
    "硬约束：① 你只能把裁定改得比初始更严格（升级为 escalate_human），不得放松；"
    "② 没有客观证据支持'感知类'时，保持初始裁定，输出 keep；"
    "③ 只输出 JSON，禁止编造数据。"
)

_PROMPT = """初始裁定（规则给出）：{rule_decision}
客观输入（白名单，逐项可核查）：
{input_json}

请判定：该问题是否本质为感知类改动、应否立即 escalate_human？
输出 JSON：
{{"decision": "{rule_decision}" | "escalate_human",
  "is_perception_in_essence": true | false,
  "evidence_fields": ["你据以判断的输入字段名，须来自上面白名单"],
  "reason": "一句话，只引用输入里存在的字段"}}
"""


def _plain(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str, sort_keys=True)


def adjudicate(cfg: LlmConfig, *, rule_decision: str, inputs: dict,
               client=None) -> tuple[str, dict]:
    """规则初始裁定 + 白名单输入 → (最终裁定, 留档 meta)。

    最终裁定只可能是 rule_decision 或 escalate_human（收紧方向）；若模型给出
    比规则更松的值，丢弃并保留规则值（meta 记 model_overruled）。
    """
    ok, why = _connectivity_ok()
    if not ok:
        raise ReviewAdjudicateError(why)
    if rule_decision not in DECISIONS:
        raise ReviewAdjudicateError(f"非法初始裁定 {rule_decision!r}")

    eff = cfg.effective_for("review")          # 真读 review 段（此前是孤儿配置）
    provider, model = eff.get("provider"), eff.get("model")
    if not provider or provider not in cfg.providers or not model:
        raise ReviewAdjudicateError("review 段未解析出 provider/model（配置缺 defaults 回填？）")

    prompt = _PROMPT.format(rule_decision=rule_decision, input_json=_plain(inputs))
    cli = client or make_client(cfg.providers[provider])
    kwargs = dict(
        model=model,
        messages=[{"role": "system", "content": _SYSTEM},
                  {"role": "user", "content": prompt}],
        temperature=eff.get("temperature", 0.0),   # 隔离手段之一，真用
        max_tokens=eff.get("max_output_tokens", 1024),
    )
    if eff.get("json_mode"):
        kwargs["response_format"] = {"type": "json_object"}
    resp = cli.chat.completions.create(**kwargs)
    raw = (resp.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReviewAdjudicateError(f"模型输出非 JSON（{exc.msg}）：{raw[:120]}") from exc
    if not isinstance(data, dict):
        raise ReviewAdjudicateError("模型输出 JSON 但非对象")

    want = str(data.get("decision", "")).strip()
    final = rule_decision
    overruled = None
    if want == rule_decision:
        pass                                     # keep
    elif _DECISION_RANK.get(want, -1) > _DECISION_RANK[rule_decision]:
        final = want                             # 收紧，采纳
    else:
        overruled = want                         # 放松，丢弃（模型想推翻硬门禁）

    usage = getattr(resp, "usage", None)
    meta = {
        "model": model, "provider": provider,
        "temperature": kwargs["temperature"],
        "prompt_profile": eff.get("prompt_profile"),   # review_isolated 真消费留痕
        "rule_decision": rule_decision, "final_decision": final,
        "model_wanted": want, "model_overruled": overruled,
        "is_perception_in_essence": bool(data.get("is_perception_in_essence")),
        "evidence_fields": [str(f) for f in (data.get("evidence_fields") or [])],
        "reason": str(data.get("reason", ""))[:200],
        "token_in": getattr(usage, "prompt_tokens", None),
        "token_out": getattr(usage, "completion_tokens", None),
        "isolation": "weak(same-model): independent prompt + temperature + input whitelist",
        "prompt_sha256_8": hashlib.sha256(prompt.encode()).hexdigest()[:8],
    }
    return final, meta


__all__ = ["adjudicate", "ReviewAdjudicateError", "_whitelist_input", "DECISIONS"]
