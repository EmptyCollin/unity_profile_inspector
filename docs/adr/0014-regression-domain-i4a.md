# ADR-0014：回归形态域模型（I4a）

- 状态：域模型完成；真实 replay 执行/UI 待 I4b（2026-11-11）
- 关联：ADR-0001、`upi/core/regression.py`

## 背景

常驻形态只能给 `unverified`。要回答“到底修没修好”，必须有确定性 replay：
同协议 3 次基线、按 cluster 算复现率、修后同 replay 验证。I4a 先把这条
判定链落成可测试的域模型，不碰真实 Editor 执行。

## 决策

```text
ReplaySuite(suite_id, project, scene, replay_script, quality_tier,
            warmup_s, window_s, budget, command, protocol_fingerprint)
RegressionRun(run_id, suite_id, run_index, protocol_fingerprint,
              clusters[], event_count, status)
BaselineMatrix(suite_id, runs[], min_runs=3, stable_rate=2/3)
RegressionPlan(items[], status=ready|no_stable_cluster|insufficient_runs|protocol_mismatch)
RegressionVerdict(status, verification_status, target_present, new_clusters,
                  guard_regressions, reason)
```

- `BaselineMatrix.matches()` 复用 I2 `match_across_runs`；
- 计划只收 `is_stable` 且非 `ambiguous` 的 cluster；
- `verify_after()` 四条件：
  1. `after_protocol == baseline.protocol`；
  2. 目标 cluster 消失；
  3. 无新 cluster（after 中无法匹配任何 baseline cluster）；
  4. 守护指标不劣化（>1% 即 regressed）。
- 四条件全过才 `verified`；否则 `unverified/failed`，并带具体原因。
- `RegressionStore` 持久化套件/矩阵到 `runs/_regression/`。

## 验证

- `scripts/test_regression.py`：12 项（3/3 复现、1/3 不稳定、计划只收稳定 cluster、
  目标消失 verified、目标仍在、新 cluster、守护劣化、协议不一致、不足 3 次、持久化）。
- 全量离线：31 套。

## 边界

- **没有真实 replay 执行**：I4a 只接收已采集的 cluster；不启动 Unity/Play/回放。
- **没有 3 次真机基线**：测试用合成 cluster；真实复现率仍需 I4b。
- **没有 UI**：回归页仍是占位；I5 接矩阵/计划/verdict 视图。
- **没有接到 orchestrator**：verified 账本尚未写入正式 run 报告。
- `final=aligned` 未被当成性能结论；门禁仍走 cluster + guards + protocol。
- I3b-2c2b 的真机写闭环仍被夹具脏区/blocked 阻塞，与本 ADR 独立。
