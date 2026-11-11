# ADR-0033：verified 目标缺口——frame-time breach 不可分

- 状态：**未完成/阻塞**（2026-11-11）
- 关联：ADR-0029、ADR-0030、ADR-0032

## 目的

验证能否用探针现有 `thr:<ns>` 的 frame-time breach 做“修复前有、修复后无”
的 verified 目标。

## 实测

在缺陷态夹具与修复态夹具上各跑一次真实 replay（2s 采样，重编译 + 重装探针）：

| 指标 | 缺陷态 | 修复态 |
|---|---:|---:|
| `Memory/GcAllocInFrameBytes` | **77,519** | **8,943** |
| `Render/CpuTotalFrameNs` | 2,350,950 | 2,243,600 |
| `frame_time_ms` 中位 | 2.3728 | 2.7558 |
| 2,000,000ns 阈值 breach 数 | **79** | **63** |

证据：`runs/measure_frame_235636.json`
脚本：`scripts/measure_replay_frame.py`

## 结论

- **GC 分配可分**：缺陷态约为修复态的 8.7 倍，是强目标；
- **frame-time 不可分**：缺陷态中位反而更低，2ms 阈值下两边都大量 breach；
- 因此现有 `thr` frame-time breach **不能**作为 verified 目标；
- 需要给探针加 **GC 分配越界 detector**（或带预算线的 sample 异常检测），
  才能做“修复前 breach、修复后无 breach”的真实 verified。

## 不做什么

- 不把 frame-time breach 的数量差当 verified（噪声/方向不对，违反 I1）；
- 不伪造 breach 事件；
- 不把 sample 候选当 finding（ADR-0032）。

## 下一步

1. 探针增加 `gcthr:<bytes>`（或 `budget:gc_alloc`）与 breach metric；
2. 缺陷态 3 次 replay → 修复 → 第 4 次 → `verify_after`；
3. 只有目标 breach cluster 消失、无新 cluster、守护不劣化才记 verified。

## 更新（2026-11-12）：gcthr 已实现，但热重载仍卡住

- `probe/upi_probe_v4.cs` 已加 `gcthr:<bytes>`，产出 `metric=gc_alloc_bytes`
  的统一 breach；`probe_control.install` 先 unload、删心跳再 eval，避免
  “旧心跳新鲜导致 Main 直接 return”。
- 但真实 Editor 上仍出现 **旧回调残留/控制无响应**：`gcthr` 返回
  `ERR cmd`，`status` 偶尔 `ERR no-response`；重新 eval 不能稳定替换回调。
- 这是 I9“旧回调无法卸载”的又一次现场复现；需要**重启 Editor**清残留后
  再验证，而不是继续在同一 Editor 里热换。
- 因此本轮**没有** verified 证据；GC detector 代码已提交，但真实闭环待重启后做。

## 更新（2026-11-12）：GC detector 已跑通，但固定态仍有零散 GC 尖峰

Editor 重启 + 清 stale command 后，`gcthr` 生效（`status` 含 `gcThr`），
缺陷/修复各跑一次：

| 指标 | 缺陷态 | 修复态 |
|---|---:|---:|
| `Memory/GcAllocInFrameBytes` 中位 | 77,519 | 8,943 |
| `gcthr:100000` breach 数 | **91** | **20** |
| `gc_alloc_bytes` 中位 | 77,519 | 299,669（尖峰） |

结论：

- GC detector 真实可用；
- 但修复态仍有 **20 次 >100KB 的 GC 尖峰**（中位 299KB），
  目标 breach cluster 不会消失，**不能直接 verified**；
- 需要更高阈值/更严格的目标（如连续窗口、per-frame 分布、排除 GC 尖峰）
  或把 GC 尖峰当成新的 guard 问题；
- 不把 91→20 当 verified（不满足“目标消失”）。

证据：`runs/measure_frame_001352.json`。
