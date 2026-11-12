"""常驻形态的最小装配（I3a）：事件 → 聚类 → 批量工单。

不负责调度/采样/Unity；那些在 I3b 接 UI/守护。这里只把“当前事件”
变成一张可批准的批量工单，所有结论保持 `unverified`（未执行/未验证）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from upi.core import events as E
from upi.core.cluster import IssueCluster, cluster_events
from upi.core.fingerprint import FingerprintStore
from upi.core.plan_provider import default_plan_provider
from upi.core.probe_events import EventLog
from upi.core.ticket import (BatchTicket, PlanProvider, TicketStore,
                             build_ticket)


class ResidentEngine:
    def __init__(self, *, event_log: Optional[EventLog] = None,
                 fp_store: Optional[FingerprintStore] = None,
                 ticket_store: Optional[TicketStore] = None,
                 knowledge_store=None):
        self.event_log = event_log or EventLog()
        self.fp_store = fp_store or FingerprintStore()
        self.ticket_store = ticket_store or TicketStore()
        if knowledge_store is None:
            from upi.core.knowledge import KnowledgeStore, default_path
            knowledge_store = KnowledgeStore(default_path())
        self.knowledge_store = knowledge_store

    def clusters(self, *, merge_gap: int = 30) -> list[IssueCluster]:
        events, _bad = self.event_log.read_all(compat=True)
        return cluster_events(events, merge_gap=merge_gap)

    def latest_events(self, n: int = 20):
        return self.event_log.tail(n, compat=True)

    def build_ticket(self, *, plan_provider: Optional[PlanProvider] = None,
                     limit: int = 8) -> BatchTicket:
        cs = self.clusters()
        provider = plan_provider if plan_provider is not None else default_plan_provider
        t = build_ticket(cs, fp_store=self.fp_store,
                         plan_provider=provider, mode="resident", limit=limit,
                         knowledge_store=self.knowledge_store,
                         previous_notes=self.ticket_store.recent_change_requests())
        self.ticket_store.save(t)
        return t
