# ADR-0030：夹具 Unity 自动 replay 驱动（I4b2b2b2a）

- 状态：已完成（2026-11-11）
- 关联：ADR-0016、`upi/core/replay_driver.py`、`scripts/e2e_replay.py`

## 背景

`SubprocessReplayDriver` 只执行用户填的 `suite.command`，没有 Unity 自动
Play/Replay/退出；回归页要求人手填命令。I4b2b2b2 需要夹具自动 replay。

## 做法

- `UnityReplayDriver`：
  1. `unity command scene open --path <suite.scene>`；
  2. `editor.playmode start`；
  3. warmup 后 `probe_control.sample(on, window=window_s*60)`；
  4. 等 `window_s`，`sample(off)`，`editor.playmode stop`；
  5. 只取本次新增探针事件（记录 run 前事件数，切片）。
- `driver_for(suite)`：有 `command` → 子进程驱动；无 command 但有
  `project+scene` → Unity 自动驱动；否则报错。
- Web `run-baseline` 改用 `driver_for(suite)`。
- `cluster_events(merge_sample=True)`：sample 窗口按指标合并、形状取组内多数；
  `RegressionRunner` 用 `merge_gap=window_s*60`，避免每个抽检窗都成一个 issue。
- 测试注入 `cli/probe/sleep`，不碰真实 Editor。

## 证据

- `test_regression.py`：**36 项通过**（含 Unity driver 伪注入）。
- `scripts/e2e_replay.py`：**5/5 通过**，真实 3 次 replay；
  证据 `runs/e2e_replay_20261111_234629.json`。
- 每次 run 事件数 96/84/96，各 12 个 sample cluster；协议一致；
  计划 ready，12 个 sample 候选。
- 全量离线：**30 套通过 / 0 失败**。

## 证明了什么 / 没证明什么

**证明**：

- 不填 `suite.command` 也能自动打开夹具场景、进 Play、开/关探针、退出；
- 每次 replay 只取新增事件，不把历史事件算进本次；
- sample 窗口不再按每个抽检事件裂成多个 cluster；
- Web 基线入口能选到 Unity 自动驱动。

**没证明**：

- 没有 verified 闭环：夹具已是修复态，3 次基线只有 12 个 sample 候选，
  没有目标缺陷 cluster 的消失/复现；
- sample 候选是否“真异常”仍需预算线/分类；当前没有把 sample 候选自动
  升级为 issue；
- 没有完整 RunReport/全量守护选择；
- 没有真实用户工程。

## 边界

- 真实 replay 需要 Editor 可达、探针已安装、场景存在；失败如实报错。
- 采样窗口按 60fps 近似转帧；实际帧率不同会偏差。
- 三次基线是同一已修复夹具，不能当“修复前”证据。
- `sample` 仍是候选，不是 finding；不要把它当 verified。
