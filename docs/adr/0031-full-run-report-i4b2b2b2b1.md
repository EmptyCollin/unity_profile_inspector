# ADR-0031：完整回归报告（I4b2b2b2b1）

- 状态：已完成（2026-11-11）
- 关联：ADR-0018、`upi/core/regression_report.py`

## 背景

回归 HTML 只有 runs/plan/verdict/guards 四块；没有形态/验证等级、
全量守护汇总、G20 不可检出清单，也没有机器可读 JSON 报告。

## 决策

`build_report()` 增加：

- `report_version="regression-run-report/1"`；
- `mode="regression"`；
- `verification_status ∈ verified|unverified`（按 verdict 推导，不伪造）；
- `guard_metrics`：所有 run 的守护指标并集；
- `blind_spots`：G20 五项（GPU 归因、未放大代码、感知、记忆体、batches）。

`render_html()` 增加：

- 形态/验证等级/守护指标数；
- runs 表增加“守护数”列；
- “不可检出清单”章节；
- 末尾明确“sample 候选不是 finding”。

`write_json_report()`：写 `reports/<suite>.json`，与 HTML 同源。

## 验证

- `test_regression.py`：**40 项通过**（新增形态/等级/G20/HTML/JSON）。
- `test_webui.py`：**49 项通过**。
- 全量离线：30 套（下一轮复跑确认）。

## 边界

- 这是报告完整性，不是 verified 闭环；没有 verdict 时
  `verification_status=unverified`。
- `blind_spots` 是固定 G20 文案，不是本项目自动推导。
- 没有把 JSON 报告接进 Web 下载入口；HTML 仍走原路径。
- sample 候选仍在计划中，报告明确标注“不是 finding”。
