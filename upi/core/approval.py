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
写决定：在同目录放 `decision.json`，形如
`{{"decision": "approve", "approver": "<你的名字>", "note": "<批的具体内容>"}}`
（approve 必须同时给 approver 与 note，缺一视同未批。）
"""


class ApprovalStore:
    """工单与决定的落盘位置：runs/<run_id>/approvals/<request_id>/。"""

    def __init__(self, run_dir: Path):
        self.root = Path(run_dir) / "approvals"

    def _dir(self, request_id: str) -> Path:
        return self.root / request_id

    def submit(self, req: ApprovalRequest) -> dict:
        d = self._dir(req.request_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "request.json").write_text(
            json.dumps(req.model_dump(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        (d / "request.md").write_text(render_markdown(req), encoding="utf-8")
        return {"request_id": req.request_id,
                "md": str(d / "request.md"),
                "decision_path": str(d / "decision.json")}

    def load_decision(self, request_id: str) -> Optional[ApprovalDecision]:
        """读人写的决定；文件不存在/不合格即 None（不猜、不默认批准）。"""
        p = self._dir(request_id) / "decision.json"
        if not p.exists():
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        raw.setdefault("request_id", request_id)
        raw.setdefault("approver", "")
        try:
            return ApprovalDecision(**raw)
        except Exception:  # noqa: BLE001 — 决定不合法=没有有效决定，绝不放行写入
            return None

    def record_used(self, request_id: str, decision: ApprovalDecision,
                    outcome: dict) -> None:
        """决定被消费后落账（§8.5：批准样本要能分层统计）。"""
        p = self._dir(request_id) / "used.json"
        p.write_text(json.dumps({"decision": decision.model_dump(),
                                 "outcome": outcome,
                                 "used_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                                ensure_ascii=False, indent=2), encoding="utf-8")

    def list_open(self) -> list[str]:
        """待批队列（有 request 无 decision）。"""
        if not self.root.exists():
            return []
        return sorted(d.name for d in self.root.iterdir()
                      if (d / "request.json").exists()
                      and not (d / "decision.json").exists())
