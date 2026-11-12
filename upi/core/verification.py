"""I8e 验证门禁：没有可靠性能证据时，禁止把 observed 写成 verified。"""
from __future__ import annotations

from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field

# 只有这些来源可以支撑 performance verified。
TRUSTED_SOURCES = {
    "independent_player_profiler",
    "memory_snapshot",
    "profiler_data",
    "external_replay_metric",
}
# Editor 内联 GC 计数器已实测不可区分 base/c01，禁止作为 verified 依据。
UNTRUSTED_SOURCES = {
    "editor_gc_counter",
    "editor_gc_total_memory",
    "editor_frame_delta",
}


class VerificationLevel(StrEnum):
    unverified = "unverified"
    observed = "observed"
    verified = "verified"
    failed = "failed"


class MetricClaim(BaseModel):
    metric: str
    source: str
    before: float
    after: float
    n: int = Field(ge=1)
    unit: str = ""


class VerificationResult(BaseModel):
    level: VerificationLevel
    reason: str
    claim: Optional[MetricClaim] = None


def classify_claim(claim: MetricClaim,
                   *, case_functional_ok: bool = True) -> VerificationResult:
    if claim.source in UNTRUSTED_SOURCES:
        return VerificationResult(
            level=VerificationLevel.observed,
            reason=f"{claim.source} 在当前 Editor 实测无法区分修前/修后，不能 verified",
            claim=claim)
    if claim.source not in TRUSTED_SOURCES:
        return VerificationResult(
            level=VerificationLevel.observed,
            reason=f"未知证据源 {claim.source!r}，不能 verified", claim=claim)
    if claim.n < 2:
        return VerificationResult(
            level=VerificationLevel.observed,
            reason="样本不足 n<2，不能 verified", claim=claim)
    if claim.after >= claim.before:
        return VerificationResult(
            level=VerificationLevel.failed,
            reason="目标指标未下降，不能 resolved", claim=claim)
    if not case_functional_ok:
        return VerificationResult(
            level=VerificationLevel.failed,
            reason="功能/编译未通过，性能对比无效", claim=claim)
    return VerificationResult(
        level=VerificationLevel.verified,
        reason="有可信来源、n>=2、目标指标下降且功能通过",
        claim=claim)
