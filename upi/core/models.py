"""结构化契约（AGENTS §2 / I7：所有 Agent 输入输出必须是这些模型，pydantic v2 per G12）。

设计约束：
- 数值字段只接受工具产出（I1）；LLM 不得填充证据数值。
- Issue.signature 只允许机制级特征，禁止具体标识符（DESIGN §8.3）。
- 指纹不一致禁止对比（G7）：对比入口统一走 protocol_fingerprint 校验。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

DataTier = Literal["A", "A2", "A3", "B", "C"]   # A=内置轮询(秒级·渲染唯一源) A2=注入式按帧(GC/内存) A3=离线.data(marker时间线,-1哨兵) B=Player C=静态估算；跨档禁止对比，指标归属制见 DESIGN §3 第 6 条（G7/G16）
Link = Literal["cpu", "gpu", "anim", "loading"]
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

    @model_validator(mode="after")
    def _needs_evidence_for_confirmed(self):
        if self.state in ("confirmed", "fixing", "resolved") and self.low_confidence:
            raise ValueError("low_confidence 的 Issue 不得进入修复循环（AGENTS §1.2）")
        return self


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


class Verdict(BaseModel):
    target: dict                    # {before, after, delta, sigma, significant}
    guards: list[GuardMetric] = Field(default_factory=list)
    perception: Perception
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
    degenerate_distribution: bool = False   # unique/n<0.1 ⇒ MAD 失效，改走 min_magnitude
    probe_overhead: float = 0.0
    instrument_distorted: bool = False   # 开销超预算则禁止用于异常判定（§1.2）


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
