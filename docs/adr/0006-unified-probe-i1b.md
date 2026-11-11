# ADR-0006：统一探针件 I1b

- 状态：实现完成，生产接线待 I3（2026-11-11）
- 关联：ADR-0005、`probe/upi_probe_v4.cs`

## 背景

I1a 先把事件格式与消费方统一了，但生产上仍有两个注入件：

- `UpiBreachProbe`（fixture Editor 常驻）：逐帧环缓冲 + 越界；
- `sampler_v3`（eval 注入）：ProfilerRecorder 窗口采样。

两者各自心跳（`pb_hb.txt` / `heartbeat.txt`）、各自落盘，正是要合并的对象。

## 决策

新增 `probe/upi_probe_v4.cs` 作为**统一探针件**：

- 一个 `EditorApplication.update` 回调；
- 一个心跳 `runs/_probe/pb_hb.txt`（tick 时间戳；旧消费者兼容）；
- 一条事件流 `runs/_probe/probe_events.jsonl`：
  - `breach`：`kind/metric/value/threshold/ring_*/a4_window/data_tier/replay_seed`；
  - `sample`：`kind/metric/series/frame_idx/rel_frame/data_tier/source`；
- 兼容写 `pb_breach.jsonl`（旧消费方迁移期仍可读）；
- 控制走 `probe_cmd.txt`：`status / thr / seed / depth / sample:on[:window] / sample:off / clear / unload`；
- sample 默认关；上层用 `sample:on[:window]` 开，避免默认改变旧行为；
- 事件文件超过 32MB 滚动到 `.1`，避免 Web/统计每次读全文件变慢。

## 真机冒烟（2026-11-11）

用 `unity command "script eval_file" --file probe/upi_probe_v4.cs` 注入夹具：

- 编译成功；`recorders=12/13 skipped=[Render/Indices(未注册)]`（fail-loud，不装死不读零）；
- 进入 Play，`sample:on:60` → `probe_events.jsonl` 出现 sample 事件；Python
  `EventLog` 解析 0 坏行；
- `thr:2000000` → 出现 breach 事件（value/threshold/ring_after=14/a4_window 有值）；
- 冒烟留档已归档：`runs/_probe/i1b_smoke_events.jsonl`（约 2.1MB）、
  `runs/_probe/i1b_smoke_pb_breach.jsonl`；当前 `probe_events.jsonl` 已清空，
  避免测试数据污染后续会话。
- 一次 `unload` 后心跳停止（没有重复回调）；旧 `UpiBreachProbe` 在测试时已先
  `unload`，否则两者会争抢 `pb_hb`/事件文件（I9 教训）。

## 迁移边界（未完成）

- 生产安装/卸载还没接到 UI/巡检：I3 的常驻形态负责“装统一探针、开 sample、
  停旧探针”。
- `sampler_v3` 与 fixture `UpiBreachProbe` 仍保留；迁移期必须先卸载旧探针，
  再装新探针，不能并存。
- 事件文件轮转只保留 `.1`；长期统计应在 I3/SQLite 侧做索引，不能每次全量读。
- `DIR` 仍是仓库绝对路径 `D:/unity_profile_inspector/runs/_probe`；接真实工程前
  要参数化（I3）。

## 结论

I1b 的“一个回调、一个心跳、同时产 breach/sample”已用真机冒烟证明可行；
但“生产上只剩一个探针”还差 I3 的安装/卸载接线。不要把本文当成“旧探针已退役”。
