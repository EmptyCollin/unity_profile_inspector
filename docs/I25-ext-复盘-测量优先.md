# I25-ext 11case 复盘（测量优先版）

> 上一版复盘的问题：先看 patch，再看结果。  
> 这一版先问：**这次测量有没有资格支持 needs_recheck？**
>
> run：`test_project_20261115_123247`  
> 结果：verified 3 / improved_partial 1 / needs_recheck 6 / no_plan 1

## 0. 核心结论

```text
现在这 6 个 needs_recheck
不能直接解释为“补丁没修好”
因为测量链本身不足以区分：
  patch 无效
  target 错
  window 错
  metric 错
  噪声
  语义删除
```

正确的复盘顺序是：

```text
Q1 workload 稳定吗
Q2 metric/window 对得上吗
Q3 热点能拆到 site 吗
Q4 patch 有因果贡献吗
Q5 语义/感知保住了吗
最后才问：性能有没有改善
```

当前 UPI 直接跳到 Q5：

```text
代码改完 → 跑一次 → metric 不显著 → needs_recheck
```

前面的 Q1~Q4 多数没有证据。

## 1. 测量链现状

11case 的采集是：

```text
baseline 3 次（每次完整 replay + capture）
after 1 次（完整 replay + capture）
metric = 整个 capture 的 counters 中位数
target gone = 候选 root 是否还在
```

它产生的是：

```text
聚合 counters
候选列表
一张 patch
一次 before/after
```

它没有产生：

```text
逐帧 timeline
site 级 self/inclusive time
allocation site
drawcall 归属
完整 window 标签
after 方差
```

所以对 needs_recheck：

```text
只能知道“这次 metric 不显著”
不能知道“哪个 site 导致不显著”
```

## 2. 逐 unit 的测量资格

| unit | window | target gone | metric delta | 测量资格 | 真实问题分类 |
|---|---|---|---|---|---|
| 01 | default | false | -253100 | 不足 | 微优化；无 site 归因 |
| 02 | default | true | +12861 | 部分 | 真改善，但幅度不够 |
| 03 | open_shop | false | -513500 | 部分 | 父函数 target；无子调用拆分 |
| 04 | default | false | -547900 | 不足 | 修复不完整（timeText 仍每帧） |
| 05 | default | true | -773950 | 不足 | 语义删除风险 + 无行为断言 |
| 06 | default | true | +22660 | 部分 | verified，但无 trace 复核 |
| 07 | default | true | -114.5 | 不足 | metric 选错（drawcalls） |
| 08 | open_shop | true | -1197700 | 部分 | 语义删除风险；metric 反直觉 |
| 09 | open_shop | false | +24732 | 部分 | verified 但 target root 仍在 |
| 10 | default | true | +20582 | 部分 | verified 但 plan 未完整落档 |
| 11 | — | — | — | 无 | 资产 no_plan |

`default` 不是有效 window。  
没有 window 的 metric，只能算 **unmeasured**，不能算 needs_recheck。

## 3. 重新分类

### 3.1 语义删除风险

```text
unit-05：删除整个 HintScan 块
unit-08：删除整个 payload 加载循环
```

共同点：

```text
replace 为空 / 删掉整块工作
target 消失
但 metric 没有改善，甚至更差
```

这类首先不是“性能没修好”，  
而是：

```text
行为可能变了
perception/behavior 未验证
target 消失来自“少做事”
```

在测量优先视角下：

```text
它们的 status 应先记 semantic_risk
不是 needs_recheck
```

### 3.2 metric 选错

```text
unit-07：删除 new Material(img.material)
metric 却是 drawcalls
```

删除材质实例：

```text
可能降低 GC / 内存
不必然降低 drawcalls
```

所以 `-114.5 / 31.6` 不能说明补丁无效，  
只能说明 **metric 和机制不匹配**。

### 3.3 修复不完整

```text
unit-04：缓存了 4 个文本
但 timeText 仍每帧写
```

```text
unit-01：hoist 一个乘法
但循环里 Sin + localScale 仍在
```

```text
unit-03：删一个 ForceUpdate
但 UpdateBody 子调用还在
```

这些是：

```text
patch 只覆盖机制的一部分
```

需要 site 级 cost 才能知道剩余部分值不值得修。

### 3.4 真改善但不够

```text
unit-02：target 消失，+12861
阈值 17952
```

这是唯一一个“正改善但未达标”：

```text
improved_partial 是合理状态
```

不是 needs_recheck。

### 3.5 能力缺口

```text
unit-11：oversized_resource_asset
no_plan
```

资产计划未接入，  
不是检测或测量问题。

## 4. 为什么当前测量无法定因

### 4.1 没有 self/inclusive 拆分

知道：

```text
ChromaGame.Update 热
```

不知道：

```text
Update 自己占多少
UpdateBody 占多少
Sin/localScale 占多少
ForceUpdate 占多少
```

所以 unit-01/03/04 只能靠读代码猜。

### 4.2 没有 window 归属

只有少数 unit 打印了：

```text
metric-window ... open_shop
```

其余是：

```text
window = default
```

结果：

```text
metric 可能取错动作段
target 消失和 metric 不同源
```

### 4.3 没有 after 方差

baseline 有 3 次，  
after 只有 1 次。

结果：

```text
无法判断 delta 是 patch 效果还是噪声
```

### 4.4 没有 site 级消融

当前是：

```text
先写 patch → 再跑 → 看 delta
```

没有：

```text
先测 site 成本 → 再决定改不改
```

所以 planner 只能从“可删代码”出发。

### 4.5 replay 重复不等于测量充分

同一个 replay 跑很多次：

```text
只能测同一路径的稳定性
不能测 site 成本
不能测窗口归属
不能测语义风险
```

## 5. 能力表

| 能力 | 需要什么 | 现在有什么 | 空洞导致的问题 | 具体提升 | 参考 |
|---|---|---|---|---|---|
| Workload 稳定性 | 5 次基线、root 复现率、方差 | 3 次；root 10→8 曾波动 | 不稳定 root 进入修复 | 5 次；复现率 < 阈值标 unstable | Google Benchmark |
| Trace | 逐帧 timeline、窗口、marker | 聚合 counters | 无法离线切窗口 | 一次 replay 产 timeline，多窗口离线切 | Perfetto/Tracy |
| Site 归因 | self/inclusive、call count、alloc site | 只有 hot_function | 只能改父函数/猜 site | `ProfilerMarker` + `ProfilerRecorder`；site 表 | Unity Profiler |
| Window | action→frame 区间 | 多数 default | metric 与 finding 不同源 | replay index→window；缺 window 即 unmeasured | Chrome trace |
| 噪声 | before/after 各 N 次 | after 只 1 次 | delta 无法判显著 | after 3 次；effect size；失败先标 unmeasured | A/B perf |
| 因果 | 单点 ablation / flag | 无 | patch 是否有效不可知 | runtime flags；单 site 消融 | ddmin、A/B |
| 语义 | 行为断言/感知 | 无 | 删工作也能 target gone | 空 replace/整块删除 → semantic_risk 门禁 | 语义补丁 |
| Metric | finding 绑定 metric | planner 自选 | unit-07 用 drawcalls 测 material | metric 白名单；link 绑定 | 性能回归契约 |
| 资产 | AssetEditPlan | 未接 exam | unit-11 no_plan | AssetEditPlan 接 exam | Unity Importer |
| 复盘数据 | delta/window/noise 落盘 | 只有 status/gone | 只能靠日志回看 | counterfactual 增加 delta/threshold/window/noise | 实验记录 |

## 6. 如果我在盲环境里做

```text
1. 跑 5 次同一 replay，确认 root 稳定
2. 开一次 trace：frame timeline + marker spans + counters
3. 对每个 root，列 source sites
4. 用 site timer 看 self/inclusive
5. 对 top site 做 runtime flag ablation
6. 只对“消融有收益”的 site 写结构补丁
7. 同 window A/B；after 3 次
8. 行为/感知门禁
9. 不显著 → 先查 window/噪声，再查 patch
```

我不会先读 patch，  
也不会因为 `new`/`ForceUpdate` 出现在代码里就改它。

## 7. 对 UPI 的最小改造

### P0 先让 needs_recheck 有资格

```text
counterfactual.jsonl 增加：
  metric_delta
  metric_threshold
  window
  before_n / after_n
  measurement_status
  semantic_risk
```

没有 window / 没有 after 方差：

```text
measurement_status = unmeasured
```

不是 needs_recheck。

### P1 一次 trace

```text
trace_replay
  timeline.jsonl
  site_metrics.json
  window_metrics.json
```

之后所有窗口从同一份 trace 切。

### P2 site 归因

```text
self/inclusive
call_count
alloc_bytes
drawcalls
```

### P3 runtime ablation

```text
flag 关掉一个 site
不重新编译/不重新 replay baseline
```

### P4 语义删除门禁

```text
replace 为空
删除整块循环/整块调用
→ semantic_risk
→ 行为断言 + 感知
```

### P5 资产

```text
AssetEditPlan 进 exam_batch
```

## 8. 结论

```text
这 6 个 needs_recheck
不是 6 个“修复失败”
是 6 个“证据不足/分类错误”
```

准确说法：

```text
unit-05/08：语义删除风险
unit-07：metric 错配
unit-01/03/04：修复不完整/无 site 归因
unit-02：真改善但不够
unit-11：能力缺口
```

所以下一步不是继续调 verified 阈值，  
而是让 UPI 先具备：

```text
一次 trace
site 归因
window 绑定
after 方差
语义门禁
```

否则下一轮 11case 仍然只能得到一堆无法解释的 needs_recheck。
