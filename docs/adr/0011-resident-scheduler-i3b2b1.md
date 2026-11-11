# ADR-0011：常驻巡检调度（I3b-2b1）

- 状态：调度完成；真实 plan_provider / 端到端真机执行待 I3b-2c（2026-11-11）
- 关联：ADR-0008、`upi/core/scheduler.py`

## 背景

I3b-2a 能执行“已批准且有 plan”的工单，但没有自动发现新问题的循环。
常驻形态需要一个后台巡检：事件 → 聚类 → 建单；执行仍必须人工批准。

## 决策

`ResidentScheduler`：

- `tick()`：读 `EventLog` → `cluster_events` → `ResidentEngine.build_ticket()`；
- 冷却去重：cluster 签名相同且未过 `cooldown_s` 不重复建单；
- `start(interval_s)` / `stop()`：daemon 线程，间隔默认 60s，最小 5s；
- 异常只写 `last_error`，线程不死；
- `status()`：running/tick_count/skipped/last_ticket/last_clusters/last_error；
- **不自动执行**，也不装探针/开采样（I3b-1）。

Web：

- `POST /api/resident/start|stop|tick`；
- 首页探针卡增加“自动巡检 / 巡检一轮 / 停”，显示 tick/最近工单/错误；
- 状态 API 带 `resident_scheduler`。

## 验证

- `scripts/test_scheduler.py`：10 项（首轮建单、冷却跳过、冷却后重建、空事件、
  线程启停、异常不死、全局单例）。
- `scripts/test_webui.py`：36 项（调度状态、手动 tick）。
- 全量离线：29 套。

## 边界

- 调度只建单；没有 plan 的候选仍是 `needs_human`，不会自动修。
- 没有持久化 `last_sig`；重启后可能对同一 cluster 再建一次单（可接受，UI 可去重）。
- 没有真正的“抽检窗口”策略；sample 开关由 I3b-1 控制，调度只消费事件。
- 线程在 Web 进程内；UI 关闭即停，不是独立守护进程。独立守护留 I6。
