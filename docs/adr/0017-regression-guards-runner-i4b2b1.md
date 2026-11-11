# ADR-0017：回归守护采集 / 失败审计 / 可注入 runner（I4b2b1）

- 状态：域模型完成；真实 Unity replay 驱动待 I4b2b2（2026-11-11）
- 关联：ADR-0014、ADR-0016、`upi/core/regression.py`

## 背景

I4b2a 只会验目标/新 cluster/协议，没采守护指标，也没保留失败 verdict；
真实 replay 驱动也没有接口。I4b2b1 先把这三块补齐，仍不启动 Unity。

## 决策

- `RegressionRun` 新增 `guards: dict`；
- `guards_from_events(events)`：sample 取 series 中位、breach 取 value，再按 metric 取中位；
- `record_run` 自动采 guards；
- `verify_last` 用 3 次基线 guards 的中位 vs after guards；
- `verify_last` **所有 verdict 都落 `verdicts/`**（不再只留 verified）；
- `summary()` 增加 `verified_count`，`last_verdict` 也显示失败；
- `RegressionRunner(suite, store, run_once)`：注入式 3 次基线采集；
  `run_once(suite) -> (protocol, events)`，真正 Unity 驱动在 I4b2b2 接。

## 验证

- `test_regression.py`：23 项（新增 runner 3 次/守护、失败 verdict 审计）。
- `test_webui.py`：41 项。
- 全量离线：31 套。

## 边界

- **没有真实 Unity replay 驱动**：`RegressionRunner` 只接受注入函数；默认无驱动。
- **守护只按事件中位**：没有 fresh-GC/记忆体专门口径，也没做全量守护选择。
- **失败 verdict 只落本地 JSON**：没有报告/UI 历史列表。
- **仍是第一个稳定 cluster**：多目标未逐个验证。
- **没有时间对齐**：relative frame 直接比。
