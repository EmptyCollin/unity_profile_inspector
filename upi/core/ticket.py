"""批量工单与串行执行器（I3a）。

设计约束（AGENTS §8.4 / DESIGN §14.4）：
- 工单可以列多个候选；执行**一次只落一个 patch**；
- 每个 patch 单独检查 permission_tier；批量批准不绕过权限闸；
- 每步 snapshot → apply → compile/smoke → observe → keep/rollback；
- 常驻结论只 `applied_unverified`，不得写 `verified/resolved`；
- 编译失败/异常/守护劣化 → 回滚并停止后续低置信候选。

本模块不自己生成 EditPlan：I3a 用注入的 `plan_provider`；默认没有 provider，
候选会标 `needs_human`，这是诚实边界，不是“修好了”。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

from upi.core import events as E
from upi.core.cluster import IssueCluster
from upi.core.fingerprint import FingerprintStore, features_from_cluster
from upi.core.models import EditPlan

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "runs" / "_resident" / "tickets"

ItemStatus = Literal["pending", "needs_human", "applied_unverified",
                     "rolled_back", "failed", "skipped"]
TicketStatus = Literal["draft", "awaiting_approval", "approved",
                       "executing", "done", "rejected"]


class TicketItem(BaseModel):
    item_id: str
    cluster_id: str
    metric: str
    shape: str
    kind: str
    count: int = 0
    first_rel: int = 0
    fingerprint_hit: bool = False
    pattern_id: str = ""
    playbook_id: str = ""
    permission_tier: Literal["auto", "semi", "blocked"] = "blocked"
    plan: Optional[dict] = None
    status: ItemStatus = "pending"
    observation: dict = Field(default_factory=dict)
    error: str = ""
    item_approve_token: str = ""
    human_approved: bool = False
    approver: str = ""
    approval_note: str = ""

    def edit_plan(self) -> Optional[EditPlan]:
        return EditPlan(**self.plan) if self.plan else None


class BatchTicket(BaseModel):
    ticket_id: str
    project_id: str = ""
    mode: Literal["resident", "regression"] = "resident"
    created_at: str
    status: TicketStatus = "awaiting_approval"
    items: list[TicketItem] = Field(default_factory=list)
    approver: str = ""
    note: str = ""
    approve_token: str = ""
    current_index: int = 0
    results: list[dict] = Field(default_factory=list)
    history: list[dict] = Field(default_factory=list)

    def summary(self) -> dict:
        return {
            "ticket_id": self.ticket_id, "mode": self.mode,
            "project_id": self.project_id,
            "created_at": self.created_at, "status": self.status,
            "items": len(self.items),
            "pending": sum(1 for i in self.items if i.status == "pending"),
            "needs_human": sum(1 for i in self.items if i.status == "needs_human"),
            "done": sum(1 for i in self.items
                        if i.status in ("applied_unverified", "rolled_back", "failed")),
            "approve_token": self.approve_token if self.status == "awaiting_approval" else "",
            "approver": self.approver,
            "results": self.results[-8:],
            "history": self.history[-20:],
            "item_list": [{
                "item_id": i.item_id,
                "metric": i.metric, "shape": i.shape, "status": i.status,
                "playbook_id": i.playbook_id, "tier": i.permission_tier,
                "fingerprint_hit": i.fingerprint_hit, "error": i.error,
                "has_plan": i.plan is not None,
                "plan_files": sorted({str(d.get("file", ""))
                                      for d in (i.plan or {}).get("diffs", [])}),
                "diff_count": len((i.plan or {}).get("diffs", [])),
                "plan_diffs": [{
                    "file": str(d.get("file", "")),
                    "why": str(d.get("_why", "")),
                    "find": str(d.get("find", ""))[:400],
                    "replace": str(d.get("replace", ""))[:400],
                } for d in (i.plan or {}).get("diffs", [])[:4]],
                "human_approved": i.human_approved,
                "approve_token": (i.item_approve_token
                                  if i.status == "needs_human" and i.plan else ""),
            } for i in self.items[:8]],
        }


_AUTO_PLAYBOOKS = {"PB-REUSE-BUFFER", "PB-CACHE-COMPONENT", "PB-PREGEN-LOOKUP"}
_SEMI_PLAYBOOKS = {"PB-SHARED-MAT-MPB", "PB-PARAM-EQUAL-GUARD", "PB-ASYNC-STAGGER"}


def _tier(playbook: str) -> Literal["auto", "semi", "blocked"]:
    if playbook in _AUTO_PLAYBOOKS:
        return "auto"
    if playbook in _SEMI_PLAYBOOKS:
        return "semi"
    return "blocked"


_RANK = {"auto": 0, "semi": 1, "blocked": 2}


def _max_tier(a: str, b: str) -> Literal["auto", "semi", "blocked"]:
    return a if _RANK.get(a, 2) >= _RANK.get(b, 2) else b  # type: ignore[return-value]


def _token(ticket_id: str, items: list[TicketItem]) -> str:
    raw = ticket_id + "|" + "|".join(i.cluster_id for i in items)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:4].upper()


def _item_token(ticket_id: str, item_id: str) -> str:
    return hashlib.sha256(f"{ticket_id}|{item_id}".encode("utf-8")).hexdigest()[:4].upper()


PlanProvider = Callable[[IssueCluster, str], Optional[EditPlan]]


def build_ticket(clusters: list[IssueCluster], *, fp_store: Optional[FingerprintStore] = None,
                 plan_provider: Optional[PlanProvider] = None,
                 mode: Literal["resident", "regression"] = "resident",
                 limit: int = 8) -> BatchTicket:
    tid = "bt-" + time.strftime("%Y%m%d_%H%M%S") + "-" + secrets.token_hex(3)
    items: list[TicketItem] = []
    for c in clusters[:max(1, limit)]:
        hit = None
        if fp_store is not None:
            hit = fp_store.route(features_from_cluster(c))
        playbook = hit.record.playbook_id if (hit and hit.record) else ""
        tier = _tier(playbook)
        plan = None
        err = ""
        if hit is None or not hit.hit:
            err = "fingerprint_miss (LLM/human required)"
        elif tier != "auto":
            err = f"tier={tier}: resident auto only"
        elif plan_provider is None:
            err = "no EditPlan provider (I3a)"
        else:
            plan = plan_provider(c, playbook)
            if plan is None:
                err = "plan_provider returned none"
            else:
                # 计划自身的权限档不得被 playbook 映射降级：
                # blocked 模板即使 playbook 是 auto，也必须 needs_human。
                tier = _max_tier(tier, plan.permission_tier)
                playbook = plan.playbook_id or playbook
                if tier != "auto":
                    err = f"tier={tier}: plan requires individual approval"
        status: ItemStatus = "pending" if plan is not None and tier == "auto" else "needs_human"
        iid = "it-" + secrets.token_hex(4)
        items.append(TicketItem(
            item_id=iid, cluster_id=c.cluster_id,
            metric=c.metric, shape=c.shape, kind=c.kind, count=c.count,
            first_rel=c.first_rel,
            fingerprint_hit=bool(hit and hit.hit),
            pattern_id=(hit.record.pattern_id if (hit and hit.record) else ""),
            playbook_id=playbook, permission_tier=tier,
            plan=(plan.model_dump() if plan is not None else None),
            status=status, error=err,
            item_approve_token=_item_token(tid, iid),
        ))
    try:
        from upi.config.project_profile import active_profile
        project_id = active_profile().profile_id
    except Exception:  # noqa: BLE001
        project_id = ""
    return BatchTicket(ticket_id=tid, project_id=project_id, mode=mode,
                       created_at=E.now(),
                       status="awaiting_approval", items=items,
                       approve_token=_token(tid, items))


def build_opportunity_ticket(findings, project_root, *, project_id: str = "") -> BatchTicket:
    """I7d：OpportunityFinding → BatchTicket（source-aware plan 或 needs_human）。"""
    from upi.core.opportunity_planner import plan_for

    tid = "bt-" + time.strftime("%Y%m%d_%H%M%S") + "-" + secrets.token_hex(3)
    items: list[TicketItem] = []
    for f in findings:
        plan = plan_for(f, project_root)
        tier = plan.permission_tier if plan is not None else "blocked"
        status: ItemStatus = ("pending" if plan is not None and tier == "auto"
                              else "needs_human")
        iid = "it-" + secrets.token_hex(4)
        items.append(TicketItem(
            item_id=iid, cluster_id=f.id, metric=f.metric, shape="opportunity",
            kind="opportunity", count=1, pattern_id=f.pattern_id,
            playbook_id=(plan.playbook_id if plan is not None else ""),
            permission_tier=tier, plan=(plan.model_dump() if plan is not None else None),
            status=status,
            error=("" if plan is not None else "no source-aware plan; needs human"),
            item_approve_token=_item_token(tid, iid),
        ))
    return BatchTicket(ticket_id=tid, project_id=project_id, mode="resident",
                       created_at=E.now(), status="awaiting_approval", items=items,
                       approve_token=_token(tid, items))


class TicketStore:
    def __init__(self, root: Optional[Path | str] = None):
        self.root = Path(root) if root else Path(
            os.environ.get("UPI_TICKET_DIR") or DEFAULT_DIR)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, ticket_id: str) -> Path:
        return self.root / f"{ticket_id}.json"

    def save(self, t: BatchTicket) -> Path:
        p = self.path(t.ticket_id)
        E.atomic_write_text(p, t.model_dump_json(indent=2))
        return p

    def load(self, ticket_id: str) -> Optional[BatchTicket]:
        p = self.path(ticket_id)
        if not p.exists():
            return None
        try:
            return BatchTicket(**json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return None

    def list(self, limit: int = 20) -> list[BatchTicket]:
        out = []
        for p in sorted(self.root.glob("bt-*.json"), key=lambda x: x.stat().st_mtime,
                        reverse=True)[:max(1, limit)]:
            try:
                out.append(BatchTicket(**json.loads(p.read_text(encoding="utf-8"))))
            except Exception:  # noqa: BLE001
                continue
        return out


def approve(ticket: BatchTicket, *, approver: str, note: str, token: str) -> tuple[bool, str]:
    if ticket.status != "awaiting_approval":
        return False, f"状态不是 awaiting_approval（{ticket.status}）"
    if not approver.strip() or not note.strip():
        return False, "批准人和批语必填"
    if not secrets.compare_digest((token or "").strip().upper(), ticket.approve_token):
        return False, "校验码不匹配（请读完工单页再签）"
    ticket.status = "approved"
    ticket.approver = approver.strip()
    ticket.note = note.strip()
    ticket.history.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "action": "approve_ticket",
                           "actor": ticket.approver, "note": ticket.note})
    return True, "approved"


def reject(ticket: BatchTicket, *, approver: str, note: str) -> tuple[bool, str]:
    if ticket.status != "awaiting_approval":
        return False, f"状态不是 awaiting_approval（{ticket.status}）"
    if not approver.strip() or not note.strip():
        return False, "驳回人和批语必填"
    ticket.status = "rejected"
    ticket.approver = approver.strip()
    ticket.note = note.strip()
    ticket.history.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "action": "reject_ticket",
                           "actor": ticket.approver, "note": ticket.note})
    return True, "rejected"


def approve_item(ticket: BatchTicket, item_id: str, *, approver: str,
                 note: str, token: str) -> tuple[bool, str]:
    """单条批准 blocked/semi patch；批量批准不替代这一步。"""
    if ticket.status not in ("approved", "executing", "done"):
        return False, f"工单状态 {ticket.status} 不接受单条批准"
    if not approver.strip() or not note.strip():
        return False, "批准人和批语必填"
    item = next((i for i in ticket.items if i.item_id == item_id), None)
    if item is None:
        return False, "item not found"
    if item.plan is None:
        return False, "该候选没有 plan，不能批准执行"
    if item.status not in ("needs_human", "pending"):
        return False, f"item 状态 {item.status} 不接受批准"
    if not secrets.compare_digest((token or "").strip().upper(),
                                  item.item_approve_token):
        return False, "单条校验码不匹配"
    item.human_approved = True
    item.approver = approver.strip()
    item.approval_note = note.strip()
    ticket.history.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "action": "approve_item", "item_id": item.item_id,
                           "metric": item.metric, "actor": item.approver,
                           "note": item.approval_note})
    if item.status == "needs_human":
        item.status = "pending"
        item.error = ""
    return True, "item approved"


class TicketExecutor:
    """串行执行器；所有外部动作注入，便于离线测试与后续接 Unity。"""

    def __init__(self, store: TicketStore, *,
                 apply: Callable[[EditPlan], None],
                 compile_ok: Callable[[], bool],
                 observe: Callable[[], dict],
                 rollback: Callable[[], None]):
        self.store = store
        self.apply = apply
        self.compile_ok = compile_ok
        self.observe = observe
        self.rollback = rollback

    def execute(self, ticket: BatchTicket) -> BatchTicket:
        if ticket.status != "approved":
            ticket.results.append({"error": f"未批准（{ticket.status}）"})
            self.store.save(ticket)
            return ticket
        ticket.status = "executing"
        self.store.save(ticket)
        stopped = False
        for idx, item in enumerate(ticket.items):
            ticket.current_index = idx
            if stopped:
                item.status = "skipped"
                item.error = "previous patch stopped the batch"
                continue
            if item.status != "pending":
                continue
            plan = item.edit_plan()
            if plan is None:
                item.status = "needs_human"
                item.error = "no plan"
                continue
            if item.permission_tier != "auto" and not item.human_approved:
                item.status = "needs_human"
                item.error = f"tier={item.permission_tier} requires individual approval"
                continue
            try:
                self.apply(plan)
                if not self.compile_ok():
                    self.rollback()
                    item.status = "rolled_back"
                    item.error = "compile_failed"
                    stopped = True
                    continue
                obs = self.observe() or {}
                item.observation = obs
                item.status = "applied_unverified"
                ticket.results.append({"item_id": item.item_id, "metric": item.metric,
                                       "status": item.status, "observation": obs})
            except Exception as e:  # noqa: BLE001 - 单 patch 失败必须回滚并留痕
                try:
                    self.rollback()
                except Exception as e2:  # noqa: BLE001
                    item.error = f"apply失败且回滚失败: {e2}"
                item.status = "failed"
                if not item.error:
                    item.error = f"{type(e).__name__}: {e}"[:200]
                stopped = True
        ticket.status = "done"
        self.store.save(ticket)
        return ticket
