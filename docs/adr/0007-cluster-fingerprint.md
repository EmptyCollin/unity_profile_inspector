# ADR-0007：事件聚类与指纹库（I2）

- 状态：已完成（2026-11-11）
- 关联：ADR-0005、`upi/core/cluster.py`、`upi/core/fingerprint.py`

## 背景

I1 产出了统一事件流，但消费方仍把“一次探针响”当一个问题。一个 replay/巡检里
可能有多个相同或不同问题；不聚类就无法回答“修的是不是同一个病”“跨 run 有没
有复现”。同时，每次都让 LLM 归因会拉高成本。

## 决策

### 1. 聚类单位

```text
ProbeEvent → (metric, shape) 分桶 → 按相对帧 merge_gap 合并 → IssueCluster
```

- 波形：`spike / step / periodic / cluster / unknown`；
- `periodic`：≥3 次且间隔 CV < 0.2；
- `cluster`：≥3 次且跨度 ≤30 帧；
- `cluster_id = hash(metric|shape|first_rel//30)`；
- 修复/复现率/验证都以 `IssueCluster` 为单位。

### 2. 跨 run 复现率

- 以第 1 个 run 为参照，后续 run 按 `metric + shape + data_tier` 精确匹配，
  `first_rel` 容差默认 ±15 帧；
- `reproduction_rate = 命中 run 数 / 总 run 数`；≥2/3 标 `is_stable`；
- 后续 run 独有的 cluster 也进表（0/N 或部分复现），不能“没看见就丢掉”。

### 3. 指纹库

```text
FingerprintFeatures(metric, shape, kind, data_tier, rel_bucket)
FingerprintRecord(pattern_id, root_cause, playbook_id, confidence,
                  n_hits, n_misses, n_success, n_fail, source, status)
```

- SQLite：`knowledge/fingerprints.db`（可用 `UPI_FINGERPRINT_DB` 重定向）；
- 内置 6 条种子：GC/渲染/动画/加载/帧时；`rel_bucket=-1` 表示只看机制不看时刻；
- 命中 → `llm_required=False`；未命中/两个命中分差 <0.05 → `llm_required=True`；
- 指纹是**路由**，不是证据；命中仍走权限/验证/回滚；
- 命中/未命中/成功/失败都计数，用于“LLM 调用数 / 100 事件”“命中后成功率”。

### 4. UI

- “问题”页展示 cluster 表（指标/波形/相对帧/次数/数据档/值）；
- “指纹库”页展示 pattern、来源、命中、成功/失败、最后命中；
- 都是只读展示；不提供改阈值/改权限入口。

## 验证

- `scripts/test_cluster_fingerprint.py`：23 项通过（run 内/跨 run/周期/容差/
  指纹命中与未命中/持久化/计数）。
- `scripts/test_webui.py`：26 项通过（cluster 摘要、指纹库状态）。
- `python -m upi ui --check`：`clusters=12`、`fingerprints.total=6`、`rc=0`。
- 全量离线套件：**25 套**（新增本测试）。

## 边界

- 跨 run 目前只用第 1 个 run 作参照；多参照/全局最优匹配留到 I4。
- `sample` 的 merge_gap 由调用方定；窗口很长时可能把不同问题合并，需要形状/共现
  进一步区分（I2 只做到波形 + 时间）。
- 指纹命中还没有接到修复执行器（I3）；当前只做路由与统计。
- 学习/人工教一次还没有 UI 入口；learned 记录可手工/后续 API 写。
