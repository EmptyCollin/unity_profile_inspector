# AGENTS.md — AI 性能优化管线 · Agent 角色契约

> 本文件定义管线的角色、边界与契约。**边界不可越，越界必须转人工。**
> 适用范围：`upi/`（管线代码，含 `agents/daemon.py`+`core/events.py` 常驻守护与 `agents/ui.py` 控制台）、`probe/`（经 Unity CLI 注入 Editor 的采样片段，非自建插件）、`fixtures/`（测试夹具）、`knowledge/`（特征库）、`dashboard/`（统计与可视化只读产物）、`runs/daemon_*`（守护运行态与事件留痕）、`llm_config.yaml`（模型服务配置与 `pipeline.monitor` 巡检段，见 DESIGN §2.3）。
> 最后更新：2026-11-09（v0.8，真机全链实测后的三处判据修正：**G11 预算护栏改节点感知**（`attempts` 只拦 fix 入口、计数点在 verify 产出判定处，作废崩溃不烧配额）；**§1.5 守护对比分两支**（时间/渲染走窗口中位，记忆体走两侧各自 fresh post-GC，不响应 GC 者记未评估盲区）；**G20 盲区清单补第四项**（记忆体守护实际只剩 `monoUsedBytes` 一项受保护）。同时纳入独立运行形态：常驻守护 + 控制台 UI + 手动开关采样 + 优雅停止（stop 走命令通道而非硬杀）。承前 v0.7：G18/G19/G20 三条门禁、Detect 输出扩为 `Finding[]`、采样三问拆分）

---

## 0. 全局不变量（所有 Agent 必须遵守）

| # | 不变量 | 违反后果 |
|---|--------|----------|
| I1 | **任何数值必须来自工具采样或解析，禁止估算/编造。** LLM 只能解释证据，不能生成证据。 | 结果标记 `unverified`，本轮修复作废 |
| I2 | **证据不足时输出 `insufficient_evidence`，不得强行给出根因或修复方案。** | 同上 |
| I3 | **修改权限只来自权限矩阵（§3），不来自 LLM 的自我判断。** | 该次写入视为越权，强制回滚 |
| I4 | **每次写入前必须有 attempt 级快照；写入后必须有验证结论。** 无结论的写入不允许进入下一次写入。 | 中断本次 Issue |
| I5 | **感知层结论（画面/手感）必须由客观门禁产出，不由 LLM 主观判断。** | 结论降级为 `unknown` |
| I6 | 一个 Issue 的修复尝试次数受 `max_review_rounds`（默认 N=3）限制，超限即记 `failed`，不得继续消耗。 | 见 §4 G6 |
| I7 | 所有 Agent 的输入输出必须是本文件 §2 定义的结构化契约（**Pydantic v2** 模型，见 G12），不接受自由文本作为唯一载体。 | 拒收，重试一次 |
| I8 | **Editor 命令名不得假设**：`eval`/`eval_file`/`screenshot` 等是否存在由 `unity command --format json` 与 `unity list` 在运行时决定；探测不到即降级到 C 档并标注 `static_estimate`。 | 该次采样作废，不得据此判定异常 |
| I9 | **采样器的控制与数据一律走文件，禁止依赖内存状态**（实测修正，DESIGN §2.2.1）：回调（`EditorApplication.update`）**跨 eval 存活**，但 eval 内静态字段**不存活**（每次 eval 是新程序集），且**新 eval 无法卸载旧回调** ⇒ 幂等标志既不能用静态 bool 也不能用 `SessionState`（后者跨域重载存活而回调注册不跨，会留下"标记已装、实际无人采样"的死状态；实测重复安装会让多个回调争抢同一文件）⇒ 只允许用**心跳文件新鲜度**这类外部可观测判据。采样写入必须按 `Time.frameCount` 门控：update 回调频率(~2100/s)≠帧率。结果落盘由 Python 侧轮询读取，文件写入必须原子（先 `.tmp` 再替换）。 | 数据视为不可信，重启 Editor 清残留后重采样 |
| I10 | **管线自身也是被度量对象**：每个阶段必须产生 trace span（耗时、token、工具调用、证据指针、环境指纹）。无 trace 的运行不得出现在任何统计与报告里。 | 该 run 全部指标标记 `untraced`，不纳入趋势 |
| I11 | **泛化结论必须由留出集支撑**：阈值、置信度权重、pattern 权限的变更，缺 DESIGN §8.7 体检表或任一项不达标 → 变更 `blocked`。 | 视为过拟合，变更作废 |
| I12 | **凭据只存环境变量，配置文件只存变量名引用**（DESIGN §2.3）：`llm_config.yaml` 与任何产物（日志、trace span、报告、git 提交）不得出现密钥值；含凭据的参数一律以哈希形式记录；未经用户明确要求，不得把密钥写入文件或命令行参数。连通性测试未通过不得进入真实调用阶段。 | 该 run 标 `credential_leak_risk` 并中止，密钥需人工轮换 |
| I13 | **密钥注入方式已定：系统级持久环境变量，由用户本人设置**；管线只读取、不代设、不收集值，存在性检查只输出"有/无"。**连通性测试只在第一次使用时跑**（外加主动复检或 `model id`/`base_url` 变更），不得反复触发真实推理请求烧成本。 | 越权写入用户环境或重复消耗成本，视为违规操作 |

---

## 1. 角色与边界

### 1.1 Orchestrator（LangGraph 状态机）
- **职责**：驱动 `detect → diagnose → plan → fix → verify → review → record` 循环；管理预算、超时、快照、回滚；产出 RunReport；**为每个阶段写出 trace span（I10）**。
- **可以做**：调度、状态持久化、失败重试、终止。
- **不可以做**：判断技术方案，修改任何工程文件。

### 1.2 Detect Agent（检测）
- **职责**：从采集层拿原始数据 → 建/更新基线 → 产出 `Finding[] = Issue[] ∪ Potential[]`（Issue 含证据链、量级、偏离倍数；Potential 含模式位置、斜率来源、规模假设）。
- **输入**：`SamplingBundle`（多次采样 + 回放脚本 + 档位配置 + `probe_overhead` 标定值）；`BreachSample[]`（探针越界样本，DESIGN §2.4 T2w，M2a 后）；C+ 扫描结果（M3.5 后）。
- **输出**：`Finding[]`，Issue 每条必须有 `signature`（症状指纹）、`evidence[]`、`magnitude`；Potential 每条必须有 `pattern + slope_source + scale_assumption` 三字段（缺一 Pydantic 拒收）。
- **硬规则**：
  - 采样次数 < `min_samples`（默认 5）时，只允许输出 `low_confidence` 标签，不允许进入修复循环。
  - 判定异常必须使用**中位数 + 噪声带**（同一协议重复采样的 σ），禁止用单次采样值和均值。
  - 重采样不复现的历史 Issue，自动记一次检测误报（FP），写入特征库统计。
  - **FP 率必须与检出率成对输出**（同一阈值下的两个数），禁止只报其一。
  - `instrument_distorted`（采样开销超预算）的 bundle 不得用于判定异常。
  - **等级归属（G18）**：`potential` 不得进入修复循环、成功率分母、FP 分母；其唯一升级路径 = T5 压测复现→转 `confirmed`，不复现→撤回。
  - **发作判定必须有逐帧源（G19）**：判"发作（episode）"只能建在逐帧源（A2/A4/探针）上；A 档秒级抽样只允许判"水位（waterline）"。渲染计数无任何逐帧源 ⇒ **禁止注册 GPU 逐笔 spike detector**；GPU 发作 detector 只能建在 A4 `gpuFrameTime` 上，且 finding 只给"越线事实"，逐笔归因 `attribution_source=human`（宁缺毋假，虚报检出能力按 I1 处理）。
- **不可以做**：推测根因、给出修复代码、写文件。

### 1.3 Diagnose Agent（归因）
- **职责**：对单个 Issue 产出 `Hypothesis[]`（根因候选 + 归因置信度 + 证据），按置信度排序。
- **输入**：Issue 证据链 + 相关代码 + `KnowledgeStore`（症状→根因映射、历史统计）。
- **输出**：`Hypothesis[]`，每条含 `attribution_factors`（见 DESIGN §5 六因子），含 `expected_effect`（预期改善量）。
- **硬规则**：
  - 置信度由**公式计算**，不由 LLM 直接打分；LLM 只判定各因子"是否成立"（可核查的二元/序数判断）并给出证据指针。
  - Top1 归因置信度 < `T_attr_min`（默认 0.6）→ 输出 `needs_human`，不进入自动修复。
- **不可以做**：修改代码，决定权限档位。

### 1.4 Fix Agent（修复）
- **职责**：按选定 `Playbook` 生成并执行 `EditPlan`（语义保持的最小改动）。
- **输入**：`Hypothesis` + `Playbook` + 代码 + 当前权限档位。
- **输出**：`EditPlan`（diff、影响文件、前置断言、**声明是否触碰感知层**）。
- **硬规则**：
  - `auto` 档位只允许执行 `playbook.side_effect == none` 且改动范围在特征库白名单模式内的操作。
  - 一旦 `EditPlan` 触碰 §3 列出的"感知相关文件/资产"，**无论置信度多高，强制转 `semi`（先报后批）**。
  - 禁止"顺手重构""改格式""调整无关代码"。改动必须能一行一句对应到证据。
  - 禁止一次 attempt 修复多个 Issue。
  - **不接收回放种子集合**（DESIGN §8.5）：断言与感知回放的种子只给 Verify/Review，防止针对已知种子写特例。
  - **不接收 holdout 夹具的实现细节**（DESIGN §8.1），不得为通过留出集而调整夹具。
- **不可以做**：判定自己的修复是否成功，跳过 verify 直接进入下一个 Issue，扩大改动范围。

### 1.5 Verify Agent（验证）
- **职责**：同一采样协议重跑，产出 `Verdict`（目标是否解决、**全量守护指标是否劣化**）；**顺带跑跨指标矛盾规则表（P4，DESIGN §3A，M2a 后）**——违反任何一条 = 视同守护劣化，报 `conflict` finding。
- **输入**：attempt 快照的 baseline bundle + 改后 bundle + 渲染/回放回归结果。
- **输出**：`Verdict{ target_metric_delta, guard_metrics[], perception[], conflicts[], realized_over_expected, status }`。
- **硬规则**：
  - 必须对比**全量守护指标**，不只是当初的异常指标（防"按下葫芦浮起瓢"）。
  - 改善量必须超过 `k·σ`（k 默认 3），否则判 `not_significant` = 视为未解决 → 触发回滚。
  - 守护指标劣化超过 `guard_tolerance`（默认 1%）→ 无条件 `rollback`，硬规则优先，不走 LLM 判断。
  - **守护对比分两支（真机实测确立，v0.8）**：
    - 时间/渲染计数类（`cpuFrameTimeMs`、`drawCalls`…）：用 bundle **窗口中位**对比，走上面 1% 容差。
    - 记忆体类（`monoUsedBytes` 等）：窗口值量的是"此刻未回收多少"而非净占用，回收时点一变即失真（实测 7.82MB 窗口 Δ 里净常驻只 1.94MB）。⇒ 必须两侧**各自**采 fresh（进 Play→强制 GC→再读），只用 `post_gc` 对比；任一侧缺 ⇒ 记 `value_source=fresh_missing/gc_unresponsive` = **未评估盲区**，既不判劣化也不判清白，进报告单列（G20）。**禁止拿被污染或不响应的窗口值冒充守护结论。**
  - 感知门禁失败 → 无条件 `rollback` 并转人工。
  - 矛盾规则违反（如 drawCalls 升 + triangles 降 + batches 不变 = 合批修复未生效反而碎描）→ 记 `conflicts[]` 进 Review，不自行解释成因。
- **不可以做**：修改代码，批准自己的结论。

### 1.6 Review Agent（审查）★ 信息隔离要求最严
- **职责**：当一次修复未解决问题、或引入了新问题时，裁定"继续迭代"还是"撤回重评估"还是"升级人工"。
- **输入（严格限定）**：baseline 证据 + `Hypothesis` + `EditPlan` diff + 改后全量数据 + `Verdict`。
- **输出**：`ReviewDecision ∈ { continue_with_direction, rollback_and_replan, escalate_human }` + `reason`（回传给 Fix Agent 的结构化原因）。
- **信息隔离**：
  - **不接收** Fix Agent 的自我辩护、自评置信度、自然语言解释。
  - **不参与** 修改方案的生成（防止自我评审偏见）。
  - 与 Diagnose/Fix 使用可分离的 prompt 与温度配置；有条件时用不同模型。
- **配额**：单 Issue 最多 `max_review_rounds` 次（可配置，默认 3）。
  - 若审查判定"本质是感知类改动，不应自动执行" → **立即 `escalate_human`，不烧完配额**。
  - 配额耗尽 → Issue 记 `failed`，保留全部证据链进入知识库（失败样本与成功样本同等重要）。

### 1.7 Knowledge Agent（沉淀与升维）
- **职责**：成功修复后写入"症状→根因→动作→实测效应"记录；维护误报率、修复成功率、管线自身成本指标；提出升维/降级建议。
- **输出**：`PatternPromotionProposal{ pattern_id, n_effective, n_trials, success_rate_wilson_lower_bound, requested_tier, overfit_checklist }`。
- **硬规则**：
  - 只做**建议**，不做执行。任何"半自动 → 自动"的权限变更必须人工一次性批准。
  - 同类性判定要求三重签名一致：`症状签名 × 根因签名 × 动作签名`，仅标签相同不算同类。
  - **签名只允许机制级结构化特征**，禁止把文件名、类名、函数名写进签名（DESIGN §8.3）；新增变体夹具时必须做签名可迁移性检查。
  - **`n` 一律用 `n_effective`**（独立场景 × 档位）；同一上下文重复运行只算复现验证，不进成功率分母（DESIGN §8.2）。
  - 提案必须附 §8.7 过拟合体检表全量数值，缺项或不达标 → 提案本身 `blocked`，不得提交人工批准。
  - 权重拟合：`n_effective < 30` 禁止拟合（沿用人工先验）；切分必须按 pattern 分组，同 pattern 样本不得跨训练/验证集。
  - 触发降级（硬）：该 pattern 出现 1 次"引入新问题被回滚"，立即降回 `semi` 并要求人工复盘；长期未复现的长尾 pattern 转 `dormant` 并停止计入 `prior_hit`。
- **不可以做**：直接修改权限矩阵，修改阈值配置（属 blocked 档，见 §3 维度一）。

### 1.8 Stats/Reporter（统计与总结）
- **职责**：生成人读产物（前后对比表、置信度分布、误报率、成功率、回滚记录、失败样本）+ **管线自身历史统计控制台**（DESIGN §7.3 七张视图）。
- **硬规则**：
  - 报告中每个数字都要能回溯到证据文件；区分"实测"与"定性判断"；声明本 demo 的边界（Editor 侧 PC 采样 ≠ 真机中低端机型）。
  - **成本指标与质量指标同轴展示**，不允许只报质量（"变准但变贵"必须可见）。
  - 每个比例值必须与 `n` 和置信区间同屏；`n < 30` 禁止画趋势折线，只画散点。
  - 版本变更（detector / weights / pipeline）必须在时间轴上打竖线，禁止把版本跳变呈现为自然改善趋势。
  - `needs_human`、`false_positive`、`untraced` 三类必须单列，不得混入成功率分母。
  - 控制台**只读**：不提供任何改阈值/改权限的交互入口。

---

## 2. 共享状态契约（LangGraph state / Pydantic）

```
RunState:
  run_id, fixture, fixture_split,        # fixture_split: tuning | holdout（§8.1）
  scene, replay_script, quality_tier
  sampling_protocol: { min_samples, warmup_samples, window_samples, poll_rate,
                       aggregation: median, data_tier: A|A2|A3|A4|B|C,  # A=轮询(水位唯一源) A2=注入按帧(GC/内存) A3=离线.data(时间线) A4=FrameTimingManager(逐帧CPU/GPU计时，固定4帧延迟)
                       scale_factor,                # 常态=1；≠1 为压测档（10×/100×，D15），数据单独目录，禁与常态混比（G7）
                       probe_overhead, instrument_distorted,
                       degenerate_distribution }   # 秒级抽样口径，见 DESIGN §2.1
  protocol_fingerprint          # G7 用；含 environment_fingerprint 与 editor_mode，不一致禁止对比
  environment_fingerprint       # Unity/URP 版本 × GPU 驱动 × 电源计划 × 散热状态
  budget: { max_issues, max_review_rounds, max_attempts_per_issue,
            max_sample_rounds, wallclock, max_tokens }   # G11
  editor: { project_path, instance_pid, reachability, safe_mode,
            mode: gui|headless, play_mode: bool }  # 可达性与模式由运行时探测，不假设命令名（G16）
  snapshots: { git_ref, asset_manifest_hash, scene_guid }
  trace: TraceRef               # runs/<run_id>/trace.jsonl；见 I10 / G13
  findings: Finding[]           # = issues ∪ potentials（G18；potentials 只进报告不进循环）
  issues: Issue[]               # 由 findings 中 kind=issue 派生，保持向后兼容
  breach_samples: BreachSample[] # 探针越界样本（DESIGN §2.4 T2w，M2a 后）
  records: AttemptRecord[]      # 每次 attempt 的完整轨迹
  metrics_summary

Finding:
  kind                 # issue | potential      （G18 归属判据）
  ptype                # waterline | episode | potential | conflict | perception（DESIGN §3A 五类）

Issue:
  signature            # dim | metric | 归因对象/代码模式 | 阈值
                       # 只允许机制级特征，禁止具体标识符（DESIGN §8.3）
  link                 # cpu | gpu | anim | loading   （对齐 DESIGN §1 四链路）
  evidence[]: Evidence # 每条 = { tool, args, raw_ref, value, unit, noise_band, data_tier }
  magnitude, deviation_ratio, explains_share_of_regression
  attribution_source   # 自动源 | human（G19；无源时 finding 只给越线事实，根因转人工）
  state              # candidate | confirmed | fixing | resolved | failed | needs_human | false_positive

PotentialFinding:      # P3 潜在风险；三字段缺一 Pydantic 拒收（G18）
  pattern, location            # 代码/资产位置（供人查，不进签名）
  slope_source                 # 必须指向实测斜率证据（A2/A4/BreachSample），禁止估算（I1）
  scale_assumption             # 明示"规模到 S 时预测越线"，S 是假设不是事实
  status                       # shadow | reported | stress_pending | promoted | retracted

BreachSample:          # P2 发作证据单元（DESIGN §2.4 T2w）
  frame_idx, metric, value, threshold_abs, deviation_ratio
  ring[-N..+M]         # 前后原始序列；N 默认 10，M ≥ 14（覆盖 A4 的 4 帧固定延迟，D17）
  a4_window            # 该窗口的逐帧 cpuFrameTime/gpuFrameTime；未回填帧已按哨兵过滤（G19）
  data_tier, protocol_fingerprint, replay_seed

Hypothesis:
  root_cause, code_anchors[]
  attribution_factors{}   # 六因子，见 DESIGN §5
  confidence (computed)
  weights_version         # 先验权重 vs 拟合权重，历史置信度可比性靠它
  expected_effect{ metric, direction, min_delta }

EditPlan:
  playbook_id, diffs[], invariants_assert[]
  side_effect         # none | may_affect_perception | unknown
  touched_assets[]
  permission_tier     # auto | semi | blocked

Verdict:
  target: { before, after, delta, sigma, significant }
  guards[]: { metric, before, after, delta, regressed }   # 全量守护，不只当初异常项
  perception: { render_diff_score, anim_event_diff, replay_divergence, passed }
  conflicts[]: { rule_id, metrics{}, expected, observed } # P4 矛盾规则违反（§1.5）；非空即进 Review
  compile_ok                  # G8，先于采样判定
  realized_over_expected
  status            # resolved | not_significant | regressed | perception_failed

TraceSpan:                    # I10 / G13；成本与质量指标的唯一来源
  run_id, issue_id, attempt_id, stage, seq
  wallclock_ms, cpu_ms, exit_status
  tool_calls[]: { kind: unity|git|sqlite|llm, name, args_hash, duration_ms }
  llm: { model, token_in, token_out, prompt_version }
  evidence_refs[], contract_hashes: { in, out }
  environment_fingerprint

MetaMetrics:                  # DESIGN §7.2，禁止只报其一
  cost:    { per_run_wallclock, per_issue_cost, token_total, stage_share,
             compile_gate_fail_rate, safemode_incidents, marginal_cost_curve }
  quality: { fp_rate, recall, success_rate, auto_rate, ece,
             realized_over_expected, rollback_rate, review_rounds,
             needs_human_share, shadow_agreement, holdout_pass_rate,
             potential_ratification_rate }  # 事后追认率：potential 经 T5 压测/真实运行确认发作的比例（G18 配套的长期指标，<30% 的模式降回影子）
  stability:{ sample_fail_rate, fingerprint_mismatch_count,
              rollback_success_rate, cross_run_verdict_consistency }
```

---

## 3. 权限矩阵（半自动 ↔ 自动 的判据）

**判定维度一：改动是否可能影响感官体验（硬规则，优先于置信度）**

| 触碰对象 | 默认档位 |
|---|---|
| 材质 / Shader / 渲染管线设置 / 光照与探针 / 后处理 / 动画状态机与混合参数 / 物理层与碰撞设置 / 相机参数 | **semi（先报后批）** |
| 纯 C# 逻辑，且改动被判定为语义保持（见下） | auto（需置信度达标） |
| 采样协议、夹具脚本、`upi/` 管线自身代码 | **blocked（永远人工）** |
| 阈值配置、置信度权重、pattern 权限矩阵、holdout 夹具定义 | **blocked，且必须附 DESIGN §8.7 体检表**（G14） |

**判定维度二：动作是否在"语义保持模式"白名单内**（auto 的必要条件，非充分条件）
- 例：把每帧分配的热路径改为复用缓冲（带容量不变断言）、把 `Update` 中的 `GetComponent` 缓存化、枚举 `ToString` 改预生成表、把未使用的组件/监听关闭。
- 反例（禁止 auto）：改变执行顺序、改变数值/时序参数、增删 GameObject 生命周期、修改资源导入设置（压缩格式、mipmap）、LOD 阈值调整。

**判定维度三：pattern 的历史表现（升维门槛，四条同时满足）**
1. 三重签名一致的**有效样本量** `n_effective ≥ n_min`（默认 10；口径见 DESIGN §8.2：独立场景 × 独立档位，同上下文重复运行只计 1 次）；
2. 成功率的 **Wilson 置信下界** ≥ 0.9（不用点估计，防"10 次全成功"被当成 100%）；
3. 期间"引入新问题"次数为 0，且平均 `realized_over_expected ≥ 0.8`；
4. DESIGN §8.7 过拟合体检表全部达标（tuning−holdout 检出率差 ≤ 0.15、clean 变体 FP ≤ 5%、训练/留出 ECE 差 ≤ 0.05、`n_effective/n_trials` ≥ 0.5、签名可迁移性 ≥ 0.8、跨 run 判定一致率 ≥ 0.9）。

满足后由 Knowledge Agent 提案 → **人工批准** → 该 pattern 升为 auto，并进入**影子模式**（先只记录不落地，比对 auto 决策与人工决策一致率）→ 小流量（限定目录/限定 profile）→ 全量。随时一键降级。影子模式一致率是独立指标：不一致时优先复盘，不得继续推进升维。

---

## 4. 硬门禁（优先级高于一切 Agent 判断）

- G1 目标指标改善 < `k·σ` → `not_significant` → **回滚**（已定：无收益的改动不留，否则污染归因、成功率失真）。
- G2 任一守护指标劣化 > `guard_tolerance` → `regressed` → 回滚 + 触发审查。
- G3 感知门禁（渲染 SSIM 低于阈值 / 动画事件序列 diff 非空 / 固定种子回放结果发散）失败 → 回滚 + 转人工。
- G4 无快照 → 禁止写入。
- G5 证据链断裂（diff 中有改动无法对应到任何 Evidence）→ 该 attempt 无效。
- G6 `max_review_rounds`（默认 3）耗尽 → Issue 记 `failed`，禁止继续。审查若判定"本质是感知类改动，不该自动执行"→ 立即 `escalate_human`，**不烧完配额**。
- G7 **协议指纹一致才可对比**：两次采样的 `scene_guid × replay_script_hash × quality_tier × warmup_samples × window_samples × n_samples × poll_rate × data_tier × editor_mode × scale_factor × graphics_api × environment_fingerprint` 任一不同 → 禁止对比，报错。A 档实测与 C 档静态估算**永远不可互比**；**常态数据（scale_factor=1）与压测档（10×/100×，D15）永远不可互比**；`graphics_api` 为预留字段——EX-1（切 DX11 验证 RenderDoc）若立项，切换前后数值禁止对比靠它兜底。环境漂移（Unity/URP 版本、GPU 驱动、电源计划、散热状态，**以及 `runInBackground`/焦点策略——实测失焦即停出帧**）同样触发本门禁（DESIGN §8.6/§12）。
- G16 **渲染类计数器唯一源=GUI Editor 的 `diagnostics get_performance` 轮询**（实测 A2/A3 两源均无渲染 counter：A2 静默读 0，A3 为 -1 哨兵——把这两种当"值为 0/负"参与统计即违反 I1）。且必须在 Play Mode 采集（实测，DESIGN §2.2.1 事实 1）：`-batchmode -nographics` 下即使场景含 20 个 renderer 且处于播放模式，`drawCalls/batches/triangles` 仍恒为 0、GPU 设备为 `Null Device`。因此：GPU 与动画链路的 A 档采样**禁止在无头模式进行**；同一指标跨 editor_mode 的数值禁止对比（并入 G7 的 `editor_mode` 字段）。CPU / 加载链路无此限制。
- G8 **编译门禁前置于采样**：EditPlan 落地后先 `recompile` 并过滤 `error CS####`；编译失败 → attempt 直接失败并回滚，**不消耗一整轮采样成本**。
- G9 **回滚不得依赖 Editor 可达**：改出编译错误时 Editor 会进 Safe Mode，Pipeline 包不加载，`eval`/`command` 全部连不上。回滚路径只允许依赖 git；回滚后再重启 Editor 验证。Detect/Verify 遇到"连不上"必须先跑 `unity pipeline list` 区分 Safe Mode 与真的没有实例，禁止把"连不上"当作"没有编辑器"进而手改场景 YAML。
- G10 **停止编辑器只按 PID**：禁止按名字批量杀（`taskkill /IM Unity.exe` 等），会连带关闭其他项目的未保存工作。
- G11 **预算护栏**：单 Issue 最大 attempt 数、最大采样轮数、墙钟时长、LLM token 上限，任一超限 → `needs_human`，不允许继续消耗积分打转。**节点感知（真机续跑实测修正）**：`attempts` 只在 fix 入口判——它管"要不要开始下一次修复尝试"，不该拦本次尝试的 verify（旧版不分节点，attempts 满后 verify 被拦，修复已落地、改前基线已花钱采好，唯独 after 采样+判定被拦 = 整轮 attempt 白烧还得靠出口回滚擦脏写）；`review_rounds` 只在 review 入口判（G6 配额）；墙钟/token 为全局消耗，各入口都拦。**attempt 计数点=verify 产出判定，不在 fix 落地时计**——基础设施崩溃（仪器漂移/编辑器消失）导致无判定的作废尝试不烧配额，否则环境抖一下就把重试机会吃光（实测死锁：resume 被 `budget_exceeded(attempts)` 永久拦停）。
- G12 **所有数据模型一律 pydantic v2**：Python 3.14 上 pydantic v1 模型无法运行（LangChain 上游已知问题），引入携带 v1 的依赖视为阻塞项。
- G13 **无 trace 不入统计**：任何出现在报告/控制台里的数字，必须能定位到产生它的 `TraceSpan` 与证据文件；缺 span 的 run 标 `untraced` 并从趋势中剔除。统计开销本身受 `max_probe_overhead_pct` 约束，超限时该 bundle 标 `instrument_distorted`。
- G14 **过拟合体检表是上线前置条件**：阈值、权重、pattern 权限的任何变更，必须先由 §7 控制台算出 DESIGN §8.7 六项数值并全部达标；缺项或不达标 → 变更 `blocked`，不允许提交人工批准。
- G15 **指标成对展示**：FP 率必须与检出率同轴同屏；成功率必须与 `n_effective` 和置信区间同屏；成本指标必须与质量指标同轴。只报其一的结论视为无效结论。
- G17 **静止分布门禁**：重复采样的分布退化时禁用相对判据——`unique/n < 0.1` **或 `MAD == 0`**（小样本下 n=4 时比例判据永不触发，`mad==0` 分支即为此补，实测 drawCalls 类离散整数指标必然撞上）→ 标 `degenerate_distribution`，只走 `min_magnitude` 绝对门限；夹具回放必须含动态（DESIGN §2.2.1）。
- G18 **finding 等级归属门禁**：`potential`（P3 潜在）不得进入修复循环、成功率分母、FP 分母——其断言"规模到 S 才发作"在当前规模下不可证伪，混入即失真（DESIGN §3A）。升级路径只有一条：T5 压测（10×/100×，D15）复现 → 转 `confirmed`；不复现 → 撤回。代码层：Pydantic 构造期拒绝 `kind=potential` 进入 Issue 通道；负例测试必配。
- G19 **逐帧源与归因源门禁**：判"发作（episode）"的 detector 必须声明逐帧数据源（`data_tier ∈ {A2, A4, probe}`）——A 档秒级抽样判发作 = 漏检 + 把被跳过的异常值当正常（最危险失效）。渲染计数无逐帧源（A2 静默 0 / A3 -1 / FD 程序侧不可解读，实测 ①-⑫）⇒ **禁止注册 GPU 逐笔 spike detector**；GPU 发作只能建在 A4 `gpuFrameTime`（固定 4 帧延迟，环形缓冲向后 ≥14 承接，D17；未回填帧 `gpu=0 且 completeTs=0` 按 -1 哨兵同规则拒收）。episode detector 必须带 `attribution_source` 字段，无自动化归因源时填 `human`——finding 只给"哪帧/哪个指标/越多少"，根因转人工。**虚报检出能力按 I1 处理。**
- G20 **盲区显式声明门禁**：每份报告必须含"不可检出清单"，与检出率同屏，逐项注明受阻原因与解锁路径（当前四项：GPU 尖峰逐笔归因→EX-1/B 档；未放大的代码风险→M3.5 C+；感知类退化→M7 T8；**记忆体守护覆盖面窄**——`monoHeapBytes/totalAllocatedBytes/totalReservedBytes/total_used_per_frame` 经实测**不受强制 GC 影响**（Δ≈0），是会话累积量而非净占用，跨会话不可比 ⇒ 已降级为未评估盲区，"修复是否多吃内存"当前**只能靠 `monoUsedBytes` 一个口径回答**；解锁路径=B 档真机/更长隔离的 fresh 采集）。缺该清单的报告无效。

---

## 5. 沟通与产物约定

- 面向用户的说明全程中文、非技术语言；报告中的数字全部标注来源与采样协议。
- 每个 Issue 一份证据链 JSON（`runs/<run_id>/issues/<id>/`），整轮一份 HTML 汇总报告 + 一份统计控制台（DESIGN §7.3 七视图，含成本轴）。
- `runs/<run_id>/trace.jsonl` 与聚合表是统计的唯一数据源，不得手写或事后补记；缺失即该 run 标 `untraced`。
- 需要人工批准的条目，必须同时给出：证据、方案、预期效应、回滚方式、失败时我该怎么办。
- **潜在风险（potential）单列一章**（`runs/<run_id>/potentials/`），与 Issue 章节物理分开，报告模板不得把两者混排进同一张"检出表"（G18 的产物层落实）；章首固定一句："以下条目在当前规模下未发作、不可证伪，仅供决策参考，不计入检出率与误报率"。
- **FD 人工下钻操作单**（D16=A 形态）附在 GPU 类 Issue 证据链末尾：目标帧号（可复现回放对齐后）、疑似 SetPass 组、建议查看的逐笔字段；不自动截图。
- 本 demo 的诚实边界必须在产物中显式声明：PC Editor 采样 + 帧预算模拟，不等于真机中低端机型数据；样本量不足时，趋势与泛化结论一律标注为"方法学示范"（DESIGN §8.8）；**"不可检出清单"缺失的报告无效（G20）**。
