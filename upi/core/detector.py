"""检测器注册表（DESIGN §1.1 / REQ-5 可复用流程的落点）。

Detector = 声明式结构：链路 / 需要的指标 / 阈值 / 归因锚点模式 / 候选修法 / 权限档 / 版本。
误报率按 detector.id + version 分桶（§3）；阈值只允许来自 clean 变体噪声标定（§8.1）——
本模块把这条规矩做成构造期校验：threshold_source != "noise_calibration" 直接拒绝。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

DataTier = Literal["A", "A2", "A3", "B", "C"]


class Thresholds(BaseModel):
    k_sigma: float = 3.0
    min_magnitude: float                    # 业务显著下限（绝对量）
    deviation_ratio: float = 1.5            # 相对 clean 的偏离倍数
    explains_share: float = 0.2             # 双门限之业务显著（§3.4）
    threshold_source: str = "noise_calibration"

    @model_validator(mode="after")
    def _only_calibrated(self):
        if self.threshold_source != "noise_calibration":
            raise ValueError("阈值只能来自 clean 变体噪声标定（DESIGN §8.1），禁止手调")
        return self


class Playbook(BaseModel):
    id: str
    side_effect: Literal["none", "may_affect_perception", "unknown"]
    whitelisted: bool = False               # §3 维度二：语义保持白名单
    desc: str = ""


class Detector(BaseModel):
    id: str
    link: Literal["cpu", "gpu", "anim", "loading"]
    fixture: str                            # 绑定的夹具机制（机制级，非场景实例）
    metric: str                             # 判定指标名（get_performance / a2 的键）
    unit: str
    source_tier: DataTier                   # 指标归属制（G16/G17）：渲染=A，GC=A2
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


DEFAULT_REGISTRY: list[Detector] = [
    Detector(
        id="DET-CPU-GC-FRAMEALLOC", link="cpu", fixture="F-CPU-01",
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
        id="DET-GPU-BATCHBREAK", link="gpu", fixture="F-GPU-01",
        metric="drawCalls", unit="calls", source_tier="A",
        thresholds=Thresholds(min_magnitude=30, deviation_ratio=1.3,
                              threshold_source="noise_calibration"),
        anchor_patterns=["per_renderer_material_instantiation", "runtime_material_property_write"],
        playbooks=[Playbook(id="PB-SHARED-MAT-MPB", side_effect="may_affect_perception",
                            desc="共享材质 + MaterialPropertyBlock（触碰材质=感知层）")],
        default_tier="semi",
    ),
    Detector(
        id="DET-ANIM-PARAMRTT", link="anim", fixture="F-ANIM-01",
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
        id="DET-LOAD-SYNCPEAK", link="loading", fixture="F-LOAD-01",
        metric="totalAllocatedBytes", unit="B", source_tier="A",
        thresholds=Thresholds(min_magnitude=5_000_000, deviation_ratio=1.05,
                              threshold_source="noise_calibration"),
        anchor_patterns=["sync_resources_load_in_start", "resident_buffer_without_release"],
        playbooks=[Playbook(id="PB-ASYNC-STAGGER", side_effect="may_affect_perception",
                            desc="分帧异步加载（改时序=可能影响体验）")],
        default_tier="semi",
    ),
]
