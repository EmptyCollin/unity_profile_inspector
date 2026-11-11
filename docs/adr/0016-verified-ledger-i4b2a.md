# ADR-0016：回归验证与 verified 账本（I4b2a）

- 状态：验证/账本完成；真实 replay/时间对齐/守护采集待 I4b2b（2026-11-11）
- 关联：ADR-0014、ADR-0015、`upi/core/regression.py`

## 背景

I4b1 能记录 3 次基线并出计划，但“修后到底 verified 没有”还没有入口。
I4b2a 把“前 3 次作基线、最后一次作 after”的判定与账本接起来。

## 决策

`RegressionStore.verify_last(sid)`：

- 要求 ≥4 次记录（3 基线 + 1 修复后）；
- 前 3 次建 `BaselineMatrix`，出 `RegressionPlan`；
- 取计划第一条目标 cluster，最后一次作 after；
- 调 I4a `verify_after`（协议/目标消失/无新 cluster/守护）；
- 只有 `verification_status=verified` 才写入 `runs/_regression/verdicts/`；
- `summary()` 带 `last_verdict`（只显示已验证的最近一条）。

Web：

- `POST /api/regression/suites/{id}/verify`；
- 回归页在 ≥4 次记录时显示“验证最后一次”；
- 结果弹窗显示 status/verification_status/reason。

## 验证

- `test_regression.py`：19 项（第 4 次目标消失→verified、账本落盘、
  summary 带 last_verdict、目标仍在→不 verified/不落账）。
- `test_webui.py`：41 项（第 4 次记录 + verify 返回 target_present）。
- 全量离线：31 套。

## 边界

- **不启动真实 replay**：仍靠外部跑 replay，UI 只消费事件。
- **只有第一个稳定 cluster**：多目标未逐个验证。
- **没有守护指标采集**：`guards_before/after` 未接，当前只验目标/新 cluster/协议。
- **未 verified 不留账**：只有 verified 记录；失败 verdict 只在响应里，审计留到 I4b2b。
- **没有时间对齐**：cluster 的 relative frame 直接比，未处理 replay 起步抖动。
