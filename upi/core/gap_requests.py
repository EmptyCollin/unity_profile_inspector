"""覆盖缺口 → 证据请求（I13b）：缺口不能只记录，要转成可执行的补采请求。

静态类请求（资产/代码）直接可满足；运行时类请求交给 r′ 窗口采集。
"""
from __future__ import annotations

from upi.core.evidence_request import EvidenceRequest

# 缺口 → 请求模板
GAP_REQUESTS: dict[str, list[dict]] = {
    "allocation": [{"kind": "replay_window", "reason": "补分配调用栈",
                    "metric": "gc_alloc_per_frame", "window": 32,
                    "expected": "窗口内 allocation callstack"}],
    "render_material": [
        {"kind": "code_search", "reason": "找 material 访问点",
         "expected": "Material"},
        {"kind": "asset_inventory", "reason": "查材质资产",
         "expected": "material", "window": 0},
    ],
    "loading": [{"kind": "replay_window", "reason": "补加载阶段证据",
                 "metric": "frame_time_ms", "window": 32,
                 "expected": "加载阶段耗时/调用栈"}],
    "instantiate": [
        {"kind": "code_search", "reason": "找 Instantiate 调用点",
         "expected": "Instantiate"},
        {"kind": "replay_window", "reason": "补实例化窗口",
         "metric": "frame_time_ms", "window": 32,
         "expected": "Instantiate 次数/调用栈"},
    ],
    "texture_asset": [
        {"kind": "asset_inventory", "reason": "查纹理资产",
         "expected": "texture", "window": 128},
        {"kind": "reference_graph", "reason": "查纹理引用",
         "file": "", "expected": "texture reference"},
    ],
}


def requests_for_gaps(coverage: dict, *, file: str = "",
                      method: str = "") -> list[EvidenceRequest]:
    """按 coverage 的 blind_spots 生成请求；无缺口返回空。"""
    gaps = coverage.get("blind_spots") or []
    out: list[EvidenceRequest] = []
    seen: set[tuple] = set()
    for gap in gaps:
        for spec in GAP_REQUESTS.get(str(gap), []):
            key = (spec["kind"], spec.get("expected", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(EvidenceRequest(
                kind=spec["kind"], reason=spec["reason"],
                metric=spec.get("metric", ""),
                window=int(spec.get("window") or 0),
                file=file or spec.get("file", ""),
                method=method,
                expected=spec.get("expected", "")))
    return out
