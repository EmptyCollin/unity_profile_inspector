"""Unity 批量执行适配（I3b-2）。

把 `TicketExecutor` 的注入口接到真实 `snapshot` / `fixer.execute` / 探针观测：
- 每个 patch 前先建 attempt 快照（G4）；
- `fixer.execute` 负责权限闸、diff 精确中止、编译门禁、失败回滚；
- 成功只记 `applied_unverified`（常驻不做复现验证，G21）；
- 编译失败/异常 → 回滚并停止后续候选。

没有 plan 的候选不会执行；调用方必须明确返回 409/needs_human，不能盲改。
"""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Callable, Optional

from upi.core import events as E
from upi.core.models import EditPlan
from upi.core.probe_events import EventLog
from upi.core.ticket import BatchTicket, TicketItem, TicketStore
from upi.tooling.snapshot import Snapshot

SnapshotFn = Callable[[str, Path, Path, str], Snapshot]
ExecuteFn = Callable[..., dict]
ObserveFn = Callable[[], dict]


def _profile():
    from upi.config.project_profile import active_profile
    return active_profile()


def default_scene() -> Optional[Path]:
    p = _profile()
    if not p.fix_subject:
        return None
    try:
        return p.scene_of(p.fix_subject)
    except Exception:  # noqa: BLE001
        return None


def default_snapshot(attempt_id: str, project: Path, scene: Path, fp: str) -> Snapshot:
    from upi.tooling import snapshot as SN
    return SN.snapshot(attempt_id, project, scene, fp)


def default_execute(plan: EditPlan, snap: Snapshot, *, project_path: Path,
                    scene_path: Path, approval_note: str) -> dict:
    from upi.agents import fixer
    return fixer.execute(plan, snap, project_path=project_path,
                         scene_path=scene_path,
                         human_approved=True, approval_note=approval_note)


def default_observe() -> dict:
    """常驻观测：只报“当前事件流最近有什么”，不做复现验证。"""
    evs = EventLog().tail(10, compat=True)
    return {
        "events": len(evs),
        "last_kinds": [e.kind for e in evs[-5:]],
        "last_metrics": [e.metric for e in evs[-5:]],
        "verification": "unverified",
    }


class UnityTicketExecutor:
    def __init__(self, store: TicketStore, *,
                 snapshot_fn: SnapshotFn = default_snapshot,
                 execute_fn: ExecuteFn = default_execute,
                 observe_fn: ObserveFn = default_observe,
                 project: Optional[Path] = None,
                 scene: Optional[Path] = None):
        self.store = store
        self.snapshot_fn = snapshot_fn
        self.execute_fn = execute_fn
        self.observe_fn = observe_fn
        self.project = project or _profile().abs_project
        self.scene = scene if scene is not None else default_scene()

    def _keep_commit(self, snap: Snapshot, ticket: BatchTicket, item: TicketItem) -> None:
        """把已 applied 的 patch 提交，保证下一 patch 的快照仍然干净。"""
        raw_root = getattr(snap, "root", None)
        if not raw_root:
            return  # 测试替身/无 git 快照：没有可提交的根
        root = Path(raw_root).resolve()
        try:
            rel = self.project.resolve().relative_to(root).as_posix()
        except ValueError:
            rel = "."
        subprocess.run(["git", "add", "--", rel], cwd=str(root),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", check=False)
        subprocess.run(["git", "commit", "-m",
                        f"[upi-keep] {ticket.ticket_id} {item.item_id} {item.pattern_id}"],
                       cwd=str(root), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", check=False)

    def execute(self, ticket: BatchTicket) -> BatchTicket:
        if ticket.status != "approved":
            ticket.results.append({"error": f"未批准（{ticket.status}）"})
            self.store.save(ticket)
            return ticket
        if self.scene is None:
            ticket.results.append({"error": "无 fix_subject/scene，拒绝执行"})
            ticket.status = "done"
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
            attempt = f"{ticket.ticket_id}-{item.item_id}"[-80:]
            fp = f"resident:{getattr(_profile(), 'profile_id', 'unknown')}:{item.cluster_id}"
            try:
                snap = self.snapshot_fn(attempt, self.project, self.scene, fp)
                rep = self.execute_fn(
                    plan, snap, project_path=self.project, scene_path=self.scene,
                    approval_note=f"batch {ticket.ticket_id} approved by {ticket.approver}",
                )
                st = str((rep or {}).get("status") or "")
                if st == "applied":
                    self._keep_commit(snap, ticket, item)
                    item.status = "applied_unverified"
                    item.observation = self.observe_fn() or {}
                    ticket.results.append({
                        "item_id": item.item_id, "metric": item.metric,
                        "status": item.status, "observation": item.observation,
                    })
                elif st == "compile_failed_rolled_back":
                    item.status = "rolled_back"
                    item.error = "compile_failed"
                    stopped = True
                else:
                    item.status = "failed"
                    item.error = f"{st}: {str((rep or {}).get('why') or '')[:160]}"
                    stopped = True
            except Exception as e:  # noqa: BLE001
                item.status = "failed"
                item.error = f"{type(e).__name__}: {e}"[:200]
                stopped = True
        ticket.status = "done"
        self.store.save(ticket)
        return ticket
