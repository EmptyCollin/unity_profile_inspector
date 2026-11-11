# ADR-0010：Unity 执行适配（I3b-2a）

- 状态：执行适配完成；live/抽检调度与真实 plan_provider 待 I3b-2b（2026-11-11）
- 关联：ADR-0008、`upi/core/unity_exec.py`

## 背景

I3a 的 `TicketExecutor` 只有注入口；I3b-1 能装探针/开采样。现在要把
“已批准工单 → 真实 Unity 写入 → 编译门禁 → 失败回滚 → unverified 留痕”
接起来。不能盲改：没有 EditPlan 的候选必须停。

## 决策

新增 `UnityTicketExecutor`：

- 每个 pending patch 前 `snapshot.snapshot()`（G4）；
- 调 `fixer.execute(plan, snap, human_approved=True, approval_note=...)`：
  - `fixer` 内部负责权限闸、diff 精确中止、`recompile` 编译门禁、失败回滚；
- `applied` → `applied_unverified` + `default_observe()`（只读事件流，不做复现验证）；
- `compile_failed_rolled_back` → `rolled_back` + 停止后续；
- 其他 `fixer` 状态/异常 → `failed` + 停止；
- 无 `fix_subject/scene` → 拒绝执行；
- 成功路径**没有 verified 分支**（G21）。

Web：

- `POST /api/tickets/{id}/execute` 从 501 改为真实执行适配；
- 没有 `pending + plan + auto` 候选时返回 **409**，不盲改；
- 工单页只在 `approved && pending>0` 时显示“执行”。

## 验证

- `scripts/test_unity_exec.py`：12 项（snapshot 先于 execute、成功 unverified、
  编译失败 rolled_back、异常 failed、失败后 skipped、无 scene 拒绝、未批准拒绝、
  无 plan 不执行）。
- `scripts/test_webui.py`：34 项（`/execute` 无 plan 返回 409）。
- 全量离线：28 套。
- **未做真机写入**：默认 `plan_provider` 仍为空，夹具没有 pending plan，
  所以 `/execute` 只能走到 409；这是安全边界，不是已完成自动修复。

## 边界

- **没有真实 plan_provider**：CPU 模板/LLM 生成尚未接入；默认候选全 `needs_human`。
- **没有 live/抽检调度**：不会自动定时建单；当前靠 `/api/tickets/build` 手动触发。
- 观察只读事件流，不是回放验证；`applied_unverified` 不得进成功率分母。
- `fixer.execute` 的 `human_approved=True` 来自批量工单批准；批量批准仍受
  `TicketExecutor` 的 item tier 闸约束（semi/blocked 不 pending）。
- 真实 Unity 执行需要干净工作区；脏区会被 `snapshot()` 拒绝（G4/I4）。
