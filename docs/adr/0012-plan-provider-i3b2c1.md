# ADR-0012：真实 plan provider 与权限不降级（I3b-2c1）

- 状态：plan provider 完成；端到端真机写入待 I3b-2c2（2026-11-11）
- 关联：ADR-0008、ADR-0010、`upi/core/plan_provider.py`

## 背景

I3b-2b1 的巡检能建单，但没有 `plan_provider`，所有候选都是 `needs_human`。
已有真实模板只有夹具 CPU full-reuse（`nodes._plan_for`）；它产出
`permission_tier=blocked`，因为改的是夹具脚本（AGENTS §3：永远人工）。

## 决策

`plan_provider.py`：

- 只对 `profile_id == upi-fixture` 且 `metric == gc_alloc_per_frame` 生效；
- 调 `nodes._plan_for("per_frame_heap_alloc_in_hot_path")`，产出真实 `EditPlan`
  （2 段 diff，`PB-FULL-REUSE`）；
- 未知 metric / playbook / 非夹具 profile → `None`，不硬编；
- `TicketBuilder` 取 `max(playbook_tier, plan.permission_tier)`：
  **blocked plan 不能因 playbook 是 auto 而被降级执行**；
- 结果：item 有 plan，但 `needs_human` + `tier=blocked`；批量批准不绕过。

## 验证

- `scripts/test_plan_provider.py`：10 项（真实模板/diffs/blocked/未知指标/
  未知 playbook/默认 provider/plan 挂载/tier 不降级/不 pending/playbook 以 plan 为准）。
- 全量离线：30 套。

## 为什么没有做“真机端到端写入”

- 模板只改夹具 C# 脚本，目标档位是 **blocked**；按权限矩阵必须逐项人工批准，
  不能由批量工单自动执行。
- 夹具工作区当前有未提交 Addressables 改动，`snapshot()` 会拒绝建快照（G4/I4）；
  强行写入会污染回滚目标。
- 因此 I3b-2c1 的诚实终点是：**真实 plan 能生成、能挂单、被权限闸拦住**；
  真机写入需要 I3b-2c2 的干净工程 + 逐项批准流，或用户自己的非 blocked 工程。

## 边界

- 没有通用/LLM plan 生成；只有夹具 CPU 一条模板。
- 没有 per-item 批准 UI；blocked/semi 仍只能 `needs_human`。
- 没有真机写闭环证据；不要对外说“已能自动修夹具”。
