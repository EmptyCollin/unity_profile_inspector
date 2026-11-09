"""人工批准回路（M4，AGENTS §1.4/§5、DESIGN §4/§8.5）。

semi/blocked 档的修复**没有自动执行权**（I3）。本模块负责"先报"这一半：
把工单渲染成人能审的形态，收人写的决定，再喂给 fixer 的批准闸。
批准只是"能不能落地"的许可，不改变判定口径——落地后仍要 verify。

工单五件套（AGENTS §5 硬要求，缺任一件 Pydantic 构造期拒收）：
  1 证据      Evidence[]（数值来自采样，I1；这里只搬运不改数）
  2 方案      EditPlan diff + 每条改动对应到哪条证据（G5 可追溯）
  3 预期效应  Hypothesis.expected_effect + realized_over_expected 口径
  4 回滚方式  snapshot 的 git_ref + rollback 语义（G9：只依赖 git）
  5 失败预案  不批准/批准后失败/验证不通过 三种情况各自怎么办

设计边界（诚实声明）：
- 本模块**不代用户批准**，也不判断该不该批。它只保证：没有人的决定，
  写入通道一定是关闭的；有决定时，决定内容与批准人可回溯。
- 批准样本会自我强化（§8.5 过拟合通道之一）：decision.approver 与结果
  一起入库，按审批者分层统计，影子一致率单独算——本模块记录原始事实，
  统计归 Stats/Knowledge。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from upi.core import events as E
from upi.core.models import EditPlan, Evidence, Hypothesis, Issue

ROOT = Path(__file__).resolve().parents[2]


class ApprovalRequest(BaseModel):
    """递交人工审批的工单。字段名即五件套，缺项构造期拒收。"""

    request_id: str
    run_id: str
    issue_id: str
    permission_tier: Literal["semi", "blocked"]
    reason_not_auto: str              # 为什么没自动执行（命中 §3 哪一条）

    # 1 证据
    evidence: list[Evidence]
    signature: dict                   # Issue.signature 机制级（不含标识符）
    magnitude: float
    deviation_ratio: float

    # 2 方案
    edit_plan: EditPlan
    diff_evidence_map: list[dict]     # [{diff_index, why, evidence_refs[]}]

    # 3 预期效应
    hypothesis: Hypothesis

    # 4 回滚方式
    rollback: dict                    # {git_ref, restores, untouched[]}

    # 5 失败预案
    failure_plan: dict                # {on_reject, on_compile_fail, on_verify_fail}

    created_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    @model_validator(mode="after")
    def _five_pieces_present(self):
        missing = [k for k in ("evidence", "edit_plan", "hypothesis",
                               "rollback", "failure_plan")
                   if not getattr(self, k)]
        if missing:
            raise ValueError(f"工单缺件（AGENTS §5 五件套）：{missing}")
        if self.permission_tier == "auto":
            raise ValueError("auto 档不进人工回路（§3：无需批准，也不该来要批准）")
        return self

    @model_validator(mode="after")
    def _diff_mappable_to_evidence(self):
        # G5 的批准前预演：每条 diff 都要能指到证据，指不到的工单不予递交
        un = [d.get("diff_index") for d in self.diff_evidence_map
              if not d.get("evidence_refs")]
        if un:
            raise ValueError(f"diff {un} 无证据对应，工单不予递交（G5 预演）")
        if len(self.diff_evidence_map) != len(self.edit_plan.diffs):
            raise ValueError("diff 数与证据映射数不符，工单不予递交")
        return self


class ApprovalDecision(BaseModel):
    """人的决定。approve 才带得出 fixer 需要的 approval_note。"""

    request_id: str
    decision: Literal["approve", "reject", "defer"]
    approver: str                     # 谁批的（§8.5 按审批者分层统计）
    note: str = ""                    # 批了什么/为什么拒
    decided_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    # 计划要求的 approver_source：ui=界面内逐页读完+校验码，external=在终端
    # 手放 decision.json 的开发通道。默认 external——只有守护经 write_decision
    # (source="ui") 落盘才会被显式改回 ui，"绕过 UI 批的"因此天然可区分、可统计。
    decided_via: Literal["ui", "external"] = "external"

    @model_validator(mode="after")
    def _approve_needs_what_was_approved(self):
        # 批了但没说批什么 = 视同未批（fixer 侧同规则，这里提前拦）
        if self.decision == "approve" and not (self.approver.strip()
                                               and self.note.strip()):
            raise ValueError("approve 必须同时给出批准人与批准内容（I3/§1.4）")
        return self

    def as_approval_note(self) -> str:
        """喂给 fixer.execute(approval_note=...) 的文本。非 approve 返回空。"""
        if self.decision != "approve":
            return ""
        return (f"{self.approver} 于 {self.decided_at} 批准工单 "
                f"{self.request_id}：{self.note}")


def _rid(issue_id: str, plan: EditPlan) -> str:
    blob = f"{issue_id}|{plan.playbook_id}|{len(plan.diffs)}|" \
           f"{json.dumps([d['find'][:40] for d in plan.diffs], ensure_ascii=False)}"
    return "APR-" + hashlib.sha256(blob.encode()).hexdigest()[:10]


def build_request(*, run_id: str, issue: Issue, hypothesis: Hypothesis,
                  plan: EditPlan, permission_tier: str,
                  reason_not_auto: str, git_ref: str,
                  restores: str = "git reset --hard + 仅夹具路径 clean") -> ApprovalRequest:
    """从状态机已有对象拼工单（不新增任何数值，I1：全部搬运证据）。"""
    mapping = [{"diff_index": i,
                "why": d.get("_why", ""),
                "evidence_refs": [f"e{j}" for j in range(len(issue.evidence))]}
               for i, d in enumerate(plan.diffs)]
    return ApprovalRequest(
        request_id=_rid(issue.signature.metric, plan),
        run_id=run_id, issue_id=issue.signature.metric,
        permission_tier=permission_tier,  # type: ignore[arg-type]
        reason_not_auto=reason_not_auto,
        evidence=list(issue.evidence),
        signature=issue.signature.model_dump(),
        magnitude=issue.magnitude, deviation_ratio=issue.deviation_ratio,
        edit_plan=plan, diff_evidence_map=mapping,
        hypothesis=hypothesis,
        rollback={"git_ref": git_ref, "restores": restores,
                  "mechanism": "G9：回滚只依赖 git，不依赖 Editor 可达；"
                               "编译失败进 Safe Mode 时仍可按 tag 复位",
                  "untouched": ["非夹具路径", "Editor 内存态（重启验证）"]},
        failure_plan={
            "on_reject": "Issue 记 needs_human，保留全部证据链入库，不消耗 attempt 配额",
            "on_compile_fail": "G8 已实测：编译门禁失败即自动回滚，不烧采样成本",
            "on_verify_fail": "G1/G2 已实测：改善不足 k·σ 或守护劣化 → 回滚 + 进 Review，"
                              "审查裁定继续/撤回/转人工（配额 3 次，G6）",
            "on_perception_fail": "G3 感知门禁失败 → 无条件回滚 + 转人工（基建 M7，"
                                  "当前 pending，工单不得被读成'感知已验'）"})


def render_markdown(req: ApprovalRequest) -> str:
    """人读版工单：批准前要看的东西按五件套顺序排，数值全部带出处。"""
    ev = "\n".join(
        f"| e{i} | {e.tool} | {e.value:g} {e.unit} | ±{e.noise_band:g} | {e.data_tier} |"
        for i, e in enumerate(req.evidence))
    diffs = "\n".join(
        f"**改动 {m['diff_index']}**（{m['why']}）→ 对应证据 {', '.join(m['evidence_refs'])}\n"
        f"```diff\n--- a/{d['file']}\n+++ b/{d['file']}\n"
        f"@@ 原文 {len(d['find'].splitlines())} 行 → 新文 {len(d['replace'].splitlines())} 行\n```\n"
        f"- 命中要求：`expect_count={d.get('expect_count', 1)}`（不精确命中即整单中止，无半成品）"
        for m, d in zip(req.diff_evidence_map, req.edit_plan.diffs))
    exp = req.hypothesis.expected_effect or {}
    tok = read_token(req.request_id)
    return f"""# 待批准工单 {req.request_id}

**为什么不是你自动就能落地**：{req.reason_not_auto}
（档位 `{req.permission_tier}`；I3——修改权限只来自权限矩阵，不来自模型自我判断）

## 1 证据（数值全部来自采样，本工单不生成任何数字）
| 编号 | 来源 | 实测值 | 噪声带 | 数据档 |
|---|---|---|---|---|
{ev}

症状签名：`{req.signature}`
量级 Δ={req.magnitude:g}，偏离倍数={req.deviation_ratio:g}

## 2 方案（playbook `{req.edit_plan.playbook_id}`）
语义保持断言：
{chr(10).join('- ' + a for a in req.edit_plan.invariants_assert)}

{diffs}

副作用声明：`{req.edit_plan.side_effect}`
触碰资产：{', '.join(req.edit_plan.touched_assets)}

## 3 预期效应
根因：{req.hypothesis.root_cause}（归因置信度 {req.hypothesis.confidence:.2f}，
公式算得、非模型打分；权重版本 {req.hypothesis.weights_version}）
预期：{exp or '（该假设未给出量化预期——按 I2 应视为证据不足，请拒批并要求补证）'}

## 4 回滚方式
- 快照 tag：`{req.rollback['git_ref']}`
- 机制：{req.rollback['mechanism']}
- 复位范围：{req.rollback['restores']}；不动：{', '.join(req.rollback['untouched'])}

## 5 失败预案
- 不批准：{req.failure_plan['on_reject']}
- 批准后编译失败：{req.failure_plan['on_compile_fail']}
- 验证不通过：{req.failure_plan['on_verify_fail']}
- 感知门禁：{req.failure_plan['on_perception_fail']}

---
**本单审批校验码：`{tok}`**
批准时须手输这 4 位——它印在页脚，所以读到这一页才抄得到（防"没看内容就按回车"）。
"""


def split_pages(md: str) -> list[str]:
    """把 request.md 按五件套切成页，供 UI 强制逐页读完才允许批。

    第 1 页含工单标题与"为什么不能自动落地"，后各页对应 ## 1..## 5；
    末页含文末脚注，避免"读不完"。把"未读全不能批"从自觉变成闸门。
    """
    import re
    marks = [m.start() for m in re.finditer(r"(?m)^## ", md)]
    if not marks:
        return [md.strip()]
    pages = [md[:marks[1]].strip()] if len(marks) > 1 else [md.strip()]
    if len(marks) > 1:
        for i in range(1, len(marks) - 1):
            pages.append(md[marks[i]:marks[i + 1]].strip())
        pages.append(md[marks[-1]:].strip())
    return pages


def read_token(request_id: str) -> str:
    """本单的审批校验码（4 位，随工单唯一）。

    落实计划原话"批准前手输 request.md 尾部 4 位"：码印在工单**页脚**（见
    render_markdown），于是"读到最后一页"是"能输入正确码"的必要条件——防的
    就是没读内容就按回车的肌肉记忆。只依赖工单号 ⇒ 不依赖 md 全文（md 里就
    印着它，取 md 尾部字符会循环定义），且守护可独立复算、不信 UI 传来的字符串。
    边界要诚实：这不是攻击者防线（决定权只在守护，I3），是给经办人设的坎。
    """
    return hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:4]


def verify_token(request_id: str, typed: str) -> bool:
    return (typed or "").strip().lower() == read_token(request_id)


class ApprovalStore:
    """工单与决定的落盘位置：runs/<run_id>/approvals/<request_id>/。"""

    def __init__(self, run_dir: Path):
        self.root = Path(run_dir) / "approvals"

    def _dir(self, request_id: str) -> Path:
        return self.root / request_id

    def submit(self, req: ApprovalRequest) -> dict:
        d = self._dir(req.request_id)
        d.mkdir(parents=True, exist_ok=True)
        # 原子写：request.json 与 request.md 是"同一张工单"的两副面孔，
        # 半写会让守护看到有 request.json 却读不到内容（或反之），
        # 工单状态不可解释。失败必须上抛——出工单失败不能装作已出。
        E.write_json(d / "request.json", req.model_dump(),
                     what="approval_request")
        E.atomic_write(d / "request.md", render_markdown(req))
        return {"request_id": req.request_id,
                "md": str(d / "request.md"),
                "decision_path": str(d / "decision.json")}

    def write_decision(self, request_id: str, dec: ApprovalDecision,
                       *, source: str = "ui") -> bool:
        """决定落盘的**唯一入口**（阶段 2 的单一写入者约定）。

        UI 不再直接写 decision.json：它只发 decide 意向，由守护经这里落盘。
        理由：decision.json 是修复放行的凭据，两个进程都能写就可能后写覆盖
        前写、或绕过 ApprovalDecision 校验落一个"看着像批准"的文件。
        source 标出这条决定从哪来（ui=界面审批，external=开发通道手写）。
        """
        d = self._dir(request_id)
        if not (d / "request.json").exists():
            E.emit("decision_without_request", request_id=request_id,
                   source=source)
            return False
        payload = dec.model_dump()
        payload["decided_via"] = source
        ok = E.write_json(d / "decision.json", payload,
                          what="approval_decision", strict=False)
        if not ok:
            return False
        # 落盘确认：decision.json 是**单写者**的关键凭据（只有守护经此函数写），
        # 所以可以安全地回读比对——这与 daemon_cmd.json（双写者，回读会把
        # 合法并发覆盖误判成故障）不同。"以为批了但盘上不是这条"的代价是
        # 修复被错误放行，值得一次小文件读。
        back, err = E.load_json(d / "decision.json")
        if err or not back:
            E.emit("decision_verify_failed", request_id=request_id,
                   reason=err or "empty")
            return False
        for k in ("decision", "approver", "request_id"):
            if back.get(k) != payload.get(k):
                E.emit("decision_verify_failed", request_id=request_id,
                       field=k, want=str(payload.get(k))[:60],
                       have=str(back.get(k))[:60])
                return False
        E.emit("decision_written", request_id=request_id,
               decision=dec.decision, approver=dec.approver, source=source)
        return True

    def load_decision(self, request_id: str) -> Optional[ApprovalDecision]:
        """读决定；不存在/损坏/不合格一律 None（不猜、不默认批准）。
        损坏与"还没人批"必须区分留痕——前者是数据事故，后者是正常等待。"""
        p = self._dir(request_id) / "decision.json"
        raw, err = E.load_json(p)
        if err == "missing":
            return None
        if err:
            E.emit("decision_unreadable", request_id=request_id, reason=err)
            return None
        raw.setdefault("request_id", request_id)
        raw.setdefault("approver", "")
        try:
            return ApprovalDecision(**raw)
        except Exception as e:  # noqa: BLE001 — 决定不合法=没有有效决定，绝不放行写入
            E.emit("decision_invalid", request_id=request_id,
                   error=str(e)[:160])
            return None

    def record_used(self, request_id: str, decision: ApprovalDecision,
                    outcome: dict) -> None:
        """决定被消费后落账（§8.5：批准样本要能分层统计）。
        账本写失败必须上抛：丢了账，这条批准样本在统计里就永远隐身。"""
        E.write_json(self._dir(request_id) / "used.json",
                     {"decision": decision.model_dump(), "outcome": outcome,
                      "used_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                     what="approval_used")

    def list_open(self) -> list[str]:
        """待批队列（有 request 无 decision）。"""
        if not self.root.exists():
            return []
        return sorted(d.name for d in self.root.iterdir()
                      if (d / "request.json").exists()
                      and not (d / "decision.json").exists())
