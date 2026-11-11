# ADR-0013：逐项批准闸（I3b-2c2a）

- 状态：逐项批准完成；真机写入待干净工程/工作区（2026-11-11）
- 关联：ADR-0008、ADR-0012、`upi/core/ticket.py`

## 背景

I3b-2c1 能生成真实 plan，但模板是 `blocked`；批量批准不能绕过权限矩阵。
需要一条**单条人工批准**路径：人看过计划/文件后，只批准这一条 patch。

## 决策

`TicketItem` 新增：

- `item_approve_token`：4 位哈希（ticket_id|item_id）；
- `human_approved / approver / approval_note`。

`approve_item(ticket, item_id, approver, note, token)`：

- 工单必须已 approved；
- item 必须有 plan，状态必须是 `needs_human/pending`；
- 校验码精确匹配；
- 通过后 `human_approved=True`、状态转 `pending`。

执行器：

- `TicketExecutor` / `UnityTicketExecutor` 的条件改为
  `tier != auto and not human_approved` → `needs_human`；
- 单条批准后，blocked/semi patch 才可执行；批量批准仍不替代这一步。

Web：

- `POST /api/tickets/{id}/items/{item_id}/approve`；
- 工单明细显示 plan 文件、diff 数、单条校验码、单条批准按钮；
- 批准需要批准人 + 批语 + 校验码。

## 验证

- `test_ticket.py`：26 项（blocked 初始 needs_human、批量批准不自动执行、
  错码拒绝、单条批准后执行）。
- `test_unity_exec.py`：14 项（单条批准后 Unity 适配执行）。
- `test_webui.py`：36 项；JS `node --check` 通过。
- 全量离线：30 套。

## 边界

- **仍无真机写闭环**：夹具工作区有未提交 Addressables 改动，`snapshot()` 会拒绝；
  且目标 scripts=blocked。逐项批准只解决“权限闸”，不解决“快照基线脏”。
- 单条批准 UI 用 prompt 收集批准人/批语/码；正式页脚校验在 I5。
- plan 预览目前只显示文件 + diff 数，不展开完整 diff；I5 补 diff 视图。
- 没有自动选择“下一个候选”；失败后仍需人工决定。
