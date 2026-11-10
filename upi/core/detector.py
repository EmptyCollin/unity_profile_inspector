"""检测器注册表（DESIGN §1.1 / REQ-5 可复用流程的落点）。

Detector = 声明式结构：链路 / 需要的指标 / 阈值 / 归因锚点模式 / 候选修法 / 权限档 / 版本。
误报率按 detector.id + version 分桶（§3）；阈值只允许来自 clean 变体噪声标定（§8.1）——
本模块把这条规矩做成构造期校验：threshold_source != "noise_calibration" 直接拒绝。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

DataTier = Literal["A", "A2", "A3", "A4", "B", "C"]
FindingType = Literal["waterline", "episode", "potential", "conflict", "perception"]


class Thresholds(BaseModel):
    k_sigma: float = 3.0
    min_magnitude: float                    # 业务显著下限（绝对量）
    deviation_ratio: float = 1.5            # 相对 clean 的偏离倍数
    explains_share: float = 0.2             # 双门限之业务显著（§3.4）
    threshold_source: str = "noise_calibration"
    budget_ref: Optional[str] = None        # frame_budget 时必填：外部锚点出处（文件路径）

    @model_validator(mode="after")
    def _only_calibrated(self):
        # §3A 两条合法阈值来源，没有第三种：
        #   noise_calibration — 水位判据，来自 clean 变体噪声标定（§8.1）
        #   frame_budget      — 发作判据，来自外部帧预算锚点（§9 帧预算模型）。
        #     预算线是目标帧率的算术导出（60fps⇒16.7ms），工程外生约束而非调参
        #     结果，因此不违反"禁止手调"——但它必须带出处（budget_ref），
        #     否则与手调无法区分。无出处的绝对线 = 手调，构造期拒收。
        if self.threshold_source == "noise_calibration":
            return self
        if self.threshold_source == "frame_budget":
            if not self.budget_ref:
                raise ValueError("frame_budget 阈值必须附 budget_ref 出处（DESIGN §9），"
                                 "无出处的绝对线视同手调，禁止")
            return self
        raise ValueError("阈值只能来自噪声标定或带出处的帧预算锚点"
                         "（DESIGN §3A/§8.1），禁止手调")


class Playbook(BaseModel):
    id: str
    side_effect: Literal["none", "may_affect_perception", "unknown"]
    whitelisted: bool = False               # §3 维度二：语义保持白名单
    desc: str = ""


class Detector(BaseModel):
    id: str
    link: Literal["cpu", "gpu", "anim", "loading"]
    # 被测机制（机制级，非场景实例）。原名 fixture，阶段 7 改名 subject：
    # §8.3 禁的是把具体标识符写进签名，而 "fixture" 读起来像"某个夹具目录"，
    # 接新工程时会被误读成"这条检测器不可迁移"。现在它与 profile.subjects
    # 同口径，值仍形如 F-CPU-01（那是机制编号，不是路径）。
    # ⚠️ 只改内存字段：detection.json 的 results 键、runs.fixture 列、
    #    Issue dict 的 "fixture" 键都是**已落盘契约**，保持原名（改了历史
    #    数据读不到，且不会有任何报错——只会静默变成 None/缺项）。
    subject: str
    metric: str                             # 判定指标名（get_performance / a2 的键）
    unit: str
    source_tier: DataTier                   # 指标归属制（G16/G17）：水位=A，GC=A2，逐帧计时=A4
    ptype: FindingType = "waterline"        # §3A/G19：episode 必须逐帧源（A2/A4/probe）
    attribution_source: Literal["auto", "human"] = "auto"   # G19：无自动归因源必须 human
    direction: Literal["higher_worse", "lower_worse"] = "higher_worse"
    thresholds: Thresholds
    anchor_patterns: list[str] = Field(default_factory=list)   # 机制级归因锚点，禁标识符
    playbooks: list[Playbook] = Field(default_factory=list)
    default_tier: Literal["auto", "semi", "blocked"] = "semi"
    version: str = "v1"
    # 标定注入痕迹（§8.1）：检测前由 apply_calibration 填充，报告需可回溯
    calibration_file: Optional[str] = None
    calibration_degenerate: Optional[bool] = None

    @model_validator(mode="after")
    def _tier_consistency(self):
        # 渲染计数只允许 A 档来源；GC/分配只允许 A2/A3（§3.6 指标归属制）
        render_metrics = {"drawCalls", "setPassCalls", "batches", "triangles", "vertices"}
        if self.metric in render_metrics and self.source_tier != "A":
            raise ValueError(f"渲染指标 {self.metric} 的数据源只能是 A（G16：A2 静默0 / A3 -1 哨兵均非法）")
        if self.metric in {"gc_alloc_per_frame"} and self.source_tier not in {"A2", "A3"}:
            raise ValueError("逐帧 GC 分配必须来自 A2/A3，A 档（轮询）无此量")
        for p in self.playbooks:
            if p.side_effect == "may_affect_perception" and self.default_tier == "auto":
                raise ValueError(f"{self.id}: 感知层 playbook 与 auto 档冲突（AGENTS §3 维度一）")
        return self

    @model_validator(mode="after")
    def _episode_needs_frame_source(self):
        # G19：判"发作"必须建在逐帧源上。A 档秒级抽样判发作 = 漏检 + 把被跳过的
        # 异常值当正常（最危险失效）；C 档静态估算更不允许。
        # （探针自计时 frameTime 属于 A4/probe 通路的实现细节，注册表以 A2/A4 声明源）
        if self.ptype == "episode" and self.source_tier not in {"A2", "A4"}:
            raise ValueError(
                f"{self.id}: ptype=episode 但 source_tier={self.source_tier} 不是逐帧源"
                "（G19：A 秒级判发作=漏检+把异常当正常；禁止注册）")
        # G19：渲染计数无任何逐帧源（A2 静默0 / A3 -1 / FD 程序侧不可解读）
        # ⇒ GPU 逐笔 spike detector 物理上不存在，禁止注册（虚报检出能力按 I1 处理）
        render_metrics = {"drawCalls", "setPassCalls", "batches", "triangles", "vertices"}
        if self.metric in render_metrics and self.ptype == "episode":
            raise ValueError(
                f"{self.id}: 渲染计数 {self.metric} 无逐帧源，禁止注册 GPU 逐笔 spike detector"
                "（G19/DESIGN §12 ①-⑫；GPU 发作 detector 只能建在 A4 gpuFrameTime 上）")
        return self

    @model_validator(mode="after")
    def _gpu_a4_episode_is_fact_only(self):
        # A4 上注册的 GPU 发作 detector：逐笔归因无源，attribution_source 必须 human
        if self.ptype == "episode" and self.link == "gpu" and self.attribution_source != "human":
            raise ValueError(
                f"{self.id}: GPU 发作的逐笔归因当前无自动源（D13），必须 attribution_source=human"
                "——finding 只给越线事实，根因转人工（G19）")
        return self


DEFAULT_REGISTRY: list[Detector] = [
    Detector(
        id="DET-CPU-GC-FRAMEALLOC", link="cpu", subject="F-CPU-01",
        metric="gc_alloc_per_frame", unit="B/frame", source_tier="A2",
        thresholds=Thresholds(min_magnitude=100_000, deviation_ratio=1.5,
                              threshold_source="noise_calibration"),
        anchor_patterns=[
            "per_frame_heap_alloc_in_hot_path",
            "linq_enumerator_closure_per_frame",
            "repeated_getcomponent_in_update",
            "enum_tostring_concat_per_frame",
        ],
        playbooks=[
            Playbook(id="PB-REUSE-BUFFER", side_effect="none", whitelisted=True,
                     desc="热路径分配改复用缓冲（容量不变断言）"),
            Playbook(id="PB-CACHE-COMPONENT", side_effect="none", whitelisted=True,
                     desc="GetComponent 结果缓存化"),
            Playbook(id="PB-PREGEN-LOOKUP", side_effect="none", whitelisted=True,
                     desc="枚举 ToString 改预生成查表"),
        ],
        default_tier="auto",
    ),
    Detector(
        id="DET-GPU-BATCHBREAK", link="gpu", subject="F-GPU-01",
        metric="drawCalls", unit="calls", source_tier="A",
        thresholds=Thresholds(min_magnitude=30, deviation_ratio=1.3,
                              threshold_source="noise_calibration"),
        anchor_patterns=["per_renderer_material_instantiation", "runtime_material_property_write"],
        playbooks=[Playbook(id="PB-SHARED-MAT-MPB", side_effect="may_affect_perception",
                            desc="共享材质 + MaterialPropertyBlock（触碰材质=感知层）")],
        default_tier="semi",
    ),
    Detector(
        id="DET-ANIM-PARAMRTT", link="anim", subject="F-ANIM-01",
        # 指标贴机制（M1 标定实测）：主线程帧时被整帧 4ms 摊薄 ⇒ 0.27ms delta 过不了地板；
        # Animator.ProcessAnimations 直接量动画管线，缺陷场景 med≈0.36ms vs clean≈0.0002ms
        metric="animator_process_animations_ns", unit="ns", source_tier="A2",
        thresholds=Thresholds(k_sigma=3.0, min_magnitude=0.15, deviation_ratio=1.25,
                              threshold_source="noise_calibration"),
        anchor_patterns=["cross_layer_state_query_per_frame", "param_write_without_equal_check",
                        "repeated_getcomponent_in_update"],
        playbooks=[Playbook(id="PB-PARAM-EQUAL-GUARD", side_effect="may_affect_perception",
                            desc="参数写入前判等/状态缓存（可能改动画时序）")],
        default_tier="semi",
    ),
    Detector(
        id="DET-LOAD-SYNCPEAK", link="loading", subject="F-LOAD-01",
        metric="totalAllocatedBytes", unit="B", source_tier="A",
        thresholds=Thresholds(min_magnitude=5_000_000, deviation_ratio=1.05,
                              threshold_source="noise_calibration"),
        anchor_patterns=["sync_resources_load_in_start", "resident_buffer_without_release"],
        playbooks=[Playbook(id="PB-ASYNC-STAGGER", side_effect="may_affect_perception",
                            desc="分帧异步加载（改时序=可能影响体验）")],
        default_tier="semi",
    ),
    # ---- 发作类（episode）：证据形态 = BreachSample，输入走 T2w 通路 ----
    # 判定口径与水位不同：单帧值 × 外部绝对线（帧预算锚点），不用 k·σ——
    # 逐帧越线的统计意义在"越过工程外生线"，不在"比自己的均值高"（范式讨论）。
    # 防误报三件套：线位离常态足够远 + warmup 豁免 + replayable() 复现判据。
    # GPU 发作 detector 暂缺（探针判定路径未接 gpuFrameTime 越线，G19 宁缺毋假）。
    Detector(
        id="DET-LOAD-FRAMESPIKE", link="loading", subject="F-LOAD-01",
        metric="frame_time_ms", unit="ms", source_tier="A4",
        ptype="episode", attribution_source="auto",   # CPU 侧越线有归因源（gc_ring/a4 cpu 分解）
        thresholds=Thresholds(min_magnitude=33.333, deviation_ratio=1.2,
                              threshold_source="frame_budget",
                              budget_ref="knowledge/frame_budget.json"),
        anchor_patterns=["sync_resources_load_in_start", "resident_buffer_without_release",
                         "bulk_alloc_burst_single_frame"],
        playbooks=[Playbook(id="PB-ASYNC-STAGGER", side_effect="may_affect_perception",
                            desc="分帧异步加载（改时序=可能影响体验）")],
        default_tier="semi",
        version="v1",
    ),
]
