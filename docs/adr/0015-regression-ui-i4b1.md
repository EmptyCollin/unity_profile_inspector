# ADR-0015：回归基线记录 UI（I4b1）

- 状态：基线记录/计划 UI 完成；真实 replay 执行/verified 账本待 I4b2（2026-11-11）
- 关联：ADR-0014、`upi/core/regression.py`

## 背景

I4a 只有域模型，用户无法在 UI 里建立 3 次基线。I4b1 把“跑完一次 replay 后
记录一次基线”接进 Web：不启动 replay，只消费探针事件。

## 决策

`RegressionStore` 新增：

- `record_run(sid, event_log)`：读当前事件 → 聚类 → `RegressionRun` → 追加矩阵；
- `list_suites()` / `summary()`：套件、协议、基线次数、稳定 cluster、计划；
- `UPI_REGRESSION_DIR` 可重定向测试目录。

Web：

- `POST /api/regression/suites` 新建套件（名称/协议）；
- `POST /api/regression/suites/{id}/record` 记录一次基线；
- 状态 API 带 `regression_suites`；
- 回归页：套件列表、基线次数、稳定 cluster、计划状态、“记录本次基线”。

## 验证

- `test_regression.py`：15 项（新增 3 次记录进矩阵/计划 ready/套件列表）。
- `test_webui.py`：40 项（新建套件、记录 3 次、计划 ready、状态含套件）。
- 全量离线：31 套。

## 边界

- **不启动 replay**：用户仍需在外部跑 replay；UI 只负责“记录当前事件”。
- **不是 3 次真机 replay**：测试用当前夹具事件；真实 replay 一致性/时间对齐未验。
- **没有 verified 账本**：目前只出计划；修后验证/写入报告待 I4b2。
- **没有防重复基线**：同一窗口重复点“记录”会追加 run；I4b2 加 run_id/时间戳去重。
- **协议靠手填/套件字段**：没有从环境自动生成完整 G7 指纹。
