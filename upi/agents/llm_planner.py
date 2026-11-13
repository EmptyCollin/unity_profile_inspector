"""LLM 修复规划器（I10d）。

- 输入：Issue 证据（已筛查）+ 假设根因 + 当前源码片段；
- 输出：结构化补丁 `{why, edits:[{file, find, replace}]}`，或明确失败原因；
- 执行器只做精确命中校验/应用/编译，不做模板替换；
- 容错：超时/限流/坏 JSON 有界重试；find 不命中把实际源码回喂，最多 2 轮；
- 提示词泄漏筛查：复用 factor_judge 的 fail-closed 规则，代码片段只来自工程内。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from upi.agents.factor_judge import (_evidence_block, _safe_ref, _screen_prompt)
from upi.config.connectivity import make_client
from upi.config.settings import LlmConfig
from upi.core.models import EditPlan, Evidence, Issue

_SYSTEM = (
    "你是 Unity 性能修复规划器。给你一条性能问题、候选根因与相关源码，"
    "请给出**最小、语义保持**的补丁。只输出 JSON，不要解释性前后缀。"
    "约束：① 只改给出的文件；② 不得改变玩法语义、数值、时序；"
    "③ find 必须是当前源码中的精确片段且只出现一次；④ 证据不足就返回 "
    '{"why":"insufficient_evidence","edits":[]}。'
)

_PROMPT = """性能问题与候选根因：

## 历史参考（仅参考，可能不适用）
{history}

## 证据
{evidence}

## 根因假设
root_cause: {root_cause}
code_anchors: {anchors}
候选证据卡:
{candidate_card}

## 相关源码（当前工程，未修改）
{source}

## 判断规则
- 若源码里存在每帧创建/分配、但对象未被使用或可缓存/池化/移到初始化，直接给结构修复；
- 局部临时对象若创建后只做 Apply/读取自身属性等自检、未参与渲染或状态，视为可删除浪费；
- 不要因为缺少“耗时占比”就拒绝；源码结构本身就是证据；
- 只改数字/循环上界不算修复。

## 输出 JSON
{{"why":"一句话说明为什么这样改", "edits":[{{"file":"Assets/...", "find":"精确原文", "replace":"替换后"}}]}}
"""


def _read_snippets(project_root: Path, anchors: list[str], *,
                   before: int = 25, after: int = 45) -> tuple[str, dict[str, str]]:
    """按锚点抽源码片段；只读工程内文件。返回 (拼接文本, 文件→原文)。"""
    chunks, texts = [], {}
    for a in anchors:
        f, _, rest = a.partition("::")
        method = rest.strip()
        fp = (Path(project_root) / f).resolve()
        try:
            fp.relative_to(Path(project_root).resolve())
        except ValueError:
            continue
        if not fp.is_file() or f in texts:
            continue
        text = fp.read_text(encoding="utf-8", errors="replace")
        texts[f] = text
        lines = text.splitlines()
        start = 0
        end = None
        if method:
            m = method.split(".")[-1]
            for i, ln in enumerate(lines):
                if ("void " + m) in ln or (" " + m + "(") in ln or (m + "(") in ln:
                    start = i
                    break
            # 只取方法体（大括号配对），避免把相邻方法带进补丁范围
            depth = 0
            started = False
            for j in range(start, len(lines)):
                for ch in lines[j]:
                    if ch == "{":
                        depth += 1
                        started = True
                    elif ch == "}":
                        depth -= 1
                if started and depth <= 0:
                    end = j + 1
                    break
        lo = max(0, start - before)
        hi = end if end is not None else min(len(lines), start + after)
        chunks.append(f"### {f}（行 {lo+1}-{hi}）\n" + "\n".join(lines[lo:hi]))
    return "\n\n".join(chunks), texts


def _call(cfg: LlmConfig, prompt: str, *, agent: str = "fix",
          client=None) -> tuple[str, dict]:
    eff = cfg.effective_for(agent)
    cli = client or make_client(cfg.providers[eff["provider"]])
    kwargs = dict(model=eff["model"],
                  messages=[{"role": "system", "content": _SYSTEM},
                            {"role": "user", "content": prompt}],
                  temperature=float(eff.get("temperature", 0.2)),
                  max_tokens=int(eff.get("max_output_tokens", 4096)))
    if eff.get("json_mode"):
        kwargs["response_format"] = {"type": "json_object"}
    t0 = time.time()
    resp = cli.chat.completions.create(**kwargs)
    text = resp.choices[0].message.content or ""
    meta = {"token_in": getattr(resp.usage, "prompt_tokens", 0) or 0,
            "token_out": getattr(resp.usage, "completion_tokens", 0) or 0,
            "elapsed_ms": int((time.time() - t0) * 1000)}
    return text, meta


def _parse(text: str) -> Optional[dict]:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s
        s = s.split("\n", 1)[1] if "\n" in s else s
    try:
        d = json.loads(s)
    except Exception:  # noqa: BLE001
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            return None
        try:
            d = json.loads(s[i:j + 1])
        except Exception:  # noqa: BLE001
            return None
    return d if isinstance(d, dict) else None


def _numeric_only(find: str, repl: str) -> bool:
    """只改数字/循环上界的“调参式”改动（I15 伪优化门禁）。"""
    import re
    norm = lambda s: re.sub(r"\d+", "N", s)  # noqa: E731
    return norm(find) == norm(repl)


def _validate(project_root: Path, edits: list[dict]) -> tuple[Optional[list[dict]], str]:
    out = []
    for e in edits:
        f = str(e.get("file", "")).strip()
        find = str(e.get("find", ""))
        repl = str(e.get("replace", ""))
        if not f or not find:
            return None, "edit 缺 file/find"
        fp = (Path(project_root) / f).resolve()
        try:
            fp.relative_to(Path(project_root).resolve())
        except ValueError:
            return None, f"文件越界：{f}"
        if not fp.is_file():
            return None, f"文件不存在：{f}"
        text = fp.read_text(encoding="utf-8", errors="replace")
        n = text.count(find)
        if n != 1:
            return None, f"{f} 中 find 命中 {n} 次（必须恰好 1 次）"
        if repl == find:
            return None, f"{f} 的 replace 与 find 相同（原地空写）"
        out.append({"file": f, "find": find, "replace": repl,
                    "expect_count": 1, "_why": str(e.get("why", ""))[:200]})
    return out, ""


def plan_issue(cfg: LlmConfig, issue: Issue, hypothesis: dict,
               project_root: Path | str, *, client=None,
               max_rounds: int = 3, history: str = "",
               candidate_card: str = "",
               retry_insufficient: bool = False) -> tuple[Optional[EditPlan], dict]:
    """假设 → EditPlan。失败返回 (None, meta{error,...})，不抛半路异常。"""
    root = Path(project_root).resolve()
    rc = str(hypothesis.get("root_cause", ""))
    anchors = list(hypothesis.get("code_anchors") or [])
    source, _texts = _read_snippets(root, anchors)
    if not source:
        return None, {"error": "no_source_snippet", "why": "锚点无法定位到工程内源码"}
    evidence, _alias = _evidence_block(issue)
    prompt = _PROMPT.format(
        history=(history or "（本项目暂无历史；仅凭本次证据）"),
        evidence=evidence, root_cause=rc,
        anchors=", ".join(anchors) or "（无）",
        candidate_card=(candidate_card or "（无额外候选统计）"), source=source)
    meta = {"rounds": 0, "tokens_in": 0, "tokens_out": 0}
    last_err = ""
    for rnd in range(1, max_rounds + 1):
        meta["rounds"] = rnd
        try:
            _screen_prompt(prompt)
            text, m = _call(cfg, prompt, client=client)
        except Exception as e:  # noqa: BLE001 — 网络/鉴权/超时：有界失败
            last_err = f"{type(e).__name__}: {str(e)[:160]}"
            if rnd < max_rounds:
                time.sleep(min(2 ** rnd, 8))
                continue
            break
        meta["tokens_in"] += m.get("token_in", 0)
        meta["tokens_out"] += m.get("token_out", 0)
        doc = _parse(text)
        if doc is None:
            last_err = "输出不是合法 JSON"
            prompt += f"\n\n上次输出不是合法 JSON。只输出 JSON。原始输出：{text[:400]}"
            continue
        if str(doc.get("why", "")).strip() == "insufficient_evidence":
            if retry_insufficient and rnd < max_rounds:
                prompt += ("\n\n上一次判证据不足。若源码中存在每帧创建/分配且"
                           "未被使用或可缓存/池化/移到初始化，必须给出结构修复，"
                           "不要因缺少耗时占比拒绝。")
                last_err = "insufficient_evidence"
                continue
            return None, {"error": "insufficient_evidence",
                          "why": "LLM 判断证据不足", **meta}
        edits = doc.get("edits") or []
        if not edits:
            last_err = "edits 为空"
            prompt += "\n\n上次 edits 为空；要么给出补丁，要么返回 insufficient_evidence。"
            continue
        diffs, err = _validate(root, edits)
        if diffs is None:
            last_err = err
            # 回喂实际源码片段，最多再试
            prompt += (f"\n\n上次补丁被拒：{err}\n以下是当前源码，请据此重写：\n{source}")
            continue
        if diffs and all(_numeric_only(d["find"], d["replace"]) for d in diffs):
            last_err = ("pseudo_optimization：只改常量/循环上界不算结构修复，"
                        "请移到初始化/缓存/池化/事件驱动")
            prompt += (f"\n\n上次补丁被拒：{last_err}。"
                       "必须改变结构，不能只把数字改小。")
            continue
        if diffs and not all(d["find"] in source for d in diffs):
            last_err = "patch_outside_hypothesis：补丁不在该假设的源码片段内"
            prompt += (f"\n\n上次补丁被拒：{last_err}。"
                       "只能修改给定源码片段里的代码。")
            continue
        return EditPlan(playbook_id="PB-LLM",
                        diffs=diffs,
                        invariants_assert=["LLM 声称语义保持，需人工复核",
                                           "find 精确命中一次"],
                        side_effect="unknown", permission_tier="semi",
                        touched_assets=sorted({d["file"] for d in diffs})), meta
    return None, {"error": last_err or "planner_failed", **meta}
