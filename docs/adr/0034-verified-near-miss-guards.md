# ADR-0034：verified 近失——目标消失但守护回归

- 状态：**未通过**（2026-11-12）
- 关联：ADR-0033、`scripts/e2e_verified.py`

## 做法

`scripts/e2e_verified.py`：

1. 临时装回缺陷态并编译；
2. 安装窗口化 GC detector（`gcthr:20000`，最近 30 帧 ≥24 帧超阈值）；
3. 3 次真实 replay 基线；
4. `PB-FULL-REUSE` 产品修复 + 编译；
5. 重装探针 + 第 4 次 replay；
6. `verify_after` 四条件。

## 结果

- 3 次基线：`plan=ready`，目标为 `gc_alloc_bytes` breach cluster；
- 修复成功、编译成功；
- 修复后 breach cluster **0 个**，目标消失；
- 但 `verify_after` 返回：

```text
status=guard_regressed
verification_status=unverified
guard_regressions=[Memory/TotalUsedBytes, Render/CpuTotalFrameNs]
```

| 守护 | 基线中位 | 修复后 |
|---|---:|---:|
| `Memory/TotalUsedBytes` | 1,409,080,685 | 1,437,873,666（+2.0%） |
| `Render/CpuTotalFrameNs` | 1,487,300 | 1,619,975（+8.9%） |
| `Memory/GcAllocInFrameBytes` | 101,221 | 9,215（大幅下降） |

证据：`runs/e2e_verified_20261112_001632.json`

## 结论

- **目标消失、GC 分配大幅下降**；
- 但全量守护里内存/CPU 总帧时超过 1% 容差，按 G2 不能 verified；
- 这不是“verified 通过”，也不能靠只报目标指标来掩盖；
- 需要先解释/消除守护回归：更长采样窗、fresh GC 内存口径、或确认是探针重装/编译后的瞬态。

## 边界

- 夹具已恢复原始字节/HEAD，工作区干净；
- 没有伪造 verdict；
- 该结果只说明“产品修复有效但守护不过关”，不说明修复无效。
