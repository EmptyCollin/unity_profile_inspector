# ADR-0032：回归计划只收 breach（sample 只做守护）

- 状态：已完成（2026-11-11）
- 关联：ADR-0030、`upi/core/regression.py`

## 问题

自动 replay 只开探针 `sample` 时，每个指标都会产 sample cluster；旧
`RegressionPlan.from_matrix()` 把所有稳定 cluster 都收进计划，结果夹具
3 次基线产出 12 个“可修复项”。这违反 AGENTS §8.2/G22：
**sample 是抽检候选，不是确定越界**。

## 决策

- `RegressionPlan.from_matrix()` 只收 `cluster.kind == "breach"` 的稳定匹配；
- sample 继续用于 `guards_from_events()` / 交叉核对，不进计划；
- 没有 breach 时计划为 `no_stable_cluster`，不再伪造 12 个候选；
- 测试夹具改用 breach 事件；Web 回归测试同步。

## 验证

- `test_regression.py`：**40 项通过**（计划/verified 用 breach 夹具）。
- `test_webui.py`：**49 项通过**（事件载荷裁剪改为 ring 裁剪）。
- `e2e_smoke.py`：**10/10 通过**（breach 建单/回归）。
- `e2e_replay.py`：**5/5 通过**；3 次真实 replay，计划
  `no_stable_cluster`、0 items（只有 sample，没有 breach）——这是诚实结果。

## 证明了什么 / 没证明什么

**证明**：

- sample-only replay 不再产出假计划；
- breach 事件仍能正常进计划/verified 测试；
- 真实 3 次 replay 的集群/协议/守护仍完整。

**没证明**：

- 没有 verified 闭环：真实夹具 replay 没有 breach 目标；
- 要 verified，需要“缺陷态 + 阈值触发 breach + 修复后不再 breach”的 replay；
- 没有真实用户工程。

## 边界

- 这不是“回归退步”，是把 sample 从修复计划里剔除；sample 仍可用于
  守护和人工判断。
- 现有 12 个 sample cluster 仍在报告计划外可见，但不作为修复项。
