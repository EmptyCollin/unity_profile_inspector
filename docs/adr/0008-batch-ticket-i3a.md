# ADR-0008：批量工单与串行执行器（I3a）

- 状态：域模型/API/UI 完成；Unity 执行接线待 I3b（2026-11-11）
- 关联：ADR-0007、`upi/core/ticket.py`、`upi/core/resident.py`

## 背景

常驻形态需要“一次列多个候选、批准后按顺序修、每步观测/回滚”，并且不能把
`unverified` 冒充成 `verified`。I2 已有 cluster + 指纹路由，缺“工单/执行”这一层。

## 决策

### 1. 工单模型

```text
BatchTicket(ticket_id, mode, status, items[], approve_token, approver, note, results)
TicketItem(cluster_id, metric, shape, fingerprint_hit, pattern_id, playbook_id,
           permission_tier, plan, status, observation, error)
```

- 状态：`draft → awaiting_approval → approved → executing → done`；可 `rejected`。
- 候选状态：`pending / needs_human / applied_unverified / rolled_back / failed / skipped`。
- `approve_token` = 4 位哈希（ticket_id + item cluster ids）；批准要同时填批准人、
  批语、校验码；“读完再签”的弱化版，I5 接正式页脚校验。
- 批量批准**不绕过**单个 patch 的 `permission_tier`；`semi/blocked` 只能
  `needs_human`，不进自动执行。

### 2. 构建

`build_ticket(clusters, fp_store, plan_provider)`：

- 每个 cluster → 指纹路由；命中带 `pattern_id/playbook_id`；
- `playbook` 映射权限：`PB-REUSE-BUFFER` 等 auto；`PB-SHARED-MAT-MPB` 等 semi；
- `plan_provider` 是注入点：I3a 默认没有，候选标 `needs_human`（诚实边界）；
- 一次工单最多 8 个候选。

### 3. 执行

`TicketExecutor(apply/compile_ok/observe/rollback)`：

1. 只接受 `approved` 工单；
2. 一次一个 patch；`snapshot/compile/observe/rollback` 全部注入，便于离线测试；
3. 编译失败 → 回滚、该 item `rolled_back`、后续 `skipped`；
4. apply 异常 → 回滚、`failed`、后续 `skipped`；
5. 成功 → `applied_unverified` + observation；**没有 verified 分支**。

### 4. Web

- `POST /api/tickets/build`：从当前事件/聚类建单；
- `POST /api/tickets/{id}/approve|reject`：批准人/批语/校验码；
- `POST /api/tickets/{id}/execute`：I3b 前返回 501（不假装执行）；
- 工单页：列表、建单、明细（指标/指纹/playbook/权限/状态/原因）、批准/驳回。

## 验证

- `scripts/test_ticket.py`：21 项（多候选、token、错误 token、批准/驳回、串行、
  编译失败回滚、失败后停止、无 plan、semi 不自动、持久化、ResidentEngine）。
- `scripts/test_webui.py`：31 项（建单/批准/执行 501/状态含工单）。
- 全量离线：26 套（新增 `test_ticket`）。

## 边界

- **没有真实 Unity 执行**：`apply/compile/observe/rollback` 注入口未接
  `fixer/snapshot/unity`；I3b 接。
- **没有常驻调度**：`ResidentEngine` 只做“当前事件 → 工单”，不自动定时/循环。
- **没有真实 plan_provider**：默认全部 `needs_human`；不会盲改。
- 工单页用 `prompt` 收集批准人/批语/校验码，是 I3a 的最小交互；I5 换正式表单。
- 指纹学习/教一次仍未接。
