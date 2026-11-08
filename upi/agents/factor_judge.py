"""LLM 因子判定链（M2，DESIGN §5 / AGENTS §1.3）。

分工纪律（§1.3 硬规则的代码落点）：
- LLM 只判定六因子的"是否成立"（可核查的序数判断：yes/likely/unclear/no）
  并给出证据指针；
- C_attr 由 attribution.c_attr() 公式算出，LLM 不打分（I1：LLM 只解释证据，
  不生成证据，也不生成数值结论）；
- LLM 给出的每个因子判定必须挂 evidence_refs（引用给定证据编号 e0/e1/...
  或 anchor:<文件:行>）；无指针的判定 value 记 0、指针记空——交给
  attribution.veto_reasons 的统一防线（"全判定无指针 → 封顶 0.5"）；
- 连通性门禁：knowledge/llm_connectivity.json 不存在或未过 → 拒发推理请求
  （§2.3.3 首次使用引导 / I13 不重复烧成本）。

本模块只做"判定 → FactorJudgment[] → 公式 → 排序"，
不做修复决策、不选 Playbook、不写文件。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from upi.config.connectivity import RESULT_PATH_DEFAULT, make_client
from upi.config.settings import LlmConfig
from upi.core.attribution import (FactorJudgment, build_hypothesis, rank,
                                  route as route_decision)
from upi.core.models import Evidence, Hypothesis, Issue

FACTOR_KEYS = ("proximity", "explains_share", "competitiveness",
               "prior_hit", "counterfactual", "mechanism")

# 判定量表：序数而非连续值——LLM 报"精确多少"没有意义，报档位才可核查
FACTOR_SCALE = {"yes": 1.0, "likely": 0.7, "unclear": 0.4, "no": 0.0}

_SYSTEM = (
    "你是性能归因因子判定器。给你一条性能问题（Issue）的证据与候选根因，"
    "你只判定六个因子各自是否成立，输出 JSON。"
    "规则：① 禁止输出任何数值结论或置信度——置信度由公式计算，不是你的职责；"
    "② 每个因子判定必须附 evidence_refs（引用给定证据编号 e0/e1/... 或 "
    "anchor:<文件:行>），无引用的判定会被作废；"
    "③ verdict 只能取 yes/likely/unclear/no 四档；"
    "④ 证据不足就答 unclear，禁止脑补。"
)

_PROMPT_TMPL = """请对以下候选根因逐因子判定。

## Issue 证据（按编号引用）
{evidence_block}

## 候选根因
root_cause: {root_cause}
code_anchors: {anchors}

## 六因子含义（逐个判定，输出同名键）
- proximity: 候选根因的代码位置是否就在越线现场/热路径上
- explains_share: 该机制能否解释 Issue 量级的主要部分（定量对上才算成立）
- competitiveness: 与同场景其他可解释机制相比是否占优（证据里有更强候选则降档）
- prior_hit: 知识库是否存在同三重签名历史记录（本提示未附知识库时答 unclear）
- counterfactual: 若该机制不存在，指标是否会回落到线内（按机制逻辑推演）
- mechanism: 机制链条是否自洽（代码→分配/绘制→指标），证据里有无拆台反例

输出 JSON（严格，六因子全给）：
{{"proximity": {{"verdict": "yes|likely|unclear|no", "evidence_refs": ["e0"], "reason": "一句话"}} , 其余五键同形}}
"""


class JudgeError(RuntimeError):
    """判定链失败（模型未过门禁 / 输出非 JSON / 结构非法）。"""


def _evidence_block(issue: Issue) -> tuple[str, dict[str, Evidence]]:
    """Issue.evidence 编成 e0/e1/... 引用表；返回文本 + 编号→Evidence 映射。"""
    lines, alias = [], {}
    for i, ev in enumerate(issue.evidence):
        k = f"e{i}"
        alias[k] = ev
        nb = f" σ±{ev.noise_band:g}" if ev.noise_band else ""
        lines.append(f"{k}: [{ev.tool}/{ev.data_tier}] {ev.value:g}{ev.unit}{nb}"
                     + (f"  源:{ev.raw_ref}" if ev.raw_ref else ""))
    return "\n".join(lines), alias


def _connectivity_ok() -> tuple[bool, str]:
    """I13：连通性测试只在第一次使用时跑；本链只读结果，不重复触发推理。"""
    try:
        rec = json.loads(Path(RESULT_PATH_DEFAULT).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, "连通性测试从未跑过（verification_gate 未通过，禁止真实推理）"
    except json.JSONDecodeError:
        return False, "连通性结果文件损坏"
    if not rec.get("all_passed"):
        return False, f"连通性测试未通过（provider={rec.get('provider')}, model={rec.get('model')}）"
    return True, ""


def judge_factors(cfg: LlmConfig, issue: Issue, root_cause: str,
                  code_anchors: list[str], *, agent: str = "diagnose",
                  client=None) -> tuple[list[FactorJudgment], dict]:
    """单假设 → FactorJudgment[]（值来自量表，置信度不在这里算）。

    返回 (judgments, 原始判定留档)。留档含每因子档位/理由/引用与 token 用量，
    供 trace span 与报告回溯（I10），不进入 Hypothesis 本身。
    """
    ok, why = _connectivity_ok()
    if not ok:
        raise JudgeError(why)

    eff = cfg.effective_for(agent)
    provider, model = eff["provider"], eff["model"]

    evidence_block, alias = _evidence_block(issue)
    prompt = _PROMPT_TMPL.format(
        evidence_block=evidence_block, root_cause=root_cause,
        anchors=", ".join(code_anchors) or "（无）")

    cli = client or make_client(cfg.providers[provider])
    kwargs = dict(
        model=model,
        messages=[{"role": "system", "content": _SYSTEM},
                  {"role": "user", "content": prompt}],
        temperature=eff.get("temperature", 0.2),
        max_tokens=eff.get("max_output_tokens", 4096),
    )
    if eff.get("json_mode"):
        kwargs["response_format"] = {"type": "json_object"}
    resp = cli.chat.completions.create(**kwargs)

    raw_text = (resp.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"模型输出非 JSON（{exc.msg}）；前 120 字：{raw_text[:120]}") from exc
    if not isinstance(data, dict):
        raise JudgeError("模型输出 JSON 但非对象")

    judgments: list[FactorJudgment] = []
    judged: dict[str, dict] = {}
    for f in FACTOR_KEYS:
        item = data.get(f) if isinstance(data.get(f), dict) else {}
        verdict = str(item.get("verdict", "")).lower()
        refs = [str(r) for r in (item.get("evidence_refs") or [])]
        if verdict not in FACTOR_SCALE:
            value, ptr, status = 0.0, "", "invalid-verdict"
        else:
            value = FACTOR_SCALE[verdict]
            # 指针合法性：e 编号必须在给定证据表里；anchor: 前缀原样保留
            valid = [r for r in refs if r in alias or r.startswith("anchor:")]
            if valid:
                first = valid[0]
                ptr = alias[first].raw_ref if first in alias else first
                status = verdict
            else:
                ptr, status = "", verdict + "(无有效指针→作废)"
                value = 0.0
        judgments.append(FactorJudgment(factor=f, value=value, evidence_ref=ptr))
        judged[f] = {"verdict": status, "value": value, "refs": refs,
                     "reason": str(item.get("reason", ""))[:200]}

    usage = getattr(resp, "usage", None)
    judged["_meta"] = {"model": model, "provider": provider,
                       "weights_in_play": "formula(c_attr)",
                       "token_in": getattr(usage, "prompt_tokens", None),
                       "token_out": getattr(usage, "completion_tokens", None),
                       "prompt_sha256_8": hashlib.sha256(prompt.encode()).hexdigest()[:8]}
    return judgments, judged


def build_hypotheses_for_issue(cfg: LlmConfig, issue: Issue,
                               candidates: list[tuple[str, list[str]]], *,
                               agent: str = "diagnose", client=None) -> tuple[list[Hypothesis], dict]:
    """Issue × 候选根因[] → 按 C_attr 降序的 Hypothesis[] + 判定留档。

    候选根因应来自 Diagnose 的检索步（代码锚点 + KnowledgeStore 同签名历史）；
    本函数不生成候选——生成与判定分离，防"自问自答"。
    路由（needs_human 与否）用 route_decision() 判，不在此下结论。
    """
    hs, archive = [], {}
    for root_cause, anchors in candidates:
        judgments, judged = judge_factors(cfg, issue, root_cause, anchors,
                                          agent=agent, client=client)
        hs.append(build_hypothesis(root_cause, anchors, judgments, issue))
        archive[root_cause] = judged
    return rank(hs), archive
