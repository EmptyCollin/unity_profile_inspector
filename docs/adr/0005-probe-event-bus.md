# ADR-0005：统一探针事件总线（I1a）

- 状态：已接受（I1a，2026-11-11）
- 关联：ADR-0003、`upi/core/probe_events.py`、`upi/core/sentinel.py`

## 背景

现状有两个注入件：

- `UpiBreachProbe`（Editor 常驻）：逐帧环缓冲 + 越界判定，写 `pb_breach.jsonl`；
- `sampler_v3`（eval 注入）：`ProfilerRecorder` 采样，写 `data.json`。

消费方各自读各自的文件，Web 控制台只能看到守护事件，看不到探针事件。I1
要把它们收成一条事件流，但**不在同一步重写 C#**——物理合并要动 Unity 回调/
句柄/生命周期，必须真机验证。

## 决策

先做 **Python 侧统一事件总线（I1a）**，C# 物理合并放 I1b：

```text
ProbeEvent:
  kind = breach | sample
  session_id, source, data_tier, protocol_fp, replay_seed
  frame_idx, rel_frame
  breach: metric, value, unit, threshold, deviation_ratio, ring_*, a4_window
  sample: metric, series, stats, metrics
  raw_ref, meta
```

落盘：`runs/_probe/probe_events.jsonl`（JSONL 追加；单生产者）。
兼容层：

- `from_breach_sample(BreachSample)` → breach 事件；
- `events_from_a2(data.json)` → 每个 series 一条 sample 事件；
- 旧 `pb_breach.jsonl` / `data.json` 不废，逐步迁移消费方。

1Hz 哨兵：`upi/core/sentinel.py` 的 `compare(probe_median, poll_median)`。
比值落在 `[0.2, 5.0]` 才通过；**零对零不判通过**（无信息量，fail-loud）。

Web 控制台：`/api/status` 增加 `probe_events`（裁剪 series/ring/A4，只给摘要）
与 `probe.events_total/by_kind/bad_lines`；首页事件流合并 probe + daemon。

## 后果

- 消费方（巡检、UI、后续聚类）先统一到 `ProbeEvent`，不再各读各的文件。
- 坏行计数进 `bad_lines`，不静默丢样本。
- I1b 只改生产端：一个回调、一个心跳、同时写 breach/sample；消费端不再动。
- 物理合并前，两个旧心跳仍存在；Web 控制台只认 `pb_hb` 作为探针在线判据，
  `a2_hb`/`heartbeat.txt` 在 I1b 合并后退役。

## 不做

- 不在 I1a 删除 `UpiBreachProbe`/`sampler_v3`。
- 不把 sample 事件直接送进修复循环；sample 只产候选（ADR-0001）。
- 不把 1Hz 轮询升级为数据源；它只做哨兵。
