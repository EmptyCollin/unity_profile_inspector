# 待批准工单 APR-eaab522a2f

**为什么不是你自动就能落地**：夹具脚本 / 管线自身代码属 blocked 档（§3 维度一：永远人工），管线无自动执行权
（档位 `blocked`；I3——修改权限只来自权限矩阵，不来自模型自我判断）

## 1 证据（数值全部来自采样，本工单不生成任何数字）
| 编号 | 来源 | 实测值 | 噪声带 | 数据档 |
|---|---|---|---|---|
| e0 | unity diagnostics + a2 sampler (m1_detect) | 76947 B/frame | ±0 | A2 |

症状签名：`{'dim': 'gc_alloc_per_frame', 'metric': 'gc_alloc_per_frame', 'mechanism': 'per_frame_heap_alloc_in_hot_path', 'threshold': 100000.0, 'threshold_source': 'noise_calibration'}`
量级 Δ=68576，偏离倍数=9.192

## 2 方案（playbook `PB-FULL-REUSE`）
语义保持断言：
- state/level 仅 Awake 赋值（静态核对）
- map[key]=level;score=map[key] ⇒ score=level 恒等
- _cooldownMap 声明保留（对外语义不破坏），改指向复用缓冲
- P2 语义断言基建 pending（M7）——本条如实标注

**改动 0**（缓冲/组件/键 预生成（Awake 扩展））→ 对应证据 e0
```diff
--- a/fixtures/upi-fixture/Assets/UpiFixtures/F_CPU_01/CombatSkillManager.cs
+++ b/fixtures/upi-fixture/Assets/UpiFixtures/F_CPU_01/CombatSkillManager.cs
@@ 原文 7 行 → 新文 26 行
```
- 命中要求：`expect_count=1`（不精确命中即整单中止，无半成品）
**改动 1**（Update 热路径分配清零）→ 对应证据 e0
```diff
--- a/fixtures/upi-fixture/Assets/UpiFixtures/F_CPU_01/CombatSkillManager.cs
+++ b/fixtures/upi-fixture/Assets/UpiFixtures/F_CPU_01/CombatSkillManager.cs
@@ 原文 27 行 → 新文 12 行
```
- 命中要求：`expect_count=1`（不精确命中即整单中止，无半成品）

副作用声明：`none`
触碰资产：fixtures/upi-fixture/Assets/UpiFixtures/F_CPU_01/CombatSkillManager.cs

## 3 预期效应
根因：热路径每帧新建 List（可复用缓冲）（机制 anchor: per_frame_heap_alloc_in_hot_path）（归因置信度 0.94，
公式算得、非模型打分；权重版本 hand-set-prior-v1）
预期：{'metric': 'gc_alloc_per_frame', 'direction': 'down', 'min_delta': 20000.0, 'source': 'calibration:thresholds_2ba2656b6f17.json', 'note': '改善低于此线 G1 即判 not_significant（预期=判据重述）'}

## 4 回滚方式
- 快照 tag：`upi/attempt-m4-001954`
- 机制：G9：回滚只依赖 git，不依赖 Editor 可达；编译失败进 Safe Mode 时仍可按 tag 复位
- 复位范围：git reset --hard + 仅夹具路径 clean；不动：非夹具路径, Editor 内存态（重启验证）

## 5 失败预案
- 不批准：Issue 记 needs_human，保留全部证据链入库，不消耗 attempt 配额
- 批准后编译失败：G8 已实测：编译门禁失败即自动回滚，不烧采样成本
- 验证不通过：G1/G2 已实测：改善不足 k·σ 或守护劣化 → 回滚 + 进 Review，审查裁定继续/撤回/转人工（配额 3 次，G6）
- 感知门禁：G3 感知门禁失败 → 无条件回滚 + 转人工（基建 M7，当前 pending，工单不得被读成'感知已验'）

---
写决定：在同目录放 `decision.json`，形如
`{"decision": "approve", "approver": "<你的名字>", "note": "<批的具体内容>"}`
（approve 必须同时给 approver 与 note，缺一视同未批。）
