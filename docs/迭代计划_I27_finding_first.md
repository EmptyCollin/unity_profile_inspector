# I27 迭代方案：finding-first · window 贯穿 · diagnose 补证回路

> 基线：`i25-ext-stage` @ `43c1f2f`（per-replay Play cycle + warmup 已落地）
> 目标不是“再跑一次 11case”，而是把主链从 `candidate → unit → contract` 掰回
> `finding(window) → diagnose(可补证) → plan(window) → verify(window)`。
> 本迭代完成前不重跑 11case，不做阈值调参。

## 0. 目标与非目标

### 目标

1. `Finding` 成为一等工作单元：窗口 + 指标 + 量级 + 证据，不含 root_cause。
2. `window` 以精确 id（`cmd-10-open_shop`）贯穿 finding → candidate → patch → verify，
   不再退化成动作名，也不再取“最后一次出现”。
3. `diagnose` 内部使用 candidate；candidate 必须挂在 finding 上，并继承/缩窄窗口。
4. `diagnose` 证据不足时，能向 detect/采集层发结构化补证请求，拿到 `EvidenceDelta` 后
   再次诊断；有轮数/去重/预算上限。
5. `verify` 用精确窗口做前后配对；绝对目标与相对改善分账；control window 检出环境漂移。
6. Pass2 的 `.data` 要么成为诊断证据，要么明确 fail-loud，不能再是断头路。

### 非目标

- 不改“所有 replay 命令都是 window”的定义。
- 不把性能判定建在深采上（深采只提供病证）。
- 不用 JSON 替代 `.data`。
- 不在本迭代重跑完整 11case / 调 verified 阈值。
- 不引入新的采样后端；沿用现有 probe/capability_run。

## 1. 目标架构

```text
┌──────────────── capture / detect ────────────────┐
│ replay（每 replay 前后 Play cycle + warmup）      │
│   → action_windows.json（cmd-i-type, frame, series）│
│   → unified_probe / evidence_v2                   │
│   → Finding[]（window + metric + magnitude + refs）│
└───────────────────────┬──────────────────────────┘
                        │ findings
                        ▼
┌──────────────── diagnose ─────────────────────────┐
│ 输入：Finding（含 window）+ 该窗口证据             │
│ 内部：Candidate[]（root cause 假设，按 finding 排）│
│ 输出：Hypothesis[] 或 EvidenceRequest[]            │
└───────────────┬───────────────────────┬───────────┘
                │ hypothesis            │ request（回边）
                ▼                       ▼
┌──────── plan / fix ────────┐   ┌── detect.fulfill ──┐
│ RepairContract.window_ids  │   │ window_series      │
│   = finding/candidate 精确 │   │ deep_window(.data) │
│   window；必填             │   │ paired_repeat      │
│ Patch.target_windows[]     │   │ control_window     │
└────────────┬───────────────┘   └─────────┬──────────┘
             │ patch                       │ delta
             ▼                             ▼
┌──────────────── verify / acceptance ───────────────┐
│ 精确 window；before 3 / after 3；control window    │
│ relative + absolute + environment 三轴              │
│ 失败 → 结构化原因回 fix；缺证据 → request 回 diagnose│
└────────────────────────────────────────────────────┘
```

**回边定义**：`diagnose` 不直接调用 Unity；它只产出 `EvidenceRequest`，
由 orchestrator 调 `detect.fulfill(request)`，再把 `EvidenceDelta` 喂回 `diagnose`。
这样 detect/diagnose 的职责不混，回路可测试、可 trace、可限流。

## 2. 数据契约（Pydantic v2，全部 `extra="forbid"`）

### 2.1 WindowRef

```python
class WindowRef(BaseModel):
    window_id: str          # "cmd-10-open_shop"（唯一身份）
    action: str             # "open_shop"
    index: int              # 10
    frame_start: int
    frame_end: int
```

规则：

- `window_id` 是唯一身份；`action` 只用于展示/分组，**不再用于索引窗口**。
- 同一 action 多次出现（open_shop 10/28）必须是两个不同 WindowRef。
- `frame_start/frame_end` 来自 `action_windows.json`，不可用 `peak_frame` 反推。

### 2.2 Finding（detect 产物，不含 root cause）

```python
class Finding(BaseModel):
    finding_id: str
    run_id: str
    project_id: str
    window: WindowRef
    metric: str
    unit: str
    observed: dict          # median/max/n/series_ref
    reference: dict         # baseline? absolute target? target_source?
    magnitude: float
    deviation_ratio: float
    data_tier: str          # A2/A4/...
    evidence_refs: list[str] # counters.json / .data / evidence_v2
    state: Literal["discovered","diagnosing","patched","verified",
                   "improved_partial","needs_recheck","semantic_risk",
                   "environment_drift","insufficient_evidence","failed"]
    created_at: str
```

约束：

- **禁止** `root_cause / mechanism / candidate_id` 字段。
- 一个 `(window_id, metric, shape)` 一条；相邻窗口同 metric 在 detect 内 cluster 去重。
- 资产类 finding 也走同一模型（window 可为空但显式 `kind="asset"`），不另开后门。

### 2.3 Candidate（diagnose 内部）

```python
class Candidate(BaseModel):
    candidate_id: str
    finding_ids: list[str]       # 至少 1
    window_ids: list[str]        # ⊆ union(finding.window_ids)，或 scope="global"
    scope: Literal["window","global"] = "window"
    root_cause: str
    mechanism: str
    kind: str
    anchors: list[Anchor]
    score: float
    confidence: float
    evidence_refs: list[str]     # 含 .data
    state: str
```

约束：

- `window_ids` 为空且 `scope != "global"` → 拒收。
- candidate 的窗口必须与 finding 窗口相交；否则标 `window_mismatch`，不得进入 plan。
- 一个 candidate 可服务多个 finding（同一根因、重叠窗口），为后续合并做准备。

### 2.4 EvidenceRequest / EvidenceDelta（回边契约）

```python
class EvidenceRequest(BaseModel):
    request_id: str
    finding_id: str
    window_ids: list[str]
    kind: Literal["window_series","deep_window","paired_repeat","control_window"]
    missing: list[str]        # 缺什么：per_frame / alloc_site / callstack / noise
    reason: str
    expected: str
    budget: dict              # max_wallclock_s, max_captures
    request_hash: str

class EvidenceDelta(BaseModel):
    request_id: str
    available: bool
    kind: str
    data_tier: str
    refs: list[str]           # .data / counters / window json
    summary: str
    rows: dict = {}
    reason: str = ""
```

回边规则：

- 每 finding 最多 `max_evidence_rounds=2`；同一 `request_hash` 重复 → 停止。
- `deep_window` 每 finding 最多 1 次；必须复用该 window 的精确 index。
- `available=False` 或没有新增根候选 → `insufficient_evidence`，不无限重采。
- 每轮写 `evidence_round_<n>.json` + trace span（request、delta、耗时、refs）。

### 2.5 RepairContract（精确窗口必填）

```python
class RepairContract(BaseModel):
    window_ids: list[str]        # 必填，非空；精确 cmd-i-type
    primary_metric: str
    min_improve: float
    guards: list[GuardBudget]
    absolute_target: dict | None # {metric, value, source}
    mb_per_ms: float = 8.0
```

- 空 `window_ids` → Pydantic 拒收 / plan 失败，不再静默 `unmeasured`。
- 禁止把 `window_ids` 退化成 action name；planner 提示词直接给 finding.window_id。

### 2.6 Acceptance / Verdict

```python
class AcceptanceUnit(BaseModel):
    unit_id: str
    finding_ids: list[str]
    candidate_ids: list[str]
    patch_ids: list[str]
    target_windows: list[WindowRef]   # 并集
    control_windows: list[WindowRef]  # 不受本组补丁影响
    merged_reason: str                # overlap / same file / LLM 判定

class Verdict(BaseModel):
    unit_id: str
    relative: dict    # before/after median, sigma, delta, threshold, status
    absolute: dict    # observed, target, target_source, status
    control: dict     # control window delta, tolerance, environment_status
    semantic: dict    # empty_replace / behavior / perception
    status: Literal["verified","improved_partial_absolute_unmet",
                    "improved_partial_absolute_unassessed","not_significant",
                    "regressed","semantic_risk","environment_drift",
                    "insufficient_evidence","needs_human"]
```

## 3. 工作流

### 3.1 detect：per-window finding

1. 读 `action_windows.json` + `counters.json`；
2. 对每个 window 的每个 metric 计算 observed vs reference；低于噪声/门限不产 finding；
3. 相邻窗口同 metric 做 cluster（同 shape）去重；
4. 产 `Finding[]`；不再先造全局 `window_peak` Issue；
5. 资产/静态机会产 asset finding（显式 kind，不混入函数 finding）。

### 3.2 diagnose + 回边

```text
diagnose(finding, initial_evidence):
    candidates = build_candidates(finding, evidence)      # 可含 .data
    hypotheses = judge(candidates, window_series)
    if sufficient(hypotheses):
        return hypotheses
    requests = make_requests(missing_factors)
    return requests
```

orchestrator：

```text
for round in 1..2:
    out = diagnose(finding, deltas)
    if hypotheses: break
    for req in out:
        delta = detect.fulfill(req)
        deltas += [delta]
    if no new evidence: stop
if still insufficient: finding.state = insufficient_evidence / needs_human
```

`detect.fulfill` 的四种实现：

| kind | 做什么 | 产出 |
|---|---|---|
| `window_series` | 从 `action_windows.json`/`counters.json` 取该 window 全 series | series ref |
| `deep_window` | `profile_windows` 只采该 window，`profile_window.py` 解包 `.data` | alloc/site/callstack refs |
| `paired_repeat` | 对该 window 做 1 次同协议浅采（预算 1 次） | 第二次 series |
| `control_window` | 读 control 窗口 series | control 对照 |

### 3.3 plan / fix

- plan 输入：Finding（window_id）+ Hypothesis + candidate_card（含窗口 series）。
- `RepairContract.window_ids` 取 finding/candidate 精确 id；planner 不得改写成 action name。
- Patch 带 `target_windows[]`；多文件补丁取并集。

### 3.4 verify / acceptance

1. **T0**：初始定性浅采（1–3 次），只做“状态指纹”，不作为最终 baseline。
2. **分组**：
   - target window 相交 → 同一 acceptance unit；
   - 改同一文件/符号/资产（即使 window 不相交）→ 同一 unit；
   - overlap 图确定性生成；LLM 只在“重叠/相邻且疑似同根因”时决定合并 patch，且必须记录理由；
   - 不相交的 unit 才进入不同 group。
3. **每组**：
   - before 3 次（本组 patch 前，贴近 after）
   - apply + compile
   - after 3 次（同协议；每 replay Play cycle + warmup）
   - control window 每次采集都带
4. **三轴判定**：
   - relative：`delta > kσ / min_magnitude`
   - absolute：observed 达到 target（预算/标定/clean reference）
   - environment：control delta 超过 `max(3σ, 1%)` → `environment_drift`，不判补丁
5. **状态**：
   - rel 显著 + abs 达线 + env ok → `verified`
   - rel 显著 + abs 未达线 + env ok → `improved_partial_absolute_unmet`（可保留，finding 不关）
   - rel 不显著 → `not_significant` / `needs_recheck`
   - abs 无 target → `unassessed`（G20 盲区）
   - semantic_risk → 不进入 verified
6. **失败回传**：结构化 `{window_id, metric, delta, threshold, failing_patch_ids, kind}` 给 fix；
   需要新证据则走 `EvidenceRequest` 回 diagnose。

### 3.5 record / review / knowledge

- 每 unit 完成即 append `counterfactual.jsonl`（不再最后一次性写）。
- `repair_tree` 保留 status + reason + window + metric_delta；不把 `unmeasured` 压成 `no_effect`。
- 失败样本进 knowledge（prior_hit 统计），成功样本写 pattern；本迭代先落“记录”，不自动升维。

## 4. 任务分解

### M0 契约与窗口身份（P0，先做）

- [ ] `WindowRef`、`Finding`、`Candidate`、`EvidenceRequest/Delta`、`RepairContract`、`Verdict`
- [ ] `CandidateV2` 增加 `window_ids`；raw `merged_candidates` 往返不丢
- [ ] `ToolFinding` 增加 `window` / `magnitude` / `threshold`（或替换为 Finding）
- [ ] 旧 `window: str` 双读迁移：新字段优先，旧字段只读，日志标 deprecated
- 出口：model round-trip 测试；window_id 不丢；`extra=forbid` 负例。

### M1 detect 产 Finding（P0）

- [ ] per-window anomaly（frame/GC/memory/drawcalls）
- [ ] 相邻窗口 cluster 去重
- [ ] `detect` 返回 `Finding[]`；删除/降级 `_issue_from_run_counters`
- [ ] 资产 finding 进入同一模型
- 出口：3 个 baseline 的 finding window 集稳定；每 finding 有 evidence_refs。

### M2 diagnose + 回边（P0）

- [ ] diagnose 只接收 Finding（含 window）
- [ ] cards/prompt 带 window_id + 窗口 series
- [ ] `EvidenceRequest` 生成、hash 去重、max 2 轮
- [ ] `detect.fulfill` 四类实现；`deep_window` 消费 `.data`
- [ ] trace span / evidence_round 落盘
- 出口：无证据时能补一次并改变 hypothesis；重复请求自动停；不可用 → insufficient。

### M3 plan / fix（P0）

- [ ] plan 输入 finding.window_id；提示词禁止 action name
- [ ] `RepairContract.window_ids` 必填并校验
- [ ] candidate → patch 的 target_windows 继承
- 出口：空 window 100% 拒收；单测覆盖。

### M4 verify / acceptance（P0）

- [ ] 精确 window_id 查表，删除 `index_for_window` last-occurrence 路径
- [ ] before 3 / after 3 同组配对；warmup + per-replay Play cycle
- [ ] control window + environment_drift
- [ ] relative/absolute 双轴 + improved_partial_absolute_unmet
- [ ] 失败结构化回传
- 出口：fixture 中逐 unit 可复现 verdict；control 漂移能阻断判定。

### M5 Pass2 消费（P1）

- [ ] `two_pass.json` status/raw_missing/stderr 落 `two_pass_error.log`
- [ ] `profile_failed` / raw_missing → 阻断或重试；不得静默继续
- [ ] deep_window 通过 `profile_window.py` 解析 `.data`，refs 进 candidate/finding
- [ ] 保留 `.data` 原始证据；JSON 只做索引
- 出口：一个 window 的 `.data` 能改变 diagnose 的 candidate 排序。

### M6 协议 / 环境 / 编排（P1）

- [ ] `protocol.FIELDS` 增加 reset_mode / warmup_s / focus / run_in_background / play_session
- [ ] `capture_env.json` 被 fingerprint/verdict 读取
- [ ] `ensure_ready` 等待/重试 + 已有 PID 检查，禁止重复起 Editor
- [ ] `profile_windows` stop_play 放 finally
- [ ] `fresh_play` 下沉到所有 capture 入口（不只 main）
- 出口：新旧协议指纹不可比；环境字段可在 verdict 中回溯。

### M7 清理 / 资产 / 报告（P2）

- [ ] asset_findings 接入 asset unit；opportunities/potentials 有消费者或删除
- [ ] multi_repair/repair_queue、claims.json、profile_window.py 明确保留/删除
- [ ] score_run 移到 bench/，产品模型不带 paper
- [ ] exam_report 补 elapsed/cost；失败样本单列
- 出口：无“写了没人读”的产物；报告成本/质量同轴。

## 5. 测试计划

### 单测（离线）

- WindowRef round-trip；`cmd-10` 与 `cmd-28` 不互相覆盖。
- Candidate.window_ids 为空/非交集 → 拒收。
- Finding 不含 root_cause（负例）。
- EvidenceRequest hash 去重、max 2 轮、available=False 停止。
- plan 空 window → 拒收。
- verify 空 window → fail-loud，不返回 unmeasured。
- control delta 超阈值 → environment_drift。
- absolute met/unmet/unassessed 与 relative 组合的状态表。
- full-shallow `window_counters` 返回 31 窗口且 series 非空。

### 集成（离线 fake capture/editor）

```text
one capture → Finding[2] → diagnose(request) → fulfill(delta)
→ diagnose(hypothesis) → plan(contract.window_ids)
→ patch → before/after 3 → verdict
```

### E2E（真实 Unity，1–3 个 unit）

- 单 window 采集 → finding → 补证（deep_window）→ plan → patch → before/after → verdict。
- 不跑 11case；通过后再开 3case，最后才 11case。

## 6. 迁移与兼容

- `window: str`（action name）保留一个迭代：读旧写新；verify 只认 `window_ids`，
  旧字段用于展示；日志标 `deprecated_window`。
- 旧 run 产物（action_windows 无 series / 旧 candidate 无 window）不可用于新 verify；
  G7 指纹变化后自然不可比。
- `Issue` 暂时保留 Pydantic 兼容；detect 不再产出，plan 逐步切到 Finding。

## 7. 风险

1. **per-replay Play cycle 冷启动方差**：warmup + 3 次 baseline 并集；单次候选可能 10 而不是 11。
2. **before/after 时间成本**：每组 6 次采集；用 group 合并减少组数，不牺牲配对。
3. **环境漂移**：control window 是硬门禁；control 漂移时不出 verdict，重采。
4. **回边放大成本**：max 2 轮、deep 1 次/ finding、总预算；无新增证据立即停。
5. **LLM merge 掩盖失败**：只允许重叠/相邻合并；确定性冲突检查；失败可拆组。
6. **`.data` 体积**：保留容量策略；只在请求窗口深采，不整场深采。

## 8. 退出标准

- 任意 finding 的 window_id 从 detect 到 verdict 不丢、不被 action name 替换。
- 空 window 不再产生 `unmeasured` 静默降级。
- diagnose 至少有一条“请求→补证→再诊断”的真实回路，且有 trace。
- verify 的 before/after 同组配对 + control window + 绝对/相对分账。
- Pass2 不再是断头路：要么消费 `.data`，要么 fail-loud。
- 单测全绿；1–3 unit E2E 通过后，才允许 11case。

## 9. 不在本迭代

- 不做自动 pattern 升维 / 权限变更。
- 不改 11case 期望/评分口径。
- 不引入新的采样后端或新的性能指标。
- 不为通过 11case 调阈值。
