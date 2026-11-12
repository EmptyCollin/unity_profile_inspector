"""I8e 验证门禁测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.verification import (MetricClaim, VerificationLevel,  # noqa: E402
                                   classify_claim)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


def claim(source, before=100, after=50, n=3):
    return MetricClaim(metric="gc_alloc_bytes", source=source,
                       before=before, after=after, n=n)


ok("可信来源可 verified",
   classify_claim(claim("profiler_data")).level == VerificationLevel.verified)
ok("Editor GC 计数器只能 observed",
   classify_claim(claim("editor_gc_counter")).level == VerificationLevel.observed)
ok("Editor TotalMemory 只能 observed",
   classify_claim(claim("editor_gc_total_memory")).level == VerificationLevel.observed)
ok("无下降判 failed",
   classify_claim(claim("memory_snapshot", before=50, after=80)).level
   == VerificationLevel.failed)
ok("n<2 只能 observed",
   classify_claim(claim("profiler_data", n=1)).level == VerificationLevel.observed)
ok("功能失败判 failed",
   classify_claim(claim("memory_snapshot"), case_functional_ok=False).level
   == VerificationLevel.failed)

print(f"\n==== 验证门禁测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
