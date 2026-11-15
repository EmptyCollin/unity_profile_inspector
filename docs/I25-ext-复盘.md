# I25-ext 11case 复盘：为什么是 3 verified / 6 needs_recheck

> run：`test_project_20261115_123247`  
> replay：压缩版 paper_replay  
> baselines：3  
> 模式：`--serial`  
> 结果：verified 3 / improved_partial 1 / needs_recheck 6 / no_plan 1

## 1. 先看结果，不看感觉

| unit | target | gone | metric delta | threshold | 结果 |
|---|---|---|---|---|---|
| 01 | ChromaGame.UpdateBody | false | -253100 | 159725.6 | needs_recheck |
| 02 | ChromaGame.RefreshTiles | true | +12861 | 17952.4 | improved_partial |
| 03 | ChromaGame.Update | false | -513500 | 159725.6 | needs_recheck |
| 04 | ChromaGame.UpdateHud | false | -547900 | 159725.6 | needs_recheck |
| 05 | ChromaGame.ResolvePendingWhenIdle | true | -773950 | 159725.6 | needs_recheck |
| 06 | BoardModel.FindMatches | true | +22660 | 17952.4 | verified |
| 07 | ChromaGame.BuildBoard | true | -114.5 | 31.6 | needs_recheck |
| 08 | ChromaGame.OpenShop | true | -1197700 | 223311.1 | needs_recheck |
| 09 | ChromaGame.RefreshItemLabels | false | +24732 | 17952.4 | verified |
| 10 | ChromaGame.ResolveAllSync | true | +20582 | 17952.4 | verified |
| 11 | oversized_resource_asset | — | — | — | no_plan |

关键事实：

```text
needs_recheck 里 5 个 metric 为负
只有 unit-02 是正改善但没过阈值
target 消失不代表 metric 改善
metric 通过不代表 target root 消失
```

## 2. 每个 needs_recheck 的真实逻辑

### unit-01 UpdateBody

plan 做的是：

```text
把 Time.unscaledTime * 6f 提到循环外
```

但循环里仍然有：

```text
for r,c:
  Mathf.Sin(animTime + r + c)
  trt.localScale = ...
```

也就是：

```text
每帧 O(N²) 次 Sin + localScale 仍然存在
只省了一次乘法
```

所以：

```text
target 仍热
metric -253100
```

这不是“修不好”，是 **planner 选了微优化**，不是机制级修复。

### unit-03 Update

plan 做的是：

```text
删除 Canvas.ForceUpdateCanvases()
```

但 `Update` 仍然调用：

```text
UpdateBody()
```

它的子调用还在热路径。

所以：

```text
target root 仍是 Update
metric -513500
```

根因是 **target 是父函数，补丁只删了一个子调用**；  
但没有证据证明 `UpdateBody` 是唯一原因，  
也没有证据证明删 ForceUpdate 对当前指标有正作用。

### unit-04 UpdateHud

plan 做的是：

```text
给 round/target/score/coin 加脏检查
```

但：

```text
timeText.text = ... 仍然每帧执行
```

也就是：

```text
只缓存了 4 个文本，漏了最频繁的 timeText
target root 仍热
metric -547900
```

这是 **修复不完整**，不是“优化无效”。

### unit-05 ResolvePendingWhenIdle

plan 做的是：

```text
直接删掉整个 HintScan 块
只保留 if (busy || bonusOpen || phase != Playing) return;
```

结果：

```text
target 消失
metric -773950
```

这是最危险的一类：

```text
它不是把工作变便宜
而是把工作删掉了
```

如果没有行为断言/感知门禁，  
这类“target 消失”可能是 **语义删除**，不是优化。

### unit-07 BuildBoard

plan 做的是：

```text
删除 img.material = new Material(img.material)
```

登记的 metric 是：

```text
drawcalls
```

结果：

```text
target 消失
metric -114.5 / 阈值 31.6
```

这里有两个问题：

```text
1. 删除材质实例不必然降低 drawcalls
2. 这个补丁可能改变颜色/材质表现，但当前没有感知门禁
```

这是 **metric 选错 + 语义风险**。

### unit-08 OpenShop

plan 做的是：

```text
删除整个 8 次 payload 加载循环
```

结果：

```text
target 消失
frame_time -1197700
```

和 unit-05 一样：

```text
这是删工作，不是优化工作
```

而且 `OpenShop` 的窗口指标反而变差，  
说明当前 metric 窗口/测量也没有支撑这个结论。

### unit-11 oversized_resource_asset

```text
no_plan
```

原因：

```text
当前链路没有把 AssetEditPlan 接进 exam_batch
```

这是能力缺口，不是检测失败。

## 3. Agent 轨迹暴露了什么

### 3.1 诊断层只给“可能”

`diagnose_archive.json` 对 Update 的六因子：

```text
proximity: yes
explains_share: likely 0.7
competitiveness: likely 0.7
counterfactual: unclear 0
```

也就是说：

```text
知道它是热点
但不知道它为什么热
不知道修哪一段能降多少
```

planner 只能从源码里挑一个“看起来能删”的东西。

### 3.2 planner 偏好“可删的代码”

从 11 个 plan 看：

```text
unit-01： hoist 一个乘法
unit-03： 删除一个 ForceUpdate
unit-05： 删除整个 HintScan
unit-07： 删除 material clone
unit-08： 删除整个 payload 循环
```

共同点：

```text
改动小、容易写、容易过 invariant
不等于能改善指标
```

### 3.3 plan_repeat 没有提供有效备选

```text
多数 unit unique=1
没有“另一个机制假设”
unit-10/11 甚至没有可用 plan
```

所以 batch 不是“多方案比较”，  
只是“单方案执行”。

### 3.4 指标窗口没有真正绑定 finding

只有少部分 unit 打印了：

```text
metric-window ... open_shop
```

多数 unit 仍是：

```text
window = default
```

这意味着：

```text
metric 可能取错窗口
target 消失/不消失和指标不同源
```

## 4. 结论

这 6 个 needs_recheck 不是同一类问题，至少分四类：

```text
A. 微优化，改不到痛点
   unit-01, unit-03, unit-04

B. 语义删除，靠删工作让 target 消失
   unit-05, unit-08

C. metric 选错/窗口不对
   unit-07，以及多数 unit 的 default window

D. 能力缺口
   unit-11 no_plan（资产计划未接）
```

所以：

```text
不能简单说“检测不准”
也不能简单说“修复没效果”
```

更准确是：

```text
诊断没有给出机制级可修点
planner 只能改容易改的代码
验证又缺少窗口/语义约束
```

## 5. 下一步（不跑 11case）

按优先级：

### 5.1 先补证据，不改判定

```text
counterfactual.jsonl 记录：
  metric_delta
  metric_threshold
  window
  patch_family
  semantic_deletion 标记
```

没有这些，下一轮复盘仍然只能靠日志。

### 5.2 给 planner 加“机制级修复”约束

```text
禁止只做：
  hoist 常量
  删除一个子调用
  删除整块工作

要求：
  说明该补丁作用于哪个机制
  说明为什么该机制是当前热点的来源
  说明预期节省的量级
```

### 5.3 语义删除单独门禁

如果 `replace` 为空，或删除了整个循环/整块调用：

```text
必须：
  行为断言
  感知门禁
  或人工批准
```

否则不能进入 verified。

### 5.4 metric 由证据决定，不由 planner 随意选

```text
unit-07 用 drawcalls 衡量 material clone，明显不匹配
```

应该：

```text
按 finding link / metric 绑定
planner 只能从允许的 metric 集里选
```

### 5.5 window 必须显式

```text
没有 window → unmeasured
不允许默默用 default
```

### 5.6 资产 no_plan

```text
把 AssetEditPlan 接进 exam_batch
unit-11 才有可执行路径
```

## 6. 下一步不做的事

```text
不重跑 11case
不调低 min_improve
不把 needs_recheck 改叫 verified
不把 target 消失当性能改善
```
