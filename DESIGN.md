# DESIGN.md — 自动化性能优化管线（最小集）v0.12

> 需求条目：REQ-1~REQ-9 功能需求 / DOM-1~DOM-5 技术领域 / SCN-1 场景锚定（定义见 §0，全文按编号引用）
> 技术底座：Unity CLI 1.0.0-beta.6 + Unity 6000.6.7f1 + Python 3.14.3 + LangChain/LangGraph
> 本文与 AGENTS.md 配套：AGENTS 定义"谁能动、能动到哪"，DESIGN 定义"怎么算、怎么证"
> v0.1 变更：按 2026-11-07 已拍板的 D1～D7 修订采集层、夹具规模与里程碑；新增检测器注册表与 §12 环境事实
> v0.2 变更：新增 §7 管线自身运行统计与可视化控制台、§8 防过拟合与泛化验证；后续章节顺延编号
> v0.3 变更：D8/D9 确认通过；M1'（埋点与统计底座）与 M6'（过拟合体检表）纳入第一轮交付范围（§11 已标注第一轮/扩展位分界）
> v0.4 变更：新增 REQ-9 与 §2.3 模型服务配置层（llm_config.yaml 双表 + 首次引导 + 连通性测试 + 密钥仅环境变量引用）；D4 收口
> v0.5 变更：确认密钥注入方式=系统级持久环境变量（用户自行设置，管线不代设不收集值）；确认连通性测试时机=第一次使用时跑（D11 补充）；llm_config.yaml 同步
> v0.6 变更：配置层代码落地（§2.3.3 双通道表、注册表回退与通道门禁实测）；连通性测试实测四步全通过，model id 按服务商列表校正为 `deepseek-flash`；§12 补记 4 项实测事实
> v0.7 变更：M0/M0' 实跑——夹具工程与 Pipeline 0.10.0 就位、191 条命令面实测；新增 §2.2.1 实测修正（无头模式渲染恒为 0 ⇒ 门禁 G16 与指纹字段 `editor_mode`；回调跨 eval 存活但静态字段不存活且旧回调卸不掉；update 频率 2100/s ≠ 帧率；`get_performance` 为上一帧快照）；契约门禁自测 6/6 通过
> v0.12 变更：**范式重排并入（DP-1，D12~D17 拍板）**。① 新增 §3A 问题分类（P1 水位 / P2 发作 / P3 潜在 / P4 矛盾 / P5 感知，按**证据形态**而非按链路分）与 finding 四等级（`confirmed`/`potential`/`guard_regress`/`needs_human`）——核心修正：旧 §2.1 把"现在什么水平""有没有发作""藏着什么雷"三个问题混在秒级轮询一个仪器里，现拆为**水位仪(A) / 发作轨(探针+A4) / 静态轨(C+)**；② 新增 **A4 档**（`FrameTimingManager` 逐帧 CPU/GPU 计时，实测编辑态 Play 得 gpu≈1.5~2.2ms 真值，**固定 4 帧延迟 + 部分帧时间戳未回填须按 -1 哨兵同规则拒收**）；③ Frame Debugger 程序化取数经全套入口实测**判定不成立**（只能取到 SetPass 分组行，逐笔明细仅 GUI 重绘时构建），GPU 发作口径改为"**检出可行（A4）、逐笔归因转人工**"；④ 新增门禁 **G18 finding 等级归属 / G19 逐帧源与归因源 / G20 盲区显式声明**；⑤ 新增 §2.4 识别时机 T0~T5 与 §3A 问题分类（类型×链路×工具的可用性矩阵见 `docs/DP-1` §3，本轮未复制入正文以免双源漂移），`potential` 不进修复循环与任何分母（其断言在当前规模下不可证伪）；⑥ D15 压测放大档按用户指定改为 **10×/100×**（原建议 2×/4× 过温和）；⑦ 里程碑插入 M2a（T2 可复现回放 + T1 环形缓冲）、M3.5（T3 代码模式扫描 + T4 斜率投影）
> v0.11 变更：M1' 完成——阈值改用**窗口间 σ**（n=4 重复窗口）取代窗口内 MAD；检测与阈值同源于 `calibration/multiwin_full.json`（杜绝跨协议混比）；三层会话（none/idle/full）归因 probe_overhead，节流后合计 0.46%~1.83% 入 5% 预算；实测发现采样器每帧心跳写盘把 CLEAN 的分配基线抬高 50%（观察者污染被观测量）；G17 补 `MAD==0 即退化` 规则（小样本下比例判据永不触发）；CLEAN 自检 FP=0，检出 4/4
> v0.10 变更：M1 完成——双源基线（A轮询+A2逐帧含动画管线 marker）、CLEAN 统一基底、阈值全量来自标定（缺项即中止）、四链路检出 4/4；实测教训"指标必须贴机制"入 §12
> v0.9 变更：实验4 定论——**双源采集架构定型**（渲染计数唯一通路=1Hz 轮询；GC/内存=按帧注入采样 handle 构造，实测真值；离线 .data 补 marker 时间线）；档位重编号 A/A2/A3；§2.2.1 增事实 7/8
> v0.8 变更：**A 档采集通路更换**——注入式 `ProfilerRecorder` 取不到渲染读数（构造成功但恒为 0），主力改为轮询内置 `diagnostics get_performance`（~1.05 次/秒 ⇒ 秒级抽样，统计口径帧→秒并新增 `poll_rate`）；§2.2.1 扩为六条；新增方法学陷阱（静止场景噪声带退化 ⇒ `k·σ` 失效）作为 M1 交付判据；幂等判据改用心跳文件新鲜度

---

## 0. 需求条目编号（全文引用口径）

本 demo 的独立工程需求，编号定义如下，正文一律按编号引用：

| 编号 | 需求条目 | 在 demo 中的落点 |
|---|---|---|
| REQ-1 | 全面覆盖 CPU、GPU、内存、加载四条关键性能链路 | §1 四条链路夹具 |
| REQ-2 | 建立性能基线与日常监控体系 | §3 基线与噪声、§1.1 检测器注册表 |
| REQ-3 | 基于 Unity 引擎的针对性优化与引擎/运行时问题深入排查 | §2.1 采集层、§11 M8（IL2CPP/Mono 面） |
| REQ-4 | 运用 Profiler / Frame Debugger / Perfdog 类专业工具精准定位瓶颈 | §2.1 A 档计数器采样、§2.2 截图证据、§9 真机边界 |
| REQ-5 | 设计并落地 AI Agent，构建"发现问题-分析-修复-验证"的自动化或半自动化可复用流程 | 整条管线（本文核心） |
| REQ-6 | 沉淀优化与 AI 工具经验，持续提升 | §6 特征库与正反馈循环 |
| REQ-7 | 管线自身作为被度量对象：记录每次运行的成本与质量元指标，并提供可视化历史统计控制台 | §7 管线运行统计与 Dashboard |
| REQ-8 | 防止特征库/权重/阈值对夹具与历史样本过拟合，升维决策必须通过留出集验证 | §8 防过拟合与泛化验证 |
| REQ-9 | 模型服务可配置：服务商与 Agent 参数两张表、首次使用引导、连通性测试、密钥只存环境变量 | §2.3 模型服务配置层 |
| DOM-1 | Draw Call / Batch | `F-GPU-01` |
| DOM-2 | Shader（含 URP） | `F-GPU-01` |
| DOM-3 | 动画 | `F-ANIM-01` |
| DOM-4 | 资源加载 | `F-LOAD-01` |
| DOM-5 | 内存与 GC | `F-CPU-01` |
| SCN-1 | 动作/战斗类场景锚定 | 全部夹具共用战斗场景 |

---

## 1. 需求覆盖度：把"领域"落成"可复现的缺陷"

核心技术领域（DOM-1~DOM-5）：Draw Call/Batch、Shader、动画、资源加载、内存与 GC；再加引擎排查面（REQ-3，含 URP 与 IL2CPP/Mono）与动作/战斗场景锚定（SCN-1）。

**已定：四条链路全做**，按"链路"覆盖而不是按"数量"覆盖——每个链路一个，正好对上 REQ-1 里的 CPU / GPU / 内存 / 加载：

| 链路 | 夹具缺陷（Fixture） | 特征性 | 修改是否影响感知 | 权限路径 |
|---|---|---|---|---|
| CPU / GC | `F-CPU-01` 战斗技能管理器：每帧 `new List<>` + LINQ + `GetComponent` + 枚举 `ToString` → 分配率尖峰、GC Gen0 频繁 | DOM-5、SCN-1 | **否**（语义保持） | **auto** 试验田 |
| GPU / 渲染 | `F-GPU-01` 同屏大量可合批材质因逐实例 `Material` 实例化 + 一个过度复杂的 URP Shader → Draw Call 与 SRP Batcher 中断、Overdraw | DOM-1、DOM-2 | **是**（改材质/Shader 会动画面） | **semi** |
| 动画 | `F-ANIM-01` Animator 控制器分层失控 + 每帧跨层控制器查询 + Update 中手算骨骼 | DOM-3、SCN-1 | **是**（改状态机/混合=改手感） | **semi** |
| 加载 | `F-LOAD-01` 场景进入时同步 `Resources.Load` 全量预热 + 无地址分组 → 首帧卡顿与峰值内存 | DOM-4、REQ-1 | 部分（改异步动时序） | **semi / blocked** |

四条全做的代价是**四条链路各自都要有稳定采样与自动判定能力**，因此采集层和检测器注册表（§1.1）必须先做扎实，否则第 4 条链路会退化成"只有 CPU 链路能自动闭环、其余靠人看"。这个代价是自愿承担的，写在这里以免后面被当成偷工减料。

### 1.1 检测器注册表（detector registry）

四个 Issue 不应该是四段散代码，而应是一个可扩展声明结构——这也是 REQ-5"可复用流程"在代码层面的落点：

```python
Detector = {
  id, link,                    # cpu | gpu | anim | loading
  counters: [...],             # 需要的采集计数器名（ProfilerRecorder 计数器 / 静态扫描项）
  signature: {...},            # dim | metric | 归因对象模式 | 阈值
  thresholds: { k_sigma, min_magnitude, deviation_ratio, explains_share },
  anchor_patterns: [...],      # 代码锚点模式（供归因邻近性与历史命中率用）
  playbooks: [...],            # 候选修法，各自带 side_effect 默认档
  default_tier: auto|semi|blocked,
  version: "...",              # 进入误报率统计的分桶键
}
```

误报率按 `detector.id + detector.version` 分桶，才能回答"是不是越用越好用"（你的第 3 条）。

**注册表两条新增约束（v0.12，G19）**：① 每个 detector 必须声明 `finding_kind: episode|waterline|potential|conflict`，判"发作（episode）"的必须有逐帧源（`data_tier ∈ {A2, A4, probe}`）——渲染计数没有逐帧源，故**禁止注册"GPU 逐笔 spike detector"**，GPU 发作 detector 只能建在 A4 的 `gpuFrameTime` 上（判"越线事实"）；② episode 类 detector 附带 `attribution_source` 字段，无自动化归因源时填 `human`（GPU 逐笔归因当前状态），finding 只给"哪帧、哪个指标、越了多少"，根因段转人工——**宁缺毋假，虚报检出能力按 I1 处理**。

---

## 2. 架构总览

```
                    ┌──────────────────────────────────┐
                    │  Orchestrator (LangGraph 状态机)  │
                    │  预算 / 快照 / 回滚 / 循环控制      │
                    └───┬─────┬─────┬─────┬─────┬──────┘
                        │     │     │     │     │
                    Detect Diagnose  Fix  Verify Review
                        │     │     │     │     │
        ┌───────────────┴─────┴─────┴─────┴─────┴──────────┐
        │                Tool Layer（确定性证据的唯一来源）     │
        │   UnityCLI 探针 │ 采样器注入片段 │ Git │ 资产静态扫描  │
        └───────┬──────────┬─────────┬───────────┬─────────┘
                │          │         │           │
        unity command   probe/*.cs  git +      .mat/.shader/
        (eval / 常驻    (eval_file  资产清单    .controller
         sampler)       注入)       哈希        解析
        ────────────────────────────────────────────────────────
                    KnowledgeStore（SQLite，跨轮持久）
        patterns / attributions / outcomes / fp_stats / tiers
```

**分层的唯一理由**：把"数值"和"语言"彻底分开。所有数字只能从 Tool Layer 出来；LLM 只在四处参与——Detect 的阈值解释、Diagnose 的因子判定、Fix 的方案生成、Review 的裁定——且四处都被结构化 schema 框住（AGENTS I7）。

### 2.1 采集层（D1 定"不写独立插件"；M0' 实跑后二次修正）

⚠️ **实跑推翻了 D1 的实现方式。** 原设想"用 `script eval_file` 注入 `ProfilerRecorder` 常驻采样器"已被验证走不通：7/7 计数器**构造成功**（`skipped=[]`）、300 帧窗口正常落盘，但渲染类**读数恒为 0**；`diagnostics profiler_connect --target editor` 与 `UnityEngine.Profiling.Profiler.enabled=true` 都打开后，新开 recorder 即时采样仍 `last=0 count=0`。同一状态下 Pipeline 内置的 `diagnostics get_performance` 直接给出真值（`drawCalls=184`）⇒ 内置通路不走 `ProfilerRecorder`（其三个反射候选入口 `GraphicsStats`/`GetCounterNames`/`GetFrameStatistic` 实测均不可用，无法确认它到底用什么）。据此重排档位：

**采样三问，三套仪器（v0.12 定性修正，DP-1 §0）**：旧文把三个不同问题全压在秒级轮询上，是口径混淆。正确分工——**"现在什么水平"看分布（A 水位仪），"有没有发作"看逐帧越线（T1 探针 + A4），"藏着什么雷"看代码（C+ 静态轨，M3.5）**。A 档**不是传感器，是水位仪**：它发现不了发作（1Hz 抽样必然漏掉亚秒级尖峰，且读的是上一帧快照、静置不刷新——被跳过的值看起来完全正常，是最危险的失效），但它是结构性指标唯一便宜的改动守护源。谁也不能替谁，混用即违反 I1。

| 档 | 手段 | 得到什么 | 实测状态 |
|---|---|---|---|
| **A（水位仪）** | **轮询 `unity command "diagnostics get_performance"`** | `render`：drawCalls / batches / setPassCalls / triangles / vertices；`memory`：totalAllocated / totalReserved / monoUsed / monoHeap；`frameTiming`：cpuFrameTimeMs / gpuFrameTimeMs / cpuMainThreadFrameTimeMs | ✅ 可用，且**不依赖 Play Mode**（编辑态同值）。硬约束：CLI 往返使实际速率仅 **~1.05 次/秒** ⇒ 这是"按秒抽样"，不是逐帧采样。**只用于 P1 水位与改动守护，禁止用于判"发作"**（G19）。本轮复核其语义边界：编辑态未播放亦出数（实测 `drawCalls=51 / setPassCalls=9 / triangles=2214`），且 `setPassCalls` 与帧调试器的事件行数**精确相等** ⇒ 印证 A 档与 FD 读的是同一套 SetPass 分组口径，不是逐笔绘制 |
| **A4（逐帧计时，v0.12 新增）** | `UnityEngine.FrameTimingManager`（引擎内建，注入常驻回调每帧 `CaptureFrameTimings`+`GetLatestTimings`，读 `FrameTiming` 结构体字段） | **逐帧** cpuFrameTime / cpuMainThreadFrameTime / **gpuFrameTime** / cpuRenderThreadFrameTime + 各级时间戳（frameStart/firstSubmit/presentCalled/frameComplete）。实测编辑态 Play：cpu≈1.6ms、gpu≈1.5~2.2ms 真值，逐帧连续出数 | ✅ **本轮实测解锁**。三条硬约束：① 结果**固定延迟 4 帧** ⇒ 环形缓冲向后深度 ≥14 才能接住（D17）；② 部分帧时间戳未回填（`gpuFrameTime=0` 且 `cpuTimeFrameComplete=0`）——与 -1 哨兵同规则**拒收，不得当 0 统计**；③ 非连续出帧时数值形态错乱（99ms/0.3ms 交替），采集前提同 G16（真实渲染出帧）。⚠️ `GraphicsEvents.drawCall` 逐笔回调在 6000.6 反射不存在，禁按旧博客示例接 |
| **A3（离线 .data，补充源）** | `profiler_record` 录制 → `ProfilerDriver.LoadProfile` + `GetCounterValuesBatch`（反射自 Profile Analyzer 源码发现；AnalysisDataSource 与官方包 `Load(.data)` 实测均不可用，须经编辑器会话中转） | 逐帧 Memory / **GC Allocated In Frame**（实测 120 帧，稳态 ~150KB/帧有分布）；渲染列 = **-1 哨兵**（counter 不存在，与 A2 同因） | ✅ 可用但覆盖窄：marker 级深挖与逐帧时间线；`-1` 必须在解析层显式过滤，禁止当 0 统计 |
| **A2（逐帧注入采样，已复活）** | `script eval_file` 装常驻回调 + `ProfilerRecorder(handle 构造)`——句柄从 `ProfilerRecorderHandle.GetAvailable` 反查，禁静态名猜测 | **GC Allocated In Frame**（实测 last=166,423 / max=508,397 B 每帧）、Total/Mono 内存（实测 1.8GB） | ✅ 可用。⚠️ 渲染计数**不在此体系**：5023 句柄精确匹配 Draw Calls/SetPass/Batches 全落空；构造不存在名称→**静默读 0 不抛异常**（§2.2.1 事实 7），渲染类指标一律禁用 A2/A3 |
| **FD（帧调试器）** | `FrameDebuggerUtility`/`FrameDebuggerWindow` 反射 | 程序侧仅 SetPass 分组行（count 恒等于同帧 setPassCalls，逐笔明细只在 GUI 重绘时由树控件构建）；`GetFrameEventObject` 全空 | ❌ **程序化取数不成立（本轮 ①-⑫ 全套入口实测，§12）**。岗位改为：P2-GPU 发作的**人工下钻**（T6 操作单，定格到指定帧人眼看逐笔）；自动化归因候选 = RenderDoc 外部截帧解析（EX-1 前置：本机 D3D12 不被集成支持）或 B 档（M8） |
| **B（半真机）** | PC Standalone Player + `runtime *` 命令（实测有 `runtime status/set_time_scale/simulate_key`） | 无 Editor 开销的播放器数据 | 需 `unity build`，覆盖 REQ-3 的 IL2CPP/Mono 面。扩展位 M8；**GPU 尖峰逐笔归因的根治路径之一** |
| **C（兜底）** | 资产静态扫描（`.mat`/`.shader`/`.controller`）+ `unity logs` / Editor.log 解析 | 材质实例数、Shader 指令与 keyword、状态机拓扑规模、同步 Load 调用点计数 | 精度低，须标 `static_estimate`，禁止与实测混列或跨档对比。**C+ 代码模式扫描（P3 潜在的唯一源）未建，M3.5** |

**换通路带来两个统计口径必须跟着改**（属 §3 前提，不是细节）：
1. **采样单位从"帧"变"秒"**：`window_frames` → `window_samples`，`n_samples` 语义改为轮询次数（默认 ≥30 次 ≈ 30 秒），并把实测 `poll_rate` 入指纹——1Hz 与 10Hz 的样本不可互比。
2. **帧粒度指标按源分工，不留空白**：逐帧 GC alloc / 内存尖峰 → **A2 已实测覆盖**；**逐帧 frame time（CPU/GPU 分解）→ A4 已实测覆盖**（v0.12，替代旧"由 A3/B 承接"的悬置）；发作的**即时判定**用 T1 探针自计时（Stopwatch 按 frameCount 门控，无 A4 的 4 帧延迟），A4 用于发作后的 GPU 归因分解，两轨并存各标来源。**禁止用秒级抽样冒充帧级结论**（违者按 I1 处理）。

四条链路取数分配（按新通路重排）：
- `F-CPU-01` → **A2 逐帧 GC Allocated In Frame（✅ 已解锁，"解释力"定量基础成立）** + A 的 `cpuFrameTimeMs`（40 样本 40 唯一值）+ C 扫分配点。
- `F-GPU-01` → A 的 `render.*`（实测有效，**唯一通路**——A2/A3 均无渲染 counter）+ C 数材质实例与 Shader 复杂度。
- `F-ANIM-01` → A 的 `frameTiming` 分段 + A3 逐帧时间线（Anim/System 类目耗时 marker，recorder 体系存在）+ C 的状态机拓扑规模。
- `F-LOAD-01` → A 的 `memory.*` + **A3 逐帧时间线**（加载窗口分配/内存尖峰定位；实测首帧 1.9MB 域重载开销可辨）+ C 的同步 Load 调用点计数。

`SamplingBundle` 协议指纹：`scene_guid × replay_script_hash × quality_tier × warmup_samples × window_samples × n_samples × poll_rate × data_tier × editor_mode × environment_fingerprint`（环境指纹=Unity/URP 版本 × GPU 驱动 × 电源计划 × 散热状态，理由见 §8.6）。`editor_mode` 保留——无头模式渲染值仍恒为 0（§2.2.1 事实 1）。指纹任一字段不同即禁止对比（AGENTS G7）。

### 2.2 采集层的已核实约束（会直接影响实现，不是假设）

1. **必须有常驻 Editor。** `unity command/list` 只连已运行的 Editor，不会自己启动。GUI Editor 会被 `unity status` 列为 `ready`；**batch 模式常驻 Editor 不出现在 `unity status` 里**，只能用 `unity command --project-path <p>` 探测。管线统一用 `--project-path` 判可达，不用 `unity status` 当唯一门禁。
2. **Safe Mode 死锁是本管线最现实的失效模式。** 一旦 Fix Agent 改出 C# 编译错误，Editor 进 Safe Mode，Pipeline 包不加载，`eval`/`command` 全部连不上——此时"重采样验证"和"通过 CLI 回滚"两条路同时断掉。对策（写进 AGENTS §4 G9）：**回滚只依赖 git，绝不依赖 Editor 可达**；回滚后重启 Editor 再验证。且 P3 编译门禁必须在采样之前，省掉一整轮采样成本。
3. **Runtime 采样档位受限。** 本机两个 Editor 的 `modules` 字段均为空（未装 Android/iOS 模块），因此 B 档若要做，需先装模块并 `unity build`，属新增授权与耗时项，不进第一轮。
4. **命令名不可假设，且存在"重命名提示"这一好设计。** 实测 `editor_play` 已不存在，服务器直接回 `It was renamed: run 'unity command editor.playmode start'`——包含空格的组前缀名整体加引号传入。参数名同样会纠正（`primitiveType`→`primitive`、`scenePath`→`path`）。所有调用一律走运行时探测 + 失败回读，不写死名字（AGENTS I8）。

**为什么不用 Frame Debugger / Perfdog 直接取数**：Frame Debugger 程序化取数经全套入口实测**判定不成立**（见 §12 "程序侧可取性"行 ①-⑫）——程序侧只能取到 SetPass 分组行（行数恒等于同帧 setPassCalls），逐笔绘制项只在 GUI 重绘时由树控件构建，`GetFrameEventObject` 全空，且 resume 同刻缓冲清空。它保留的岗位：**人工下钻的证据界面**（定格到指定帧看逐笔，配合 `unity command screenshot` 给人工批准时看画面差异）；数值不作源。GPU 尖峰的"检出"由 A4 承接（见 §2.1），"逐笔归因"的自动化候选是 RenderDoc 外部截帧解析（前置 EX-1：本机 D3D12 不被 Unity 的 RenderDoc 集成支持）与 B 档。Perfdog 是真机工具，本 demo 无真机（📌 §9）。我们用"帧预算模型"把 Editor 侧采样映射到中低端机型预算线，映射函数与误差必须在报告里交代，不能假装是真机数据。

#### 2.2.1 采集层实测事实（M0' 实跑 + 实验4 定论，2026-11-07）

这八条是**跑出来的**，不是推的，每条都改变了实现形状：

1. **无头编辑器（`-batchmode -nographics`）的渲染读数恒为 0。** 场景含 20 个 Cube + 1 个相机、`isPlaying=True`、层级里 20 个 renderer 都在，`get_performance` 仍返回 `drawCalls=0 batches=0 triangles=0`，GPU 型号显示 `Null Device`；换带界面编辑器（`gpu=NVIDIA GeForce RTX 4060 Ti`）同场景立即得 `drawCalls≈184~195 setPass≈40~41`。memory 类不受影响（无头实测 570MB 真值）。
   ⇒ **渲染类指标必须 GUI Editor 采集**（AGENTS G16）；无头仅适用于 CPU/内存/加载链路。第一轮无法全程无人值守，需接受桌面窗口与 license 占用。
2. **回调跨 eval 存活，静态字段不存活，且旧回调卸不掉。** 第一次 eval 装的采样器在后续 eval 中持续写盘（`frames` 162480→270180）；但新 eval 返回 `frames=0`（新程序集、新静态状态），旧委托无法从新程序集摘除 ⇒ **两个回调争抢同一文件**。
   ⇒ `EditorApplication.update` 无 `IsRegistered`；`SessionState` 也不行（它跨域重载存活，而回调注册不跨，会留下"标记已装、实际没人采样"的死状态）⇒ **v3 改用"心跳文件新鲜度"作为唯一可观测判据**。清理残留回调的唯一手段是重启编辑器。
3. **`EditorApplication.update` 频率 ≠ 帧率。** 实测约 **2100 次/秒**；且 eval 内 `Thread.Sleep` 会阻塞主线程使 `frameCount` 不推进——**这是测量假象**，改成 Python 侧间隔两次独立 eval 后才看到帧正常推进（4 秒 Δ≈1700，空转约 427fps）。
   ⇒ 采样写入按 `Time.frameCount` 变化门控；并固定 `targetFrameRate=60`，300 帧窗口才对应 ~5 秒。
4. **`get_performance` 读的是上一帧快照，静置时不刷新。** 静置 8 秒三次取值 `drawCalls` 恒定、而 `cpuFrameTimeMs` 在变。
5. **域重载会销毁内存场景物体。** 退出 Play Mode 后探针物体消失（重建时 `cameras=2` 说明上轮物体已没了）。
   ⇒ 每轮录制前必须在**编辑态重建场景**（`probe/build_scene.cs`），否则采到空场景、指标全 0。
6. **`ProfilerRecorder` 这条路在本机取不到渲染读数**：构造 7/7 成功（`skipped=[]`）、300 帧窗口正常落盘，但 `Render/Draw Calls`、`Render/SetPass Calls`、`Render/Batches`、`Render/Frame Time`、`Scripts/Update Time`、`Memory/Mono Used Memory` 的 median **全为 0**，仅 `Memory/Total Used Memory` 有真值（≈1.36GB）。`diagnostics profiler_connect --target editor` + `Profiler.enabled=true` 均无效；单独新开 recorder 即时采样也 `last=0 count=0`。反射候选入口 `GraphicsStats` / `GetCounterNames` / `GetFrameStatistic` 实测均不可用。
   ⇒ 内置 `get_performance` 与 `ProfilerRecorder` 不是同一后端（成因见事实 7）。**渲染计数主力=轮询内置命令**（§2.1 A 档）；注入式采样器经事实 7 澄清后按指标分工复活为 A2。

7. **渲染计数不属于 counter/recorder 体系（实验4 定论）。** 启用 Profiler 后端（`profileEditor=true` + `enabled=true`）后枚举全部 **5023** 个 recorder 句柄，精确匹配 `Draw Calls` / `SetPass Calls` / `Batches` **全部落空**（仅 `GC Allocated In Frame` 命中，类目=Memory）；`ProfilerCategory.Render + "Draw Calls"` 构造不抛异常但恒读 0 ⇒ **"构造成功 ≠ 名称有效"，不存在的名称静默返回 0**。`get_performance` 读到 184 说明它走的是 Frame Stats（引擎直读），与 counter 体系是两条道。
   ⇒ 双源定型：渲染计数唯一走 A（1Hz 轮询）；GC/内存走 A2（按帧，handle 从 GetAvailable 反查）；A3 补 marker 级时间线。同一指标禁止跨源混用，归属由检测器注册表按指标声明数据源。
8. **`GC Allocated In Frame` 实测可用（F-CPU-01 阻塞解除）**：A2 按帧读到 last=166,423 / max=508,397 B；A3 离线 120 帧序列显示首帧 1.9MB（域重载）稳态 ~150KB——分布真实，"30 次 new List → +X B/帧"这类定量预测从此可验证。

**⚠️ 由此暴露一个方法学陷阱（必须在 M1 之前解决，不能留到调参时再说）**：静止场景下 `drawCalls` 的真实分布是**退化的**——40 次轮询全部等于 184（唯一值数=1），MAD=0 ⇒ `noise_band=0` ⇒ §3 的 `k·noise_band` 判据对这类指标完全失效，**任何改动都会被判"统计显著"**。
⇒ 三条对策作为 M1 交付判据：① 夹具回放脚本**必须含动态**（相机/物体运动、动画或粒子驱动），让指标本身有抖动；② 对退化指标（`unique/n < 0.1`）禁用 MAD 噪声带，改用 `min_magnitude` 绝对门限并显式标 `degenerate_distribution`；③ 检出阈值一律来自 clean 变体的噪声标定（§8.1），禁止以"该指标零方差所以改一点就算显著"为由放过改动。

另附一条对夹具设计的意外收获：GUI 实测 `drawCalls≈195 / batches=0` —— 20 个立方体产生 195 个 draw call、批次为 0，说明这套 URP 模板默认配置下 **SRP Batcher 与合批都没生效**。`F-GPU-01` 不必自己造异常，默认场景即是一个天然待诊断样本（但为可控与可复现，夹具仍按设计自建显式缺陷版）。

### 2.3 模型服务配置层（REQ-9）

**接口协议**：一律走 **OpenAI 兼容模式**（`/v1/chat/completions`），LangChain `ChatOpenAI(base_url=...)` 落地，不写厂商专用 SDK——换服务商只改配置表，不改 Agent 代码。

#### 2.3.1 配置文件：`llm_config.yaml`（项目根目录）

两张表，第二张引用第一张，避免"每个 Agent 重复写一遍地址与密钥"。

| 表 | 内容 | 作用 |
|---|---|---|
| `providers` | 服务商 → `base_url` / `protocol` / `api_key_env` / 超时重试 / 该商旗下 `models`（含上下文窗口、是否支持 tool calling 与 json mode） | 描述"能连什么" |
| `agent_model_parameters` | 每个 Agent → `provider` + `model`（引用上表）+ `temperature` / `top_p` / `max_output_tokens` 等 | 描述"谁用什么" |

配置读取顺序：文件存在 → 直接加载；schema 校验用 **pydantic v2**（AGENTS G12），字段非法即启动失败并报出具体字段，不允许静默降级到内置默认值。

#### 2.3.2 密钥边界（安全不变量）

- `llm_config.yaml` **只存环境变量名**（如 `api_key_env: UNITY_PROFILE_INSPECTOR`），不存值；配置文件可入仓库。变量名由配置决定，代码只按 `api_key_env` 指向的名字读取，不硬编码。
- **✅ 注入方式已确认：系统级持久环境变量**（Windows 用户级变量）。推荐由用户本人在「设置 → 系统 → 高级系统设置 → 环境变量」的图形界面里新建，设置后需重启终端/新建会话生效。
  - ⚠️ **Windows 实测限制**：GUI 写入的是注册表，**不会注入已在运行的进程**。因此开发联调通道（`--dev`）允许回退读注册表（`HKCU\Environment` → `HKLM\...\Session Manager\Environment`），并在结果里标注 `source: registry_fallback`；终端用户通道只认进程环境，读不到就停在引导提示重启终端。
  - 之所以由用户自己设而不是管线代设：密钥值只有用户掌握，且写入用户环境变量属于账号级持久改动，按谨慎原则不做自动写入；同时避免密钥进入任何 shell 历史与进程参数列表。
- 密钥只从进程环境读取（开发通道可按上一条回退注册表）。启动时若变量缺失，报"环境变量 `<api_key_env 的值>` 未设置"，**绝不把缺失变量的值或占位符写入任何产物**；存在性检查只输出"有/无"，不输出值。
- 日志、trace span、报告、错误信息一律做脱敏：`TraceSpan.tool_calls[].args_hash` 对含凭据的参数取哈希而非原文（AGENTS I12 / DESIGN §7.1）；HTTP 异常回显中的 header 需过滤。
- 不把密钥作为命令行参数传递（会进 shell history 与进程列表），只允许环境变量注入。

#### 2.3.3 首次使用引导

**✅ 必须先分清两条通道（维护者开发期 ≠ 终端用户）**

| 通道 | 何时配置密钥 | 谁执行 | 连通性测试通过的语义 |
|---|---|---|---|
| **用户通道** | **只在真正要用的时候**才触发引导（首次运行管线、新增模型、或更换 Key/base_url 后） | 终端用户本人 | "这套服务可用于本次运行" |
| **开发/联调通道 `--dev`** | 仓库还没有可运行代码时由维护者预置，目的是提前验证配置层实现 | 维护者 | "配置层可用"，**不代表用户引导已经发生** |

由此得出两条实现要求：
1. 引导逻辑本身必须被实现、并能脱离预置 Key 独立演练（无 Key 时必须走到"停下来提示设置环境变量"这一步，而不是报运行时异常）——否则等于没写。
2. 开发通道跑出的连通性结果不得冒充用户通道的"已通过"状态：结果文件记录 `channel` 字段；用户通道首次运行时，除非服务商/model id/base_url/Key 变量名均未变且结果未过期，否则仍需自己跑一遍。

三条分支，任一完成才允许进入 M2 之后的真实调用：

1. **检测到 `llm_config.yaml` 存在** → 加载 + 校验 schema + 检查密钥变量存在性（**只判断有没有，绝不回显值**）；首次使用或 `model id`/`base_url` 变更后跑连通性测试（§2.3.4），失败则停在配置环节，不开跑。
2. **不存在** → 询问用户「自建配置」还是「使用默认配置」：
   - 选**自建**：逐项问答生成 `providers` 与 `agent_model_parameters`，未通过连通性测试的模型不写入配置。
   - 选**默认**：落一份预置配置（仅 DeepSeek 官方 + `deepseek-flash`，全部 Agent 指向它），并告知"需要你自行设置系统级持久环境变量（变量名即配置里的 `api_key_env`）"。**密钥值不由管线收集、不经管线传递**（见 §2.3.2），设置完重启终端后由首次使用的连通性测试来验证。默认配置的价值是"少填几个字段"，不是"跳过验证"。
3. **配置损坏 / schema 不合法** → 报具体字段错误并给"重建 / 手改 / 切默认"三选，不覆盖用户文件（AGENTS 谨慎原则：同名项先保留两边）。

#### 2.3.4 连通性测试（✅ 已确认：在第一次使用时跑）

触发时机（D11 补充确认）：`run_on: [first_use]`；另在用户主动要求复检、或 `model id`/`base_url` 发生变更时再跑。**不是**每次改 Key 都自动跑——Key 的值管线看不到，也无从判断"改了"。结果落盘 `knowledge/llm_connectivity.json`（含通过时间与回填后的能力字段）。

一次测试做三件事，按顺序，任一失败即中止并分类报错：
1. **`GET /models`**：确认地址与 Key 可用，并**用返回的模型列表校正配置里的 model id**。配置表中的 `context_window` 等能力字段由该列表回填，不允许手填猜测值——默认配置里这些字段初始为 `null`，跑过测试才有数。
2. **最小 chat 调用**（一句固定短输入）：确认该 model id 真能推理，并记录首 token 延迟与往返耗时，写入 trace span（§7.1）。
3. **tool calling / json mode 冒烟**：管线的结构化输出契约（AGENTS I7）依赖这两项能力，服务商声明支持但实测不行时，必须在配置阶段就暴露，而不是跑到 M3 才发现。

⚠️ 步骤 2~4 会向服务商发起真实推理请求并产生费用（量级为几条极短请求）。因此测试属于**首次使用的既定环节**，不在未获用户点头的情况下反复触发。

失败分类要可判别，避免把配置问题当成模型问题：`401/403`=Key 无效或额度、`404/invalid model`=model id 不在列表、`timeout/连接`=地址或网络、`429`=限流、`schema 解析失败`=能力不支持而非服务故障。测试结果只记录摘要与哈希，不落任何凭据。

#### 2.3.5 默认配置与既有约束的衔接

- 默认配置下**全部 Agent 使用同一模型**（DeepSeek 官方 / `deepseek-flash`，2026-11-07 实测服务商只提供 `deepseek-flash` 与 `deepseek-v4-pro`），满足"只有一个模型与服务提供商"的要求。
- 但 AGENTS §1.6 要求 Review Agent 与 Diagnose/Fix 做信息隔离。同模型情况下隔离手段降级为：**独立 prompt 模板 + 独立 temperature + 输入字段白名单裁剪**（Review 不收 Fix 的自我辩护），配置表保留 `review` 单独一行以便随时换成第二个模型——**报告里必须标注"信息隔离为弱隔离（同模型）"**，不能让读者以为做到了模型级隔离。这是诚实性问题，不是配置偏好问题。
- `model`、`temperature`、`prompt_version` 与 token 用量全部进 §7 元指标（成本轴），换模型/换参数属于版本变更，需在趋势图打竖线（§7.3、§8.4）。

---

### 2.4 识别时机（T0~T5，DP-1 并入；v0.12）

| # | 时机 | 触发 | 跑什么 | 产出 | 成本 |
|---|---|---|---|---|---|
| T0 | 标定窗 | 首次 / G7 指纹漂移（含 `runInBackground`、焦点策略变更） | 三层会话链（none/idle/full）× ≥5 窗口 | 基线、阈值、probe_overhead、**P1 水位判据** | 1 标定链 |
| T1 | 常开探针 | **每帧全程**（D12 已拍板=全程常开；启动/域重载后首 N 帧不判，防开机噪声） | 只做越线比较（读标量，判定路径零分配零写盘） | 越线事件（帧号/指标/量级） | 每帧常数级；**开销须按新结构重跑三层标定，旧 ≤1.83% 结论不迁移** |
| T2w | 越界捕获 | 探针响的那一刻 | 环形缓冲落盘：前后 10 帧原始序列（向后 ≥14 覆盖 A4 的 4 帧延迟，D17）+ A4 计时窗口回取 | **P2 证据 = 越界样本 BreachSample** | 越界才写盘 |
| T3 | 验证窗 | 每次 attempt 改后 | 同协议重采样 + 全量守护 + 矛盾规则表（P4） | Verdict、P4 复检 | 1 标定链 |
| T4 | 静态审 | 代码/资产变更事件（fix 落地顺带） | C+ 模式扫描 + 斜率投影（零采样成本） | **P3 potential 清单** | ≈0 |
| T5 | 放大压测 | 人工批准 / 定期 | 夹具规模参数放大 **10×、100×**（D15 用户指定，原 2×/4× 过温和）各 3 窗口 | potential→confirmed 升级或撤回 | 2 标定链/模式 |

顺序依赖：T1/T2w 的越线绝对线必须来自 T0（禁止拍脑袋阈值）；T4 独立于运行链路。

---

## 3A. 问题分类与 finding 等级（v0.12 新增，DP-1 §1）

**分类轴 = 证据形态**（"问题留什么证据、什么时候留"），不是"哪条链路坏了"（四链路是次轴，类型×链路=20 格矩阵逐格登记覆盖）。

| 类 | 定义 | 典型病例 | 发现判据 | 证据要求 | 等级 |
|---|---|---|---|---|---|
| **P1 水位** | 结构性水平过高，每帧"正常"但整体烂；规模无关 | 195 drawCalls/batches=0（SRP 未生效，夹具实测）；稳态分配底噪高 | 绝对锚点（帧预算线）+ 窗口间分布对比 | ≥5 独立窗口中位数+MAD（G17 退化走绝对门限） | confirmed |
| **P2 发作** | 周期/随机尖峰，均值可全绿 | 技能帧 30KB new；同步 Load 卡 800ms；**GPU 单帧爆（A4 gpuFrameTime 越线）** | 探针越线 → 前后 10 帧看形状 | BreachSample（帧号+越线指标+原始序列+A4 窗口） | confirmed |
| **P3 潜在** | 代码有放大模式，规模未触发，**运行时零痕迹** | 热路径 new List（当前规模不发作） | 静态扫描 + 斜率投影（模式×实测斜率×规模假设三合一） | PotentialFinding 三字段缺一不可 | **potential** |
| **P4 矛盾** | 指标组合物理上说不通 | drawCalls 高+triangles 低（合批缺失）；GC 尖峰+代码无分配点（仪器污染，实测） | 跨指标规则表（人工先验，首批 6~8 条） | 两条以上同帧指标+违反的规则编号 | confirmed |
| **P5 感知** | 画面/手感劣化，客观数值可能全绿 | 后处理关掉 drawCalls 反降 | 感知门禁（回放 diff/SSIM），不由 LLM 判 | 固定种子回放结果 | needs_human |

**finding 等级与分母规则（G18）**：`confirmed` 进修复循环；**`potential` 不进修复循环、不进成功率/FP 分母**——它的断言"规模到 S 才发作"在当前规模下**不可证伪**，混进分母质量指标即失真。`potential` 的唯一升级路径 = T5 压测复现（→confirmed）或不复现（→撤回），长期用"事后追认率"评估（<30% 的模式降级回影子）。

**为什么这么分**：仪器跟着证据形态走——用秒级窗口抓尖峰必漏（P2 错用 P1 仪器），用运行时统计潜在风险必虚（P3 当下根本没有运行时痕迹）。P4 是"不需要预设正常值就能证伪"的第三条腿；P3 是范式批评（"正确答案不存在"）推出来的必然层：绝对线以下仍有安全余量，那部分只能靠代码证据。

---

## 3. 基线与噪声：先让"异常"这个词站得住

1. **协议化重复采样** N≥5 次，同场景、同脚本、同档位。A 档下"一次"= 一个轮询窗口（`window_samples` ≥30 次 ≈30 秒，实测速率 ~1.05 次/秒），N≥5 指**窗口重复 5 次**，不是轮询 5 次。
2. 统计量用**中位数 + 稳健 MAD**（不用均值/标准差：GC 尖峰与掉帧是重尾分布，均值会被带偏）。
3. `noise_band = 1.4826 × MAD`；异常判定 = 偏离 > `k × noise_band`（k 默认 3）**且**绝对量 > `min_magnitude`（避免"合批数从 12 降到 11，统计显著但毫无意义"）。
   ⚠️ **分布退化例外（G17，实测）**：静止场景 `drawCalls` 40 次全等于 184 ⇒ MAD=0 ⇒ 噪声带判据失效，任何改动都会"统计显著"。此时禁用相对判据、只走 `min_magnitude`，并标 `degenerate_distribution`；夹具回放必须含动态（§2.2.1）。
4. **双门限**：统计显著 + 业务显著（`deviation_ratio` ≥ 1.5，或该指标占回退总量 `explains_share` ≥ 20%）。
5. 基线版本化 `baselines/<hash>.json`，失效判定用"采样协议指纹 + 场景 GUID + 资产清单哈希"三元组。
6. **档位不可跨比**：A/A2/A3/B/C 任意两档之间（含 A 档 Play 与 A 档 Player）一律拒绝并报错。**指标归属制**：渲染计数类只认 A（A2/A3 无此 counter，A3 中表现为 -1 哨兵、A2 中表现为静默 0——两者都必须被解析层拒绝而非当 0 统计）；逐帧 GC/内存类只认 A2/A3。-1 哨兵与静默 0 是"该源无此项"的信号，不是测量值（I1）。

误报率来源：**重采样不复现的 Issue 自动记一次 FP**；被 Review 判定"改后数据无显著变化但 Detect 曾判异常"也记一次 FP。按 `detector.id + version` 分桶统计。

---

## 4. 修复—验证—回滚 的闭环细节

```
Issue(confirmed)
  → Hypothesis[]（按 §5 置信度排序）
  → 选 Playbook（先查特征库命中；未命中则 LLM 生成→强制人工首签）
  → 权限判定（AGENTS §3 三维度）
  → snapshot（git commit tag + 资产清单哈希 + 采样协议指纹）
  → 执行 EditPlan（unity command 语义编辑 / 探针改场景对象，非文本正则）
  → P3 编译门禁（recompile + 日志过滤 error CS####，失败即回滚，不采样）
  → 不变量断言（固定种子回放，语义保持检查）
  → 重采样（同协议同档位）
  → 门禁 G1/G2/G3
       ├ 通过 → resolved → 记 success → Knowledge 写样本
       ├ 未解决/引入新问题 → Review Agent
       │     ├ continue_with_direction（结构化方向）→ 下一 attempt（计数+1）
       │     ├ rollback_and_replan → git 回滚 → 回 Diagnose（带 reason）
       │     └ escalate_human
       └ G3 感知失败 → 立即回滚 + escalate_human（不烧配额）
```

**回滚颗粒度（你第 5 条）**：两层
- `attempt 级`：单次 EditPlan → 回滚到该 attempt 起点（基线）。修复失败即回滚，符合你的要求。
- `run 级`：整轮管线开始前打 tag；管线自身把工程搞坏（场景损坏、Library 异常）时整轮回滚。

支撑两件事：① 只用 Unity CLI 语义编辑与序列化 API，不手改 `.unity/.prefab/.asset` 的 YAML 文本（Pipeline 文档明确警告手改会 error-prone / invisible / 打错文件）；② 资产导入会重生成 Library 与 `.meta`，故 `asset_manifest_hash` 入快照，回滚后校验 meta 未被意外改写。

**已定 D2**：`not_significant`（没变好也没变坏）视为未解决 → 回滚。留着无收益的改动会污染后续归因，且让"修复成功率"失去意义。

---

## 5. 置信度体系：回答你的问题

> "除了'距离异常热力点的代码块距离'这条规则外，是否还需要考虑别的因素？"

需要。而且**"代码距离"单独用会系统性出错**：离热点近，很多时候只是因为它是被调用方（`Update` 里的公共工具函数），根因其实在调用方。所以把它降为一个因子，而不是主指标。

置信度拆成两个正交的量，不混成一个分数：
- **归因置信度 `C_attr`**：是不是这个原因 → 决定要不要动手
- **动作成功置信度 `C_act`**：这个修法能否解决且不引入新问题 → 决定能不能自动动手

`C_attr` 六因子（除邻近性外均为新增）：

| # | 因子 | 含义 | 为什么必须有 |
|---|---|---|---|
| 1 | **热点邻近性** `proximity` | 你原有的"距异常热力点距离" | 有效但易误判，权重压低 |
| 2 | **解释力/覆盖度** `explains_share` | 该根因能解释多少异常量级（如"30 个材质实例 → 预测 +25 draw call"，实测 +23） | **最关键**：能定量对上才算归因，"看起来相关"不算 |
| 3 | **排除竞争假设** `competitiveness` | 竞争候选数，及是否被证据独立否证 | 只有一个候选时不能自称高置信 |
| 4 | **历史命中率** `prior_hit` | 同三重签名的历史修复成功率（Wilson 下界） | 你第 4 条正反馈循环落到数值上的地方 |
| 5 | **可反事实验证** `counterfactual` | 用一个便宜且不改语义的检测动作先证（临时禁用该组件后重采样，异常是否消失） | 从"推理"升级为"实验"，最硬的一条 |
| 6 | **机制链完整度** `mechanism` | 代码→分配/DrawCall/加载 的引擎机制路径是否有断点 | 防止"改了个像是好的东西" |

计算形式：**逻辑回归式加权 + 硬性否决**。

```
logit(C_attr) = w0 + Σ wi · fi
否决项（任一命中 → C_attr 强制 ≤ 0.5，不允许进 auto）：
  - explains_share < 0.3
  - 无任何 Evidence 支持，仅凭代码阅读
  - counterfactual 实验做了且被否证
```

权重来源要诚实：第一轮用**人工设定先验**（报告标注 `weights: hand-set prior`）；`AttemptRecord` 积累到一定量后用"是否 resolved"作标签拟合真实权重。切换时递增 `weights_version`，否则历史置信度不可比。

`C_act` 三因子：`pattern_maturity`（历史 n、成功率 Wilson 下界、引入新问题率）、`blast_radius`（影响文件数、是否共享资产、是否热路径）、`semantic_preservation`（不变量断言全部可执行且通过——不是 LLM 感觉）。

**决策门**：`C_attr ≥ T1` 才允许修；`C_attr ≥ T1 且 C_act ≥ T2 且 side_effect==none` 才允许 auto；任一不过 → semi；`C_attr < T_attr_min`（默认 0.6）→ `needs_human`。

反过度自信的一层：**校准检查**。按 `C_attr` 分桶统计实际成功率，画可靠性曲线。若"0.8–0.9 桶"实际成功率只有 0.55，说明置信度虚高，必须重定标而不是继续拿高置信去申请 auto 权限。这条是让"越用越好用"可被证明的关键，也是本 demo 区别于"套壳 LLM 自动改代码"的地方。

---

## 6. 特征库与正反馈升维（你第 4 条）

SQLite 三张核心表 + 一张统计表：

```sql
patterns(id, symptom_sig, link, root_cause_sig, action_sig, playbook_id,
         side_effect, tier, n_trials, n_success, n_new_issue, updated_at)
attributions(id, pattern_id, factor_scores_json, c_attr, c_act, outcome)
outcomes(id, issue_id, pattern_id, run_id, metric_delta, guards_json,
         realized_over_expected, review_rounds, rolled_back, created_at)
detector_stats(detector_id, detector_version, n_alerts, n_reproduced, n_fp, fp_rate)
```

三重签名（症状 × 根因 × 动作）才算"同一条经验"，只标签相同不算——避免把"降低 GC"当成一个 pattern 把十种成因混在一起。

**升维**门槛见 AGENTS §3 维度三，四条同时满足才提案（新增的第 4 条即 §8.7 体检表），且**人工批准 + 影子模式 + 小流量**三段走。`n_min` 按 §8.2 的 `n_effective` 口径计数（独立场景×档位），不按运行次数。
**降级**（硬）：1 次"引入新问题被回滚"即降回 semi；感知门禁失败即冻结该 pattern 并人工复盘。另按 §8.6 加 TTL/衰减：长期未复现的长尾 pattern 降级 dormant，不再计入 `prior_hit`。
**默认**：所有新 pattern 一律 `semi` 起步，无例外。

**成功率口径（已定）**：`成功率 = resolved / (resolved + failed)`，`needs_human` 单列不进分母（否则大量转人工会掩盖真实能力）；同时报 `自动率 = auto执行 / 总resolved`。两个数一起才说明"是否越用越自动"。

---

## 7. 管线自身运行统计与可视化控制台（REQ-7）

前六章让管线去度量游戏，本章让管线**度量自己**。这不是附加报表：没有它，"越用越好用"只是形容词——质量涨了但成本翻倍、或误报率下降靠多烧三轮采样换来，都看不出区别。两条要求由此成立：成本指标与质量指标**同轴展示**；且每条指标都要能回溯到产生它的证据文件。

### 7.1 埋点模型

```
trace: run_id
  └ issue_id
      └ attempt_id
          └ stage ∈ {detect, diagnose, plan, fix, compile_gate,
                     snapshot, invariant, sample, verify, review, record}
              └ tool_call（unity command / eval_file / git / sqlite / llm）
```

- **每个 span 必记**：起止墙钟、`cpu_ms`、退出状态、输入/输出契约的 hash、引用的 evidence 文件路径、LLM 的 `token_in/token_out` 与模型名、是否命中特征库、`environment_fingerprint`（见 §8.6）。
- **载体**：`runs/<run_id>/trace.jsonl`（追加写、schema 版本化）→ 聚合进 SQLite 的 `runs / spans / stage_stats` 三表。原始 jsonl 不删，Dashboard 只读聚合表，避免看图时把管线跑崩。
- **观察者效应必须先标定**：`probe_overhead` = 有采样器 vs 无采样器的帧时间差，写入 `SamplingBundle` 元数据。开销超过预算的 `max_probe_overhead_pct`（默认 5%）时该 bundle 标 `instrument_distorted`，**禁止用于任何异常判定**。管线自己的统计同样要报自身开销，否则"工具本身变慢"会被当成"问题变少了"。

### 7.2 元指标清单（三类，缺一不可）

| 类 | 指标 | 口径要点 |
|---|---|---|
| **成本** | 单 run 墙钟；单 Issue 修复成本（累计 token + 采样轮数 + Editor 重启次数）；阶段耗时占比；CLI 调用次数；编译门禁失败率；Safe Mode 事故率 | 必须报**单次 resolved 的边际成本曲线**：随经验积累成本应下降，不降说明经验没有复用价值 |
| **质量** | FP 率（按 `detector.id + version` 分桶）；成功率；自动率；校准误差 ECE；`realized_over_expected` 分布；回滚率；平均审查轮次；`needs_human` 占比；影子模式一致率；留出集通过率（§8） | FP 率与检出率必须**成对展示**（同轴 ROC），单看其一可以任意调阈值刷好看 |
| **稳定性** | 采样失败率；协议指纹不匹配次数；快照/回滚成功率；同夹具跨 run 判定一致率（自一致性） | 自一致性是硬指标：同一缺陷重复跑若结论翻转，前面所有统计都不可信 |

### 7.3 可视化控制台

形态（D8 已确认）：**第一轮做 A**，B 列为扩展位 M9。

- **A｜单文件 HTML 报告**（离线可看、可归档、可入仓库）：读 SQLite → 内嵌 JSON → 图表库渲染；并入 §11 的 M7 汇总报告。零服务依赖，最适合演示。
- **B｜本地只读 dashboard 服务**（按 run 增量刷新，带时间窗/链路/夹具/环境筛选）：不进第一轮。

七张核心视图：
1. **成本-质量双轴趋势**（按 run 时间序列）——回答"是变好了还是变贵了"
2. **FP 率 × 检出率**，按 detector 版本分面，带 Wilson 置信区间带
3. **阶段耗时堆叠/瀑布**，并叠加编译门禁与 Safe Mode 事故标记
4. **可靠性曲线（校准图）+ ECE**，训练集与留出集两条线同屏（差值即过拟合度，见 §8.7）
5. **指标矩阵热力图**：夹具 × 全量守护指标的前后 delta，劣化格直接标红
6. **失败与回滚钻取表**：回滚原因、审查轮次消耗、最终状态，点击进证据链目录
7. **成本分布**：按 Agent 的 token 分布 + 单 Issue 累计成本箱线图

交互与诚实性硬要求：
- 每个数据点可钻到具体证据文件路径；`n` 必须与置信区间同屏，**小样本（n < 30）禁止画折线，只画点**。
- 指标口径 hover 显示公式，不允许只给数不给口径。
- 时间轴上必须在 detector/weights/pipeline 版本上线处打**竖线**，否则趋势图会把"代码改了"误读成"越用越好"。
- 控制台**只读**：不提供在图上点按钮改阈值/改 tier 的入口。任何阈值或权限变更一律走 §8 的留出集 gate + git 流程（AGENTS I3、§3 维度一 blocked 条）。

### 7.4 明确不做
不做实时监控大屏与告警服务（REQ-2 的"日常监控"在本 demo 落为基线 + 回归门禁，不是常驻服务）；不做多用户权限体系。

---

## 8. 防过拟合与泛化验证（REQ-8）

本章的存在理由：本管线有三个自我强化的回路（特征库命中率进 `prior_hit`、人工批准样本进 `outcomes`、阈值决定检出结果），**每个回路都是过拟合通道**。不显式设防，跑 20 轮就能得到一份"看起来全绿"的统计，而它对任何新场景无预测力。

### 8.1 夹具切分：tuning / holdout
夹具天然只有 4 个，且缺陷是自己写的——把阈值调到刚好检出它们，等于把答案写进配置。对策：
- fixtures 分为 `tuning set` 与 `holdout set`（第一轮取 2 + 2）；**holdout 夹具的缺陷实现细节不进入任何阈值调参过程**。
- 阈值只能来自**噪声标定**：对"注入缺陷之前的同一夹具"实测 σ 来定 `k·σ` 与 `min_magnitude`，**禁止**为了让某个缺陷被检出而回调阈值。
- 每加一个变体夹具（同机制不同规模/不同代码写法）都要在 holdout 上报检出率与 FP 率，这两个数一起才叫覆盖度。
- 每个夹具配一个 **clean 变体**（无缺陷版），专供 FP 率测量——只有负样本才能测出误报。

### 8.2 样本独立性：`n` 的计数单位
升维门槛里的 `n ≥ n_min`，如果同一夹具同一档位跑 10 遍就满足，那 10 次测的是同一件事，`n` 是假的。
- **有效样本单位 = 独立场景 × 独立档位**；重复运行的同一上下文只计 1 次有效样本（其余记为"复现验证"，用于稳定性统计，不进成功率分母）。
- 报告同时给 `n_trials` 与 `n_effective`（去重后的独立上下文数），升维提案只看 `n_effective`。

### 8.3 签名不得记具体代码
三重签名若含文件路径、类名、函数名，成功一次就会在同一处反复"命中"，看起来准，实为记忆。
- 签名只允许**机制级结构化特征**（如"每帧热路径分配 / 材质实例化导致合批中断 / 状态机跨层查询规模"），禁止把具体标识符写进签名。
- 新增变体夹具时做**签名可迁移性检查**：同机制不同写法的缺陷应命中同一 pattern；若命中不了，说明签名里混进了实现细节。

### 8.4 置信度权重拟合的防泄漏
用 `resolved` 作标签拟合六因子权重，样本少且标签由管线自产（self-labeled），天然乐观偏置。
- **样本量下限**：`n_effective < 30` 不拟合，坚持人工先验（报告标 `weights: hand-set prior`）。
- **按 pattern 分组切分**（group split）：同一 pattern 的样本不得跨训练/验证集，否则等于背答案。
- 权重拟合加正则项；训练集与留出集的 ECE 差值作为过拟合度，**差值 > 0.05 禁止该权重版本上线**。
- `weights_version` 递增并在 §7.3 趋势图打竖线，历史置信度只在同版本内可比。

### 8.5 断言与回放的种子防针对性绑定
- 语义保持断言与感知回放**跑多组种子**，判定取种子集合的并集（任一发散即不通过），避免"只在这一帧序列上不变"。
- 种子集合**不下发给 Fix Agent**，只给 Verify/Review——否则修复会针对已知种子写特例，变成对测试集过拟合。
- `counterfactual` 因子用的临时禁用动作：只允许 disable/silence 级别且必须可回退，实验前后校验协议指纹一致，防止用"改变运行状态"的方式造出虚假归因证据。

### 8.6 人在环与时间漂移
- 人工批准的样本会自我强化（批准→成功→置信度升→更敢自动）。对策：记录审批者标识与决策结果，按审批者分层统计，并把**影子模式一致率**作为独立指标（auto 决策与人工决策的一致程度），不一致时优先复盘而不是继续升维。
- 环境漂移纳入协议指纹：Unity/URP 版本、GPU 驱动、电源计划、机器散热状态组成 `environment_fingerprint`；跨环境禁止对比，历史指标按环境分桶展示。
- 特征库只增不减会让长尾 pattern 僵死：加 TTL/衰减——连续 `m` 轮未复现且无新样本的 pattern 降级为 `dormant`，不再计入 `prior_hit`。

### 8.7 过拟合体检表（每次阈值/权重/pattern 变更必须附）
| 项 | 通过条件 |
|---|---|
| tuning 检出率 − holdout 检出率 | ≤ 0.15 |
| clean 变体 FP 率 | ≤ 目标 FP 上限（默认 5%） |
| 训练 ECE vs 留出 ECE | 差值 ≤ 0.05 |
| `n_effective / n_trials` | ≥ 0.5（否则样本独立性不足） |
| 签名可迁移性（跨变体命中率） | ≥ 0.8 |
| 同夹具跨 run 判定一致率 | ≥ 0.9 |

**上线规则**：以上数值由 §7 的控制台从 SQLite 直接算出，附在变更说明里；缺任一项或任一项不达标 → 该变更 `blocked`（符合 AGENTS §3 维度一"管线自身代码永远人工"）。

### 8.8 当前阶段的诚实边界
第一轮只有 4 个缺陷机制（含 clean 变体共 8 个夹具场景配置，见 §11），独立场景 × 档位的组合数少，`n_effective` 是个位数，**统计功效不足以支撑任何泛化结论**。所有质量与成本趋势在此阶段只能作为**方法学示范**（机制跑通、口径可算），不得表述为"已验证越用越好用"。由此推出一条必然结果：第一轮的 §8.7 体检表**预期不达标**，因而所有阈值/权重/pattern 变更都保持 `blocked` —— 这不是流程失败，而是门禁在按设计工作。达标所需的 `n_effective` 门槛写进报告页脚，让读者知道什么时候才有资格谈泛化。

---

## 9. 边界声明（写进交付物，不藏）

本 demo 第一轮采样的全部数据来自 **PC 上的 Unity Editor（Mono + 编辑器开销）**，与真机（尤其中低端 Android）在 CPU 频率、GPU 架构、GC 行为、加载 IO 上差异巨大。诚实表述：

- 管线验证的是**方法与闭环**：协议化采样 → 稳健统计 → 归因 → 分权限修复 → 全量守护验证 → 回滚 → 沉淀。方法换到真机数据源上不变，只换采样适配器（B 档 Player / 未来 Perfdog 适配器）。
- `SamplingAdapter` 接口化，第二档实现（Android/Perfdog）在本 demo 只有占位与单元测试，无真机数据，报告中显式标 `placeholder`。
- 帧预算模型给的是"若按此映射，等效预算超支多少"，标注 `model estimate`，不与实测混列。

这段不是可选的免责说明：结论的可信度取决于数据来源被如实交代。缺了它，"实测"与"估算"在报告里无法区分，整个量化口径随之失效。

---

## 10. 决策结论（D1~D7 于 2026-11-07 拍板；D8~D11 于同日确认通过）

| # | 决策 | 结论 |
|---|---|---|
| **D1** | 采集层是否先写 Editor C# 插件 | **不写独立插件**，改用 CLI 自带 `eval`/`eval_file` 注入常驻采样器（§2.1 A 档）。代价：跨 eval 无静态状态 → 采样器必须自持并落盘；依赖 Pipeline 包与常驻 Editor |
| **D2** | `not_significant` 是否触发回滚 | **回滚** |
| **D3** | 第一轮覆盖几条链路 | **四条全做**（CPU/GC、GPU/渲染、动画、加载），需配套 §1.1 检测器注册表 |
| **D4** | LLM 后端 | ✅ **已收口**：**OpenAI 兼容模式**；服务商 DeepSeek 官方（`https://api.deepseek.com`，经官方文档核实）；默认模型按 2026-11-07 实测校正为 `deepseek-flash`（原拟用的 `deepseekv41flash` **不在服务商模型列表内**，服务商只提供 `deepseek-flash` 与 `deepseek-v4-pro`），全部 Agent 使用同一模型；密钥只从环境变量读取，变量名即配置里的 `api_key_env`（当前为 `UNITY_PROFILE_INSPECTOR`），配置不存值。开发通道四步连通性测试已全部通过（详见 §2.3） |
| **D5** | `max_review_rounds` | **3**，并允许"配额未耗尽但审查判定不该自动 → 立即转人工"的短路 |
| **D6** | 误报率下降是否做成显式指标与图表 | **要**（D6 采纳，见 §7 元指标与 §11 M6；FP 率必须与检出率成对展示） |
| **D7** | 工程来源 | **新建 URP 空工程自建夹具**（`com.unity.template.urp-blank@17.2.6`，Editor `6000.6.7f1`） |
| **D8** | 管线自身统计的载体与形态 | ✅ **已确认**：jsonl + SQLite 聚合；可视化做**单文件 HTML 报告**（§7.3 A），dashboard 服务为扩展位（M9） |
| **D9** | 防过拟合的强制度 | ✅ **已确认**：作为**变更上线门禁**而非事后复盘——§8.7 体检表缺项或不达标 → 该阈值/权重/pattern 变更 `blocked` |
| **D10** | 第一轮交付范围 | ✅ **已确认**：M0、M0'、M1、**M1'**、M2、M3、M4、M5、M6、**M6'**、M7 全量纳入；M8/M9 为扩展位。体检表与埋点底座不再是"机制演示"，而是第一轮必须交付的可用件 |
| **D11** | 模型配置载体与安全边界 | ✅ **已确认**：根目录 `llm_config.yaml` 双表（`providers` ← `agent_model_parameters` 引用）；文件只存**环境变量名**、不存密钥值；首次使用引导自建或默认配置。**补充确认**：密钥用**系统级持久环境变量**注入（用户在图形界面自行设置，管线不代设、不收集值）；连通性测试**在第一次使用时跑**（另在主动复检或 model id/base_url 变更时重跑），不随每次改 Key 自动触发。同模型下 Review 隔离降级为弱隔离并需在报告标注（§2.3.5） |
| **D12** | 探针常开范围 | ✅ **已拍板（v0.12）：全程常开**（不限于回放窗口）。代价入档：① 开销标定必须按新结构重跑三层链（旧 ≤1.83% 是窗口内口径，不迁移）；② 启动/域重载帧天然越线，warmup 规则扩展到"每次会话首 N 帧不判"，全程越线事件 FP 单列分桶 |
| **D13** | GPU 尖峰方案 | ✅ **已拍板（v0.12）**：调研推翻"完全盲区"原判——**检出用 A4 FrameTimingManager**（实测编辑态 Play 逐帧 gpu 真值 1.5~2.2ms；约束：4 帧固定延迟、未回填帧按哨兵拒收）；**逐笔归因**：短期人工下钻（FD GUI + T6 操作单），自动化候选 RenderDoc 程序化解析（前置实验 EX-1：本机 D3D12 不被 Unity RenderDoc 集成支持，需验证切 DX11 后数值可比性并给指纹加 `graphics_api` 字段）与 B 档（M8）。`GraphicsEvents.drawCall` 回调在 6000.6 反射不存在，禁再尝试。EX-1 是否立项待用户指令 |
| **D14** | C+ 首批模式集 | ✅ **已拍板（v0.12）**：6 条起步——热路径 new/装箱/Update 内 GetComponent 前 3 条默认启用；LINQ/ToString、同步 Load 调用点、运行时状态机重建、材质 Instantiate 后 3 条影子模式（只进报告不投影）；事后追认率 <30% 的模式降回影子 |
| **D15** | 压测放大档 | ✅ **已拍板（v0.12）**：**10× 与 100× 两档、各 3 窗口**（用户指定，替代原建议 2×/4×——过于温和，多数雷在 2 倍下仍不发作）；规模参数进协议指纹 `scale_factor` |
| **D16** | FD 下钻形态 | ✅ **已拍板（v0.12）**：A——报告只出"帧号+操作单"，不自动截图（截图依赖帧对齐基建，M7 再评） |
| **D17** | 环形缓冲深度 | ✅ **已拍板（v0.12）**：默认 10、可配置；**向后深度 ≥14 为硬性下限**（配置校验强制），理由=接住 A4 的固定 4 帧延迟 |

补充机制（已采纳进设计，非待议）：
- **P1 预算护栏**：单 Issue 最大采样轮数、总时长、token 上限，超预算直接 `needs_human`。没有它，管线会在归因不明的 Issue 上无限打转并烧成本；现同时是 §7 成本指标的告警线。
- **P2 语义保持断言**：改前后跑固定种子回放，比对关键逻辑输出（伤害值、事件序列、状态机轨迹）。这是"不影响感官体验"的**可执行依据**，也是 auto 权限真正的技术地基。种子集合的分配规则见 §8.5。
- **P3 编译门禁**：EditPlan 落地后先确认工程可编译（`recompile` + `error CS####` 过滤），失败即 attempt 失败并回滚，不进入采样。

---

## 11. 里程碑（第一轮 = M0~M7 含 M0'/M1'/M6'；M8/M9 为扩展位）

**第一轮必交付**（D10 已确认，M1' 与 M6' 不再是机制演示，而是可用件）

1. `M0` 环境与可达性：用 `6000.6.7f1` 创建 URP 空工程 + `unity pipeline install` + 常驻 Editor 启动脚本 + **可达性探测**（`unity command --project-path` 与 `unity command --query eval` 确认真实命令目录，不假设命令名）+ **模型配置引导（§2.3：加载/生成 `llm_config.yaml`，连通性测试通过才算 M0 完成）**
2. `M0'` **采集层（原第一优先级）**：`probe/` 三段片段（install / start-stop / read），产出第一份带噪声带的 `SamplingBundle`；含"跨 eval 状态"与 Play 模式时序两个坑的验证；**含 §7.1 的 `probe_overhead` 标定**
3. `M1` 基线与统计：中位数+MAD、双门限、基线版本化、档位不可跨比；四个夹具注入并被自动检出（`F-CPU-01`/`F-GPU-01`/`F-ANIM-01`/`F-LOAD-01`）。
   - 口径提醒：阈值只由 tuning 夹具的 clean 变体噪声标定得出（§8.1），四个夹具"被检出"是标定后的结果，不允许为让某个夹具出现而回调阈值——holdout 上的检出率是验证，不是调参依据。
4. `M1'` **埋点与统计底座**：`trace.jsonl` + `runs/spans/stage_stats` 三表 + 控制台骨架（成本/质量/稳定性三类指标先各出一张图）。
   - 交付判定：任一 run 的每个阶段都有 span，且缺 span 的 run 能被自动标 `untraced` 并从趋势剔除（G13）。
   - 顺序要求：必须在 M2 之前完成，否则后续所有"越用越好"的结论无法自证，届时补埋点会让历史 run 全部作废。
5. `M2a` **发作捕获基建（v0.12 新增，DP-1 工具 T2+T1+T7）**——插在 M1' 与 M2 之间，因为越界样本是 M2 归因的新输入形态：
   - **T2 可复现回放先行（最先做）**：夹具驱动改固定种子+固定步长，`replay_script_hash` 真实入指纹，重放帧号 ±1 对齐验证。它是 T1 样本复跑、T5 压测、FD 人工下钻三处的共同前提。
   - **T1 全程常开探针 + 环形缓冲**（D12=B，D17=默认 10/向后 ≥14）：判定路径零分配零写盘（M1' 观察者污染教训），A4 计时接入（未回填帧哨兵拒收）；上线前**重跑三层开销标定**，旧 ≤1.83% 结论不迁移；warmup 扩展到"会话首 N 帧不判"。
   - **T7 矛盾规则表首批**（纯计算，几乎免费）：drawCalls/triangles 比、setPass/batches、GC 尖峰/代码分配、gpuFrameTime 越线+cpuFrameTime 平稳=GPU-bound 等 6~8 条。
   - 交付判定：任一夹具能产出结构完整的 `BreachSample`（帧号+越线指标+前后序列+A4 窗口+指纹），且同协议重放能再次命中同一帧号 ±1。
6. `M2` 归因与置信度：Evidence→Hypothesis、六因子、否决项；输出带证据链的归因报告
7. `M3` 修复与验证闭环：EditPlan + 快照 + git 回滚 + P2 断言 + P3 编译门禁 + 全量守护 + G1/G2/G3；`F-CPU-01` 全自动跑通一次
8. `M3.5` **潜在风险层（v0.12 新增，DP-1 工具 T3+T4+T5 骨架）**：代码模式扫描器（C+，D14 前 3 启用后 3 影子）+ 斜率投影器 + 压测协议定义（10×/100×，D15）。交付判定：`PotentialFinding` 三字段契约校验生效（缺斜率来源或规模假设即拒）；对夹具代码出清单；potential 全程不进分母（G18 负例测试）。
9. `M4` 权限分档与人工回路：`F-GPU-01`、`F-ANIM-01`、`F-LOAD-01` 走 semi（先报后批），证明感知门禁与批准流程
10. `M5` 审查 Agent 与信息隔离：人为制造"引入新问题"与"Safe Mode"两类场景，验证 `rollback_and_replan`、配额、短路转人工、以及**回滚不依赖 Editor 可达**
11. `M6` 特征库与指标：误报率、成功率、自动率、校准曲线、升维提案（展示机制，不实际批准）；新增**事后追认率**口径（potential 的长期质量指标，§3A）
12. `M6'` **过拟合体检表接入上线流程**（§8.7，D9 已确认为硬门禁）：clean 变体夹具、tuning/holdout 切分、`n_effective` 计数、签名可迁移性检查；体检表六项数值由 §7 控制台自动算出并附在变更说明里，缺项即 `blocked`。
13. `M7` 总结报告 HTML：前后对比全量指标表 + 证据链索引 + §7.3 七张视图 + 边界声明 + §8.7 体检表实际数值 + **§2.4 格式的"不可检出清单"（G20）**；含 T6 FD 人工下钻操作单（D16=A 形态）与 T8 感知门禁基建（种子回放+截图 SSIM+动画事件 diff，G3 由此从纸面落地）

**扩展位**（第一轮不做）

14. `M8` B 档 Player 采样，覆盖 IL2CPP/Mono 面（需装 Android/iOS 模块，见 §12）——**同时是 GPU 尖峰逐笔归因的根治路径之一**（另一候选：RenderDoc 程序化解析，前置 EX-1 见 D13）；`M9` 本地 dashboard 服务

**M1'/M6' 进第一轮带来的两处代价，需一并接受**：
1. **夹具数量翻倍**：§8.1 要求 4 个缺陷夹具分为 tuning(2) + holdout(2)，且每个都要配 clean 变体测 FP ⇒ 实际要维护 **8 个夹具场景/配置**（4 缺陷 + 4 clean）。工作量主要在场景搭建与回归脚本复用，不在 Agent 侧。
2. **体检表在第一轮必然不达标**：`n_effective` 是个位数，§8.7 六项中的样本独立性、迁移性、跨 run 一致率无法达到门槛。因此第一轮的结论只能是"门禁机制可用 + 数值未达标 → 所有阈值/权重/pattern 变更一律保持 `blocked`"，这本身是门禁在正确工作，不是失败。§8.8 的"方法学示范"标注由此从免责声明变成**第一轮的必然输出**。

**演示脚本建议演三段**：`F-CPU-01` 全自动闭环（证明"能自动"）+ `F-GPU-01` 半自动带批准（证明"知道什么时候不该自动"，更能体现权限分档与人工回路真正生效）+ §7 控制台的成本-质量双轴趋势图（证明"可复用且越用越省"）。四条链路全部实现，但演示只挑这两个对立面，其余两条作为广度证据出现在报告里。

---

## 12. 环境与已验证事实（非假设，均为本机实测）

| 项 | 实测值 | 影响 |
|---|---|---|
| CLI | `unity` 1.0.0-beta.6 | 可用；beta，命令面可能漂移 |
| Editor | `D:\unity_editor\6000.6.7f1`（x86_64）、`D:\unity_editor\2022.3.51f1c1` | **必须用 6000.x**——`com.unity.pipeline` 要求 Unity 6.0+，2022.3 无法承载 |
| 已装模块 | 两个 Editor 的 `modules` 字段均为空 | 无 Android/iOS 模块 ⇒ B 档 Player 采样需额外安装授权，不进第一轮 |
| 当前 Editor 实例 | 无（`STATUS_NO_INSTANCES`） | M0 需要启动并常驻一个 Editor，会占用 license seat |
| URP 模板 | `com.unity.template.urp-blank` v17.2.6（URP 17.x） | 新建夹具工程用它 |
| Python | 3.14.3（`C:\Python314\python.exe`） | 满足 LangChain 1.x（要求 ≥3.10） |
| LangChain 依赖 | dry-run 解析通过：`langchain-1.4.9`、`langgraph-1.2.20`、`langchain-openai-1.7.4`、`openai-3.46.0`，纯 v2 pydantic 路径 | ⚠️ 已知坑：**pydantic v1 模型在 3.14 上跑不起来**（上游 issue #34441）。本项目所有模型一律 pydantic v2；若引入任何老三方包携带 pydantic v1，视为阻塞问题 |
| 真机 | 无 | ⇒ §9 边界声明 |
| 实验4：recorder 体系边界 | 枚举 5023 个可用 recorder：`GC Allocated In Frame`（类目=Memory）存在且 handle 构造实测读到 166KB/508KB 每帧真值；`Draw Calls/SetPass/Batches` 精确匹配**不存在**；不存在名称的构造静默返回 0 不抛异常 | 双源定型依据（§2.2.1 事实 7/8）；解析层必须把"静默 0"与"-1 哨兵"当非法值拒绝 |
| M1 基线标定 | tuning=F-CPU-01/F-GPU-01，holdout=F-ANIM-01/F-LOAD-01，+CLEAN 统一基底（20 共享材质立方体+轨道相机）；检出 4/4，CLEAN 对照自身零检出；阈值全量来自标定（4/4 应用，缺项即中止） | 检测端硬约束：`threshold_source != noise_calibration` 构造即拒（已验证负例拦截） |
| M1 指标贴机制教训 | F-ANIM-01 用 `cpuMainThreadFrameTimeMs`（整帧 4ms 摊薄，delta 0.27ms<地板）两轮未检出；换 `Animator.ProcessAnimations`（A2 逐帧，实测 F-ANIM-01 med=354µs vs clean≈0）立即检出 | 检测器指标必须直接量机制本身，"帧时被睡眠摊薄"是秒级/整帧指标的通病；标定表与检测器指标需全量对齐（否则漏标=未覆盖） |
| M1 缺陷量级修正 | F-CPU-01 4×100 skills（+68.5KB/帧=5.1×）；F-GPU-01 40 实例化材质（47→124 draw call=2.6×）；F-ANIM-01 128 单元；F-LOAD-01 400×64KB 同步加载+16MB 常驻（+77.5MB 峰值=1.12×）；targetFrameRate=-1 解除 vsync 锁死 | clean 与缺陷场景唯一差异=缺陷组件本身，drawCalls/分配差才可归因 |
| 实验4：离线 .data 解析通路 | `ProfilerDriver.LoadProfile` + `GetCounterValuesBatch` 经编辑器会话可解析（120 帧实测，Memory/GC 列真值、渲染列 -1）；Profile Analyzer 的 `Load(.data)` 与 AnalysisDataSource 反射均不可用 | A3 档成立但覆盖窄；-1=counter 不存在的哨兵 |
| 服务商模型列表 | 2026-11-07 实测 `GET /models` 返回 2 项：`deepseek-flash`、`deepseek-v4-pro`（均 `context_window=1048576`、`max_output_tokens=393216`） | ⚠️ 原拟用的 `deepseekv41flash` 不在列表内，已按实测校正为 `deepseek-flash`。印证 §2.3.4 "model id 必须由列表回填、禁止手填猜测值"这条规定的必要性 |
| 连通性测试（开发通道） | 四步全通过：`list_models` / `minimal_chat` / `tool_call_smoke` / `json_mode_smoke`，纪录落 `knowledge/llm_connectivity.json`（`channel: dev`，不含凭据） | tool calling 与 json mode **实测可用**，结构化输出契约（AGENTS I7）成立 |
| 密钥变量可见性 | `HKCU\Environment` 有 `UNITY_PROFILE_INSPECTOR`（REG_SZ，非空），但**已运行的终端进程读不到** | Windows GUI 设置只写注册表、不广播给现有进程。⇒ 代码实现注册表回退（仅开发通道，并标注 `source: registry_fallback`）；用户通道需重启终端 |
| 通道门禁 | 实测：仅有 `dev` 纪录时 `user` 通道放行判定返回 `False` 并提示需自行完成一次测试 | §2.3.3 双通道语义由代码而非文档保证 |
| 夹具工程 | 已创建 `fixtures/upi-fixture`（URP 模板 17.2.6，Editor 6000.6.7f1）；`com.unity.pipeline` **0.10.0-exp.1** 已装入 manifest | M0 完成；工程已纳入 git（Library/Temp 等生成物由 .gitignore 排除） |
| Pipeline 命令面 | 实测 **191 条命令**（`unity command --project-path <p>`），含 `script eval` / `script eval_file` / `script recompile`、`diagnostics get_performance`、`editor.playmode start/stop`、`gameobject *`、`scene *`、`material *`、`animation.animator *`、`capture screenshot` 等；**命令名带组前缀且部分含空格**，须整体加引号；重命名会给提示（`editor_play` → `editor.playmode start`） | I8 落实：全部运行时探测，禁止写死命令名；参数名同样会被服务器纠正（`primitiveType`→`primitive`、`scenePath`→`path`） |
| `ProfilerRecorder` 可用性（A3 通路） | 15/15 个目标计数器**可构造**（`Unity.Profiling` 命名空间，非 `UnityEngine.Profiling`；`ProfilerCategory` 是 struct 不能用 `Enum.Parse`），但渲染/Scripts/Mono 类目**读数恒为 0**，仅 `Total Used Memory` 有真值 | ⚠️ 构造成功 ≠ 取到数。A 档主力因此改为内置 `get_performance` 轮询（§2.1）；名称写错也不抛异常，属静默失效，必须由 clean 变体标定暴露 |
| **无头模式渲染不可用** | `-batchmode -nographics` + Play Mode + 场景含 20 Cube/1 相机：`drawCalls=0 batches=0 triangles=0`、`gpu=Null Device`；换 GUI 编辑器（RTX 4060 Ti）同场景：`drawCalls=195 setPass=41 triangles=5357` | **GPU/动画链路必须 GUI Editor 采集（G16）**；CPU/内存/加载链路无头可用（memory 无头实测 570MB 真值）。协议指纹新增 `editor_mode` 字段 |
| 采样器存活特性（I9 实测） | 回调跨 eval 存活（`frames` 162480→270180 持续增长）；静态字段不跨 eval 存活（第二次 eval 返回 `frames=0`）；**且新 eval 无法卸载旧回调**（`CallbackFunction` 无 `IsRegistered`，重复安装导致多回调争抢同一文件）；`SessionState` 跨域重载存活而回调注册不跨 ⇒ 也不能做幂等标志 | 幂等只能用**心跳文件新鲜度**（v3 实现）；残留回调的唯一清理手段是重启 Editor |
| update 回调频率 | 实测 ~**2100 次/秒**，且静置时 `Time.frameCount` 不推进 | 采样必须按 `Time.frameCount` 门控，否则窗口长度与噪声带全部失真（§2.2.1 事实 3） |
| `get_performance` 语义 | 静置 8 秒三次取值 `drawCalls` 恒 195（`cpuFrameTimeMs` 在变）⇒ 读的是**上一帧快照** | 只可用于单点快查/健康探测，帧序列统计必须靠常驻采样器（§2.2.1 事实 4） |
| 契约门禁自测 | `upi/core/models.py` 六条硬门禁（跨模式禁对比、无头不误伤 CPU/加载、感知层强制非 auto、Verdict 必含全量守护、签名禁标识符、低置信禁入循环）**6/6 拦截生效** | AGENTS 文档约束已落为可执行校验，不是纸面规则 |
| **Frame Debugger 程序侧可取性**（2026-11-07 实测，Unity 6000.6.7f1 + URP 17.2.6，GUI Editor） | ① `UnityEngine.FrameDebugger.enabled`：`CanWrite=False`、`SetMethod=NULL`，反射赋值抛 `ArgumentException: Set Method not found` ⇒ **无赋值入口**，唯一入口是 `FrameDebuggerUtility.SetEnabled(bool,int)`；② `SetEnabled` 的效果**跨 tick 落地**（同调用内回读恒为 False，下一次调用才读到 True）；③ 抓取落地时 `EditorApplication.isPaused` 自动变 True、帧号停止推进（`m_EnablingWaitCounter=4`）；④ 置 `isPaused=false` 后游戏继续出帧（6 秒 frame 94983→97233）；⑤ **但 resume 的同一时刻 `enabled` 回到 False、`count` 由 9 归 0** ⇒ 抓取在 resume 时被丢弃；⑥ `limit` 可写但会被引擎复位（设 8000/20000 后 `count` 恒 9）；⑦ `GetFrameEvents()` 返回 **9 条**，与同帧 `get_performance.setPassCalls=9` 精确相等（同帧 `drawCalls=51`），类型分布 `DrawProcedural=6 / SRPBatch=2 / Mesh=1` ⇒ 给的是 **SetPass 顶层分组行，不是逐笔绘制项**；⑧ `GetFrameEventData(index, out data)` **第一参数是序号**；对序号 0..600 全量扫描，仅 index 8 可解析，内容是 `Hidden/Universal/CoreBlit` + pass `BilinearDebugDraw` = **FD 自身的调试叠加层**；⑨ `GetFrameEventObject(index)` 在全部可解析项上返回空；⑩ 树数据源 `m_FrameEvents` 长度同为 9、入口 `SetEvents(FrameDebuggerEvent[])`，`FetchData()` 在 eval 内调用后 `children` 仍取不到子级 ⇒ GUI 与程序读到的是**同一个 9 行数组**，逐笔子项只在窗口重绘时构建；⑪ `OpenWindowAndToggleEnabled` 是 **toggle 语义**，与 `SetEnabled` 混用会让状态不可归因；`OpenWindow` 单独调用不开启抓取；⑫ `GetBatchBreakCauseStrings()` 恒 49 条 = 引擎内置候选原因表，与具体场景无关 | **结论：探针响的那一刻无法程序化取到可解读的当帧渲染结构**。"探针响 → 拉当帧 FD 数据 → resume"链路在采集层不成立（resume 丢数据 + 明细需 GUI 重绘才物化）。渲染结构下钻保留为**人工审查时在 GUI 里看**，不进自动管线；自动链路仍用 A2 常驻采样器的"越界前后 10 帧"profile。附带两条方法学教训：**(a) 后台失焦 `runInBackground=False` 会使帧号不推进**——这正是"FD 定格世界"误判的真正来源，采集前置必须显式确认该开关或保持窗口聚焦；**(b) 静默 catch 掩盖了参数误用**（把事件对象当序号传入），三次误诊皆源于此 ⇒ 探针侧禁止吞异常，失败必须原样落盘 |

| **A4：`FrameTimingManager` 逐帧 CPU/GPU 计时**（2026-11-08 实测，D13 调研产物） | 类型/方法全部存在（`CaptureFrameTimings`/`GetLatestTimings(uint, FrameTiming[])`/`GetGpuTimerFrequency`=1e9/`GetCpuTimerFrequency`=1e7）；`FrameTiming` 字段含 `cpuFrameTime/gpuFrameTime/cpuMainThreadFrameTime/cpuRenderThreadFrameTime` + 四个时间戳。实测两态：**编辑态非播放** cpu=99/0.3ms 交替、gpu 恒 0（未出帧，形态错乱）；**Play+真实连续出帧** cpu≈1.6ms、**gpu≈1.4~2.2ms 逐帧真值**，但约 2/8 帧 `gpuFrameTime=0 且 cpuTimeFrameComplete=0`（时间戳未回填）。`GraphicsEvents`/`GraphicsRenderStats` 类型在本版本反射**不存在**（Unity 6 已移除/内移，旧博客方案不可复用） | **GPU 尖峰"检出"自此有逐帧源**（推翻上一轮"GPU 发作=全盲区"的判断）：T1 探针即时判越线（自计时），A4 提供逐帧 GPU 分解与归因初判（gpu 越线+cpu 平稳=GPU-bound）。三条使用规则：① 结果固定延迟 4 帧 ⇒ 环形缓冲向后 ≥14（D17）；② 未回填帧（gpu=0 且 completeTs=0）按 -1 哨兵同规则拒收，**不得当 0 统计**；③ 采集前提=真实连续出帧，前置检查显式确认 `runInBackground` 或窗口聚焦（与 G16 同源，指纹字段承接）。逐笔归因仍盲区，路径=EX-1（RenderDoc+需切 DX11，本机 D3D12）或 B 档 |
| **RenderDoc 外部路径的先验核查**（D13 调研，未实测） | Unity 官方文档明示 RenderDoc 集成仅支持 **D3D11 / OpenGL Core**；本机实测 `SystemInfo.graphicsDeviceType=Direct3D12`；本机未安装 RenderDoc。RenderDoc 侧程序化解析能力存在（Python API：ActionDescription 逐笔 draw、FrameStatistics、in-app API 触发 capture） | EX-1 实验内容 = 切 DX11 后①能否 capture ②数值与 DX12 是否可比（G7 需 `graphics_api` 字段）。**结论未出前，GPU 逐笔归因自动化不承诺**；已写入 G19/D13 的"归因转人工"口径不因本行放松 |

---

## 13. 变更记录

- **v0.12**（2026-11-08）：**DP-1 范式重排并入**（全文见 `docs/DP-1_问题分类与识别矩阵_设计补丁.md`）。① §2.1 定性修正——A 档从"主力"改判为"水位仪"，采样拆为三问三仪器（水位 A / 发作 探针+A4 / 潜在 C+）；② **新增 A4 档**（`FrameTimingManager` 逐帧 CPU/GPU 计时，实测 Play 态 gpu 真值 1.4~2.2ms；约束：4 帧固定延迟、未回填帧按哨兵拒收、需连续出帧）——**推翻上一轮"GPU 尖峰全盲区"判断：检出可行，逐笔归因仍人工**；③ §1.1 注册表加两条约束（episode 必须逐帧源、`attribution_source` 无源填 human）；④ 新增 §2.4 识别时机 T0~T5 与 §3A 问题五类×finding 四等级（`potential` 不进任何分母，升级只经 T5 压测）；⑤ D15 修订为 **10×/100×** 两档各 3 窗口（用户指定，原 2×/4× 过温和）；D12=全程常开（开销标定重跑、会话首 N 帧不判）、D13=调研收口（EX-1 待立项）、D14/D16/D17 按推荐拍板；⑥ 里程碑插入 **M2a**（T2 回放基建→T1 环形缓冲→T7 矛盾表）、**M3.5**（T3+T4），M7 扩 T6/T8，M8 注记为 GPU 归因根治路径之一；⑦ AGENTS 同步至 v0.7：新增 **G18/G19/G20** 三条门禁（并补齐正文此前缺失的 G17 条目）、Detect 输出扩为 `Finding[]`、Verdict 增 `conflicts[]`、契约增 `PotentialFinding/BreachSample`、指纹增 `scale_factor/graphics_api`、MetaMetrics 增事后追认率。
- **v0.8**（2026-11-07）：M0' 闭环实跑推翻 D1 实现方式——注入式 `ProfilerRecorder` 构造 7/7 成功但渲染读数恒为 0（`profiler_connect` + `Profiler.enabled` 均无效），A 档主力改为**轮询内置 `diagnostics get_performance`**（实测 drawCalls=184 等真值，但速率仅 ~1.05 次/秒 ⇒ 秒级抽样）；统计口径相应从"帧"改"秒"并新增 `poll_rate`；§2.2.1 扩为六条实测事实；**新发现方法学陷阱：静止场景 drawCalls 唯一值=1 ⇒ MAD=0、`k·σ` 判据失效**，M1 须以"回放含动态 + `degenerate_distribution` 走绝对门限"为交付判据；幂等判据由 `SessionState` 改为心跳文件新鲜度。
- **v0.7**（2026-11-07）：M0/M0' 实跑。夹具工程 `fixtures/upi-fixture` 创建并装入 `com.unity.pipeline` 0.10.0-exp.1；Pipeline 命令面实测 191 条（命令名带组前缀、部分含空格，重命名有提示）；A 档 15/15 计数器可构造。**新增 §2.2.1 四条实测修正**：无头模式（`-nographics`）渲染计数器恒为 0（GUI 下同场景实测 drawCalls=195/setPass=41），据此新增门禁 **G16** 与协议指纹字段 **`editor_mode`**；采样器回调跨 eval 存活而静态字段不存活、且旧回调无法从新 eval 卸载 ⇒ **I9 重写**、幂等标志改用 `SessionState`、文件写入改原子替换；`EditorApplication.update` 实测 ~2100 次/秒 ≠ 帧率 ⇒ 采样按 `Time.frameCount` 门控；`get_performance` 为上一帧快照 ⇒ 仅用于单点快查。代码侧新增 `upi/core/models.py`（六条契约门禁，自测 6/6 拦截生效）、`upi/tooling/unity_cli.py`、`upi/tooling/trace.py`、`probe/sampler_v2.cs`；§12 补记 8 项实测事实。附带发现：URP 模板默认配置下 20 个 renderer 得 `batches=0`，合批与 SRP Batcher 均未生效。
- **v0.6**（2026-11-07）：配置层代码落地（`upi/config/settings.py` 双表校验 + 加载时合并实测能力字段 + 通道放行门禁；`upi/config/connectivity.py` 四步测试与失败分类；`scripts/bootstrap_llm.py` 引导入口，含 `--dev` 注册表回退）；开发通道连通性测试四步全通过；**model id 按服务商 `/models` 实测校正为 `deepseek-flash`**（原拟用 id 不在列表内）；§2.3.2 补记 Windows 进程环境可见性限制；§12 新增 4 项实测事实；能力字段回归单一真相来源（实测纪录），配置文件不再保存第二份数值。
- **v0.5**（2026-11-07）：确认密钥注入方式=**系统级持久环境变量**（用户在图形界面自行设置，管线不代设、不收集值，避免密钥进入 shell 历史与进程参数）；确认连通性测试时机=**第一次使用时跑**（`run_on: [first_use]`，另在主动复检或 `model id`/`base_url` 变更时重跑，不随每次改 Key 自动触发）；§2.3.2/§2.3.3/§2.3.4 与 D11、`llm_config.yaml` 同步。
- **v0.4**（2026-11-07）：新增 REQ-9 与 §2.3 模型服务配置层（`llm_config.yaml` 双表、首次使用三分支引导、连通性测试四步与失败分类、密钥仅环境变量引用）；D4 收口为 DeepSeek 官方 / `deepseekv41flash` 默认单模型，新增 D11；§11 M0 补入配置引导与连通性门禁；AGENTS 新增 I12 凭据安全不变量；同模型下 Review 隔离降级为弱隔离并需在报告标注（§2.3.5）。
- **v0.3**（2026-11-07）：D8/D9 确认通过，新增 D10（第一轮交付范围）；M1'、M6' 由"机制演示"升级为第一轮可用件；§11 显式划分第一轮/扩展位并记录两处代价（夹具扩到 8 个场景配置、体检表在第一轮必然不达标）；§7.3、§8.1 措辞同步为已定口径。
- **v0.2**（2026-11-07）：新增 §7 管线自身运行统计与可视化控制台（REQ-7）、§8 防过拟合与泛化验证（REQ-8）；原 §7~§11 顺延为 §9~§12；新增决策 D8/D9；里程碑插入 M1'（埋点与统计底座）与 M6'（体检表接入上线流程）。
- **v0.1**（2026-11-07）：按 D1～D7 拍板结论修订；采集层从"自建 bridge 插件"改为"CLI `eval` 注入常驻采样器"三档方案；新增 §1.1 检测器注册表、§2.2 三个已核实约束（常驻 Editor、Safe Mode 死锁、模块缺失）、§10 环境实测表；里程碑插入 M0'。
- **v0**：初版讨论稿。
