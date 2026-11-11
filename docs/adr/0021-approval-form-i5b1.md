# ADR-0021：正式审批表单 / 执行时间线 / onboarding（I5b1）

- 状态：审批表单/时间线/引导完成；旧 TUI 删除/完整 diff 视图待 I5b2（2026-11-11）
- 关联：ADR-0008、ADR-0020、`upi/agents/webui.py`

## 背景

I5a 前审批用浏览器 `prompt`，不适合“读完再签”；工单执行只有状态表，
没有时间线；设置页有步骤但没有当前状态勾选。

## 决策

- 工单页新增 `approvalPanel`：批准人、批语、4 位校验码、确认/取消；
  - 批量批准/驳回、单条批准共用；
  - 驳回隐藏校验码；
  - 校验码预填但可改；错误显示在表单内，不弹 alert；
- `showTicketDetail` 增加“执行时间线”：从 `results` 展示 metric/status/observation；
- 设置页 onboarding：探针在线、有事件/cluster、有批量工单、有回归套件四项实时勾选；
- `BatchTicket.summary` 增加 `results`（最近 8 条）。

## 验证

- `test_webui.py`：48 项（正式审批表单/时间线容器/onboarding 容器）。
- 全量离线：31 套。

## 边界

- 审批表单没有“多步确认/审批历史”；只有当前一次。
- 计划详情仍只显示文件 + diff 数，没有完整 diff 视图。
- 时间线只来自 `results`，不是完整 attempt trace。
- 旧 ANSI TUI 仍未删除；I5b2。
- **I3b-2c2b 仍被挡**：夹具脏区 + scripts=blocked。
