"""结构化契约（AGENTS §2 / I7：所有 Agent 输入输出必须是这些模型，pydantic v2 per G12）。

设计约束：
- 数值字段只接受工具产出（I1）；LLM 不得填充证据数值。
- Issue.signature 只允许机制级特征，禁止具体标识符（DESIGN §8.3）。
- 指纹不一致禁止对比（G7）：对比入口统一走 protocol_fingerprint 校验。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

DataTier = Literal["A", "A2", "A3", "A4", "B", "C"]   # A=内置轮询(秒级·水位唯一源) A2=注入式按帧(GC/内存) A3=离线.data(marker时间线,-1哨兵) A4=FrameTimingManager(逐帧CPU/GPU计时，固定4帧延迟，未回填帧按哨兵拒收) B=Player C=静态估算；跨档禁止对比，指标归属制见 DESIGN §3 第 6 条（G7/G16/G19）
Link = Literal["cpu", "gpu", "anim", "loading"]
FindingKind = Literal["issue", "potential"]                              # G18 归属判据
FindingType = Literal["waterline", "episode", "potential", "conflict", "perception"]  # DESIGN §3A 五类
AttributionSource = Literal["auto", "human"]                             # G19；无自动归因源时 human
IssueState = Literal["candidate", "confirmed", "fixing", "resolved", "failed",
                     "needs_human", "false_positive"]
PermissionTier = Literal["auto", "semi", "blocked"]
SideEffect = Literal["none", "may_affect_perception", "unknown"]
VerdictStatus = Literal["resolved", "not_significant", "regressed", "perception_failed"]
ReviewDecision = Literal["continue_with_direction", "rollback_and_replan", "escalate_human"]


class Evidence(BaseModel):
    """一条确定性证据。value 必须来自工具采样或解析（I1）。"""

    tool: str                       # unity command / probe / static_scan / git
    args_hash: str = ""             # 不落原始参数，尤其含凭据者（I12）
    raw_ref: str = ""               # 指向 runs/<id>/evidence/... 的原始文件
    value: float
    unit: str
    noise_band: float = 0.0
    data_tier: DataTier = "A"


class IssueSignature(BaseModel):
    """症状指纹：机制级特征，禁止文件名/类名/函数名（DESIGN §8.3）。"""

    dim: str                        # 度量维度，如 gc_alloc_per_frame
    metric: str
    mechanism: str                  # 机制级描述，如 per_frame_heap_alloc_in_hot_path
    threshold: float
    threshold_source: str = "noise_calibration"   # §8.1：只能来自噪声标定

    @model_validator(mode="after")
    def _no_identifiers(self):
        for field_name, v in (("dim", self.dim), ("metric", self.metric), ("mechanism", self.mechanism)):
            if any(t in v for t in (".cs", ".unity", ".prefab", "(", ")")):
                raise ValueError(f"signature.{field_name} 含具体标识符，违反 DESIGN §8.3")
        return self


class Issue(BaseModel):
    signature: IssueSignature
    link: Link
    evidence: list[Evidence] = Field(min_length=1)
    magnitude: float
    deviation_ratio: float
    explains_share_of_regression: float = 0.0
    state: IssueState = "candidate"
    low_confidence: bool = False     # n_samples < min_samples 时强制 True（§1.2）
    ptype: FindingType = "waterline"                   # §3A：waterline|episode|conflict|perception
    attribution_source: AttributionSource = "auto"     # G19：无自动归因源必须 human（finding 只给越线事实）

    @model_validator(mode="after")
    def _needs_evidence_for_confirmed(self):
        if self.state in ("confirmed", "fixing", "resolved") and self.low_confidence:
            raise ValueError("low_confidence 的 Issue 不得进入修复循环（AGENTS §1.2）")
        return self

    @model_validator(mode="after")
    def _episode_needs_frame_source(self):
        # G19：判"发作"必须建在逐帧源上；A 档秒级抽样判发作 = 漏检+把被跳过的异常当正常
        if self.ptype == "episode":
            tiers = {e.data_tier for e in self.evidence}
            if not (tiers & {"A2", "A4"}):
                raise ValueError(
                    f"ptype=episode 的 Issue 必须有逐帧源证据（A2/A4/探针），当前={tiers or '无'}（G19）")
            # 归因源在场性**按链路**判（与 detector._gpu_a4_episode_is_fact_only 同口径）：
            #   gpu   —— 逐笔归因程序侧无源（FD 实测不可取，DESIGN §12 ①-⑫），必须 human
            #   其余 —— 样本自带 gc_ring + A4 CPU/GPU 分解 = 程序化归因源在场，允许 auto。
            # 旧版此处无条件禁 auto，是把 GPU 特例泛化——会误杀 loading/anim 发作通路，
            # 与注册表口径互相矛盾（T2w 收口实测撞出）。
            if self.link == "gpu" and self.attribution_source != "human":
                raise ValueError("episode 归因源按链路判：GPU 发作的逐笔归因当前无源，"
                                 "必须 attribution_source=human（G19/DESIGN D13）")
        return self


class PotentialFinding(BaseModel):
    """P3 潜在风险（DESIGN §3A）。断言"规模到 S 才发作"在当前规模下不可证伪，
    故 G18：不得进入 Issue 通道 / 修复循环 / 成功率与 FP 分母。

    三字段缺一即 Pydantic 拒收（pattern + slope_source + scale_assumption）。
    """

    pattern: str                       # 机制级模式（如 per_frame_heap_alloc_in_hot_path）
    location: str                      # 代码/资产位置，供人查；不进签名
    slope_source: Evidence             # 必须指向实测斜率证据（A2/A4/BreachSample），禁止估算（I1）
    scale_assumption: str              # "规模到 S 时预测越线"——S 是假设不是事实
    link: Link
    status: Literal["shadow", "reported", "stress_pending", "promoted", "retracted"] = "shadow"

    @model_validator(mode="after")
    def _never_an_issue(self):
        # 构造期兜底：potential 永不能"看起来像" issue
        if self.status == "promoted":
            raise ValueError("potential 转 confirmed 必须经 T5 压测另立 Issue，本对象不承载升级态（G18）")
        return self


class Finding(BaseModel):
    """G18 归属容器：Finding = Issue ∪ Potential。potentials 只进报告不进循环。"""

    kind: FindingKind
    issue: Optional[Issue] = None
    potential: Optional[PotentialFinding] = None

    @model_validator(mode="after")
    def _kind_matches_payload(self):
        if self.kind == "issue" and (self.issue is None or self.potential is not None):
            raise ValueError("kind=issue 必须且只能带 issue 载荷")
        if self.kind == "potential" and (self.potential is None or self.issue is not None):
            raise ValueError("kind=potential 不得携带 Issue（G18：potential 不进修复循环/分母）")
        return self


class A4Frame(BaseModel):
    """A4 逐帧计时的一帧（DESIGN §2.1 A4 行）。"""

    frame_idx: int
    cpu_frame_time_ms: float
    gpu_frame_time_ms: Optional[float] = None    # 未回填(None)=非法值，不得当 0 统计
    complete_ts: int = 0

    @model_validator(mode="after")
    def _unfilled_is_not_zero(self):
        # 实测：gpu=0 且 completeTs=0 = 时间戳未回填，按 -1 哨兵同规则拒收（G19/I1）
        if self.gpu_frame_time_ms == 0.0 and self.complete_ts == 0:
            raise ValueError("未回填帧(gpu=0 且 completeTs=0)不得作为读数入统计（哨兵规则）")
        return self


class BreachSample(BaseModel):
    """P2 发作证据单元（DESIGN §2.4 T2w，M2a）。越界那一刻 + 前后原始序列 + A4 窗口。"""

    model_config = {"extra": "forbid"}   # 探针落盘字段与本契约必须逐一对齐：
    # 默认的"静默忽略多余字段"会让探针/契约漂移无声发生（T2w 收口实测撞出：
    # rel/gc_ring_* 在 jsonl 里存在、契约没有 → 被静默吃掉 → 下游 AttributeError）。

    frame_idx: int
    rel: int = -1                                        # 相对回放 origin 的帧号（T2 对齐口径）
    metric: str
    value: float
    threshold_abs: float
    deviation_ratio: float
    ring_before: list[float] = Field(default_factory=list)   # 前 N 帧（默认 10，D17）
    ring_after: list[float] = Field(default_factory=list)    # 后 M 帧（M≥14 覆盖 A4 固定 4 帧延迟）
    gc_ring_before: list[float] = Field(default_factory=list)   # 逐帧 GC 分配序列（loading/cpu 发作的归因源）
    gc_ring_after: list[float] = Field(default_factory=list)
    a4_window: list[A4Frame] = Field(default_factory=list)
    data_tier: Literal["A2", "A4"] = "A2"                    # 发作必须有逐帧源（G19）
    protocol_fingerprint: str = ""
    replay_seed: int = -1                                    # T2：可复现回放种子；-1=不可复现，禁进修复循环

    @model_validator(mode="after")
    def _after_depth_covers_a4(self):
        if len(self.ring_after) > 0 and len(self.ring_after) < 14:
            raise ValueError(f"ring_after 深度 {len(self.ring_after)} < 14，接不住 A4 的 4 帧固定延迟（D17）")
        return self

    def replayable(self) -> bool:
        """T2 前提：不可复现的样本允许记录，但禁止进入修复循环（消费方必须检查）。"""
        return self.replay_seed >= 0


class AttributionFactors(BaseModel):
    """六因子，LLM 只判定"是否成立"并给证据指针（§1.3），置信度由公式算。"""

    proximity: float = Field(0.0, ge=0, le=1)
    explains_share: float = Field(0.0, ge=0, le=1)
    competitiveness: float = Field(0.0, ge=0, le=1)
    prior_hit: float = Field(0.0, ge=0, le=1)
    counterfactual: float = Field(0.0, ge=0, le=1)
    mechanism: float = Field(0.0, ge=0, le=1)

    def vetoed(self) -> Optional[str]:
        """DESIGN §5 硬性否决项。"""
        if self.explains_share < 0.3:
            return "explains_share < 0.3"
        return None


class Hypothesis(BaseModel):
    root_cause: str
    code_anchors: list[str] = Field(default_factory=list)
    attribution_factors: AttributionFactors
    confidence: float = Field(ge=0, le=1)      # 计算得出，不由 LLM 打分
    weights_version: str = "hand-set-prior-v1"
    expected_effect: dict = Field(default_factory=dict)
    veto_reasons: list[str] = Field(default_factory=list)   # §5 否决项命中记录；非空则 conf≤0.5

    @model_validator(mode="after")
    def _veto_cap_consistency(self):
        # 自洽校验：有否决理由却声称 conf>0.5，属计算与声明不符（I1）
        if self.veto_reasons and self.confidence > 0.5:
            raise ValueError("命中否决项的 Hypothesis 置信度不得 >0.5（DESIGN §5）")
        return self


class EditPlan(BaseModel):
    playbook_id: str
    diffs: list[dict] = Field(default_factory=list)
    invariants_assert: list[str] = Field(default_factory=list)
    side_effect: SideEffect = "unknown"
    touched_assets: list[str] = Field(default_factory=list)
    permission_tier: PermissionTier = "blocked"

    @model_validator(mode="after")
    def _perception_forces_semi(self):
        # AGENTS §3 维度一：触碰感知层无论置信度多高，强制 semi
        if self.side_effect == "may_affect_perception" and self.permission_tier == "auto":
            raise ValueError("触碰感知层的 EditPlan 不得为 auto（AGENTS §3 维度一）")
        return self


class GuardMetric(BaseModel):
    metric: str
    before: float
    after: float
    delta: float
    regressed: bool = False


class Perception(BaseModel):
    render_diff_score: Optional[float] = None
    anim_event_diff: Optional[int] = None
    replay_divergence: Optional[bool] = None
    passed: bool = False


class Conflict(BaseModel):
    """P4 跨指标矛盾（DESIGN §3A / T7）。违反规则=视同守护劣化，记进 Review，不自行解释成因。"""

    rule_id: str
    metrics: dict = Field(default_factory=dict)     # {指标名: 实测值}
    expected: str = ""                              # 规则期望的关系
    observed: str = ""                              # 实测到的关系


class Verdict(BaseModel):
    target: dict                    # {before, after, delta, sigma, significant}
    guards: list[GuardMetric] = Field(default_factory=list)
    perception: Perception
    conflicts: list[Conflict] = Field(default_factory=list)   # P4；非空即进 Review（AGENTS §1.5）
    compile_ok: bool = False        # G8 前置于采样
    realized_over_expected: float = 0.0
    status: VerdictStatus

    @model_validator(mode="after")
    def _guards_required(self):
        # §1.5：必须对比全量守护指标，不能只报当初异常项
        if not self.guards:
            raise ValueError("Verdict 必须包含全量守护指标（AGENTS §1.5）")
        return self


class SamplingProtocol(BaseModel):
    min_samples: int = 5
    # 实测修正（DESIGN §2.1）：A 档为轮询内置命令的秒级抽样，单位是"次/秒"而非帧
    warmup_samples: int = 10
    window_samples: int = 30
    poll_rate: float = 1.0          # 实测 ~1.05 次/秒，入指纹；1Hz 与 10Hz 不可互比
    aggregation: Literal["median"] = "median"
    data_tier: DataTier = "A"
    degenerate_distribution: bool = False   # unique/n<0.1 或 MAD==0 ⇒ MAD 失效，改走 min_magnitude（G17）
    probe_overhead: float = 0.0
    instrument_distorted: bool = False   # 开销超预算则禁止用于异常判定（§1.2）
    scale_factor: float = 1.0            # 常态=1；≠1 为压测档（10×/100×，D15）。禁与常态混比（G7）
    ring_after_depth: int = 14           # D17：接住 A4 的 4 帧固定延迟的向后深度下限


class Fingerprint(BaseModel):
    """G7：指纹一致才可对比。A 档与 C 档永远不可互比。

    editor_mode 是实测补入的必要字段（AGENTS G16 / DESIGN §2.2.1 事实 1）：
    无头模式下渲染计数器恒为 0，GUI 下同场景实测 drawCalls=195。
    """

    scene_guid: str
    replay_script_hash: str
    quality_tier: str
    warmup_samples: int
    window_samples: int
    n_samples: int
    poll_rate: float
    data_tier: DataTier
    editor_mode: Literal["gui", "headless"] = "gui"
    environment_fingerprint: str
    scale_factor: float = 1.0
    graphics_api: str = "unknown"   # EX-1 预留：切 DX11 前后数值禁止对比靠它兜底（AGENTS G7）

    # 注意：这里**不**按 editor_mode 拒绝构造。实测 memory / Scripts / Loading 计数器在
    # 无头模式同样出真值（§2.2.1 事实 1 仅适用于渲染类目），拦掉会误伤 CPU/加载链路。
    # G16 的约束由 Detector 按 counter 归属类目校验；本模型只负责"跨 editor_mode 禁止对比"。

    def same_for_compare(self, other: "Fingerprint") -> tuple[bool, list[str]]:
        diffs = [f for f in self.model_fields_set | other.model_fields_set
                 if getattr(self, f, None) != getattr(other, f, None)]
        if self.data_tier != other.data_tier:
            diffs.append("data_tier（跨档禁止对比）")
        if self.editor_mode != other.editor_mode:
            diffs.append("editor_mode（gui/headless 数值不可比，G16）")
        return (not diffs), diffs


class TraceSpan(BaseModel):
    """I10/G13：无 trace 不入统计。成本与质量指标的唯一来源。"""

    run_id: str
    stage: str
    seq: int
    wallclock_ms: int
    exit_status: int = 0
    issue_id: Optional[str] = None
    attempt_id: Optional[str] = None
    tool_calls: list[dict] = Field(default_factory=list)
    llm: Optional[dict] = None            # {model, token_in, token_out, prompt_version}
    evidence_refs: list[str] = Field(default_factory=list)
    environment_fingerprint: str = ""
    channel: Literal["user", "dev"] = "user"


class PatternPromotionProposal(BaseModel):
    """AGENTS §1.7 Knowledge Agent 输出：只提案，不执行（权限变更须人工批准）。"""

    pattern_id: str
    n_effective: int                                   # §8.2 口径：独立场景×档位
    n_trials: int
    success_rate_wilson_lower_bound: float
    requested_tier: Literal["auto", "semi"]
    overfit_checklist: dict = Field(default_factory=dict)
    current_tier: str = "semi"
    status: Literal["proposal_ready", "blocked"] = "blocked"
    blocked_reasons: list[str] = Field(default_factory=list)
    checklist_ok: dict = Field(default_factory=dict)
    note: str = ""

    @model_validator(mode="after")
    def _no_proposal_without_checklist(self):
        # G14：缺体检表或不达标 → 提案本身 blocked，不得提交人工批准
        if self.status == "proposal_ready":
            if not self.overfit_checklist:
                raise ValueError("proposal_ready 但缺 §8.7 体检表（G14）")
            if self.blocked_reasons:
                raise ValueError(f"proposal_ready 却带拦截理由: {self.blocked_reasons}")
        return self
