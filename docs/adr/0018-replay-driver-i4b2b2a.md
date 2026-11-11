# ADR-0018：时间原点对齐 + 子进程 replay 驱动（I4b2b2a）

- 状态：通用驱动/对齐/UI 完成；夹具 Unity replay 命令与报告待 I4b2b2b（2026-11-11）
- 关联：ADR-0014、ADR-0017、`upi/core/replay_driver.py`

## 背景

I4b2b1 有注入式 runner，但没驱动、没时间对齐、UI 也不能跑 3 次。
I4b2b2a 先接一个**通用命令驱动**和**时间原点对齐**，不猜具体 Unity 命令。

## 决策

- `align_clusters(clusters)`：把 run 内最早 cluster 的 `first_rel` 归零；
  `occurrence.rel_frame` 同步平移；`cluster_id` 保持不变；
- `SubprocessReplayDriver`：
  - 执行 `suite.command`（`shlex.split`，**不经 shell**）；
  - 命令负责跑 replay/Play 并落探针事件；
  - 成功后读 `EventLog`，返回 `(protocol, events)`；
  - 失败/超时/空命令 → `ReplayDriverError`，不冒充成功；
- `RegressionRunner(..., align=True)`：每次运行后做时间原点对齐；
- Web：
  - 新建套件可填 `command`；
  - `POST /api/regression/suites/{id}/run-baseline` 跑 N 次；
  - 回归页“跑3次基线”按钮。

## 验证

- `test_regression.py`：28 项（对齐最早 cluster/保留 ID/occurrence 同步、
  subprocess driver 读事件、空命令报错）。
- `test_webui.py`：43 项（带 command 建套件、跑 3 次、计划 ready）。
- 全量离线：31 套。

## 边界

- **没有夹具专用 Unity replay 命令**：`suite.command` 是用户填的通用命令；
  没有自动注入 Play/ReplayDriver/退出 Play。
- **对齐只是原点**：不是轨迹等价证明；跨 run 时间抖动仍可能影响结论。
- **命令是本地信任输入**：无 shell，但会执行用户给的任意程序。
- **没有报告/全量守护选择**：只记录事件中位 guards；报告待 I4b2b2b。
- 仍只验证第一个稳定 cluster。
