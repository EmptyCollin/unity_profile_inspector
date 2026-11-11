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
