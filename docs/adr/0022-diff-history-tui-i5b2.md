# ADR-0022：diff 展开 / 审批历史 / 移除 TUI 入口（I5b2）

- 状态：已完成（2026-11-11）
- 关联：ADR-0008、ADR-0021、`upi/agents/webui.py`、`upi/core/ticket.py`

## 决策

- `TicketItem` 计划通过 `summary.item_list[].plan_diffs` 暴露：
  `file / _why / find / replace`（各截 400 字符，最多 4 段）；
- 工单明细“计划”列改 `<details>`，展开显示每段 diff；
- `BatchTicket.history` 记录 `approve_ticket / reject_ticket / approve_item`；
  approval/reject/item approval 都追加；Web 明细显示“审批历史”；
- 移除 `upi dev tui` 入口与 `cmd_tui`；`upi/agents/ui.py` 模块仅保留给历史测试，
  不再帮助/文档宣传；
- README/使用说明/UI 设计同步“TUI 入口已移除”。

## 验证

- `test_ticket.py`：28 项（审批历史、明细 diff）。
- `test_webui.py`：48 项。
- `test_ui_actions.py`：142 项仍通过（直接 import 模块，不走 CLI）。
- `python -m upi dev` 列表已无 tui。
- 全量离线：31 套。

## 边界

- diff 只截断展示；不是完整文件上下文审查。
- 审批历史只记动作/人/批语/时间，不记当时 diff 哈希。
- `upi/agents/ui.py` 代码未删；只移除了用户入口。彻底删除留 I6。
- 回归报告/工单时间线仍是只读展示，不是完整 trace。
- **I3b-2c2b 仍被挡**：夹具脏区 + scripts=blocked。
