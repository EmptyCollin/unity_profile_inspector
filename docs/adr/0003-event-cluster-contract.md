# ADR-0003：事件 / 聚类 / 指纹 / 批量工单契约

- 状态：已接受（I0，2026-11-11）
- 关联：ADR-0001、ADR-0002

## 决策

后续实现以这些实体为骨架；能复用现有 `models.py` 的字段就复用，不另起一套。

```text
Project, ProbeSession, ReplayCase, TestTarget
ProbeEvent(kind=breach|sample, rel_frame, metric, value, ring, a4, protocol_fp)
IssueCluster(cluster_id, metrics, rel_range, shape, occurrences, reproduction_rate)
Fingerprint(features, pattern_id, root_cause, playbook_id, confidence, stats)
BatchTicket(id, mode, items[], status)
TicketItem(cluster_id, hypothesis, edit_plan, permission_tier, rollback)
PatchAttempt(ticket_id, patch, snapshot, safety, observation, decision)
Observation(metrics, window, logs, new_errors)
Verdict(mode, verification=unverified|observed|verified|failed, target, guards, new_clusters)
```

## 关键规则

1. **cluster 是修复/验证的唯一单位**，不是单条探针事件。
2. **同一 replay 内多问题先聚类再分类**：指标 + 相对帧 + 波形 + 共现；
   分不清时标 `ambiguous`，不强行归因。
3. **批量工单可以列多个候选，但一次只落一个 patch**；每步 snapshot →
   编译/冒烟 → 观测 → keep/rollback。
4. **常驻结论只能 `unverified`**；回归结论才可 `verified`。
5. **统计分账**：`mode` + `verification_status` 是一等字段；`unverified`
   不进成功率分母。
6. **指纹是路由，不是证据**：命中可跳过 LLM，但修复仍走权限/验证/回滚。

## I0 的落地边界

本 ADR 只冻结契约名与不变式；字段的最终 pydantic 模型在 I1/I2 随实现补齐。
I0 不实现事件总线、聚类器、指纹库和批量工单，避免“文档先行、代码空转”。
