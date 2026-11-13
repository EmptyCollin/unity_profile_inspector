"""缺失类证据（I13b）：按窗口汇总运行时证据 + 静态结构，并报告覆盖缺口。

静态线索只做解释/提示，不单独下结论；覆盖缺口进入 blind_spots。
"""
from __future__ import annotations

from pathlib import Path

CLASSES = ("allocation", "material", "loading", "instantiate", "texture")


def _in_range(frame: int, start: int, end: int) -> bool:
    return start <= frame <= end


def window_runtime(bundle, start: int, end: int) -> dict:
    funcs = [f for f in bundle.functions if _in_range(f.frame_idx, start, end)]
    allocs = [a for a in bundle.allocations
              if _in_range(getattr(a, "frame_idx", -1), start, end)]
    render = [r for r in bundle.render_objects
              if _in_range(getattr(r, "frame_idx", -1), start, end)]
    stacks = [c for c in bundle.callstacks
              if _in_range(getattr(c, "frame_idx", -1), start, end)]
    return {"functions": funcs, "allocations": allocs,
            "render_objects": render, "callstacks": stacks}


def static_hints(index, functions) -> dict:
    """窗口内函数对应的静态结构：分配点/加载/实例化/材质访问。"""
    out = {"allocations": [], "loads": [], "instantiates": [],
           "materials": []}
    for f in functions:
        key = f"{getattr(f.source, 'file', '')}::{f.symbol.split('.')[-1]}" \
            if getattr(f, "source", None) else ""
        info = index.methods.get(key)
        if info is None:
            continue
        if info.allocations:
            out["allocations"].append({"method": info.name,
                                       "file": info.file,
                                       "sites": info.allocations[:10]})
        if info.loads:
            out["loads"].append({"method": info.name, "file": info.file,
                                 "hooks": info.loads[:10]})
        if info.instantiates:
            out["instantiates"].append({"method": info.name,
                                        "file": info.file,
                                        "count": info.instantiates})
        if info.material_access:
            out["materials"].append({"method": info.name,
                                     "file": info.file,
                                     "count": info.material_access})
    return out


def coverage(bundle) -> dict:
    """按证据源报告覆盖；没有就是缺口，不静默。"""
    counters = getattr(bundle, "counters", {}) or {}
    has_alloc = (bool(bundle.allocations) or bool(bundle.callstacks)
                 or "GC Allocated In Frame" in counters)
    has_render = (bool(bundle.render_objects)
                  or "Draw Calls Count" in counters)
    has_func = bool(bundle.functions)
    cov = {
        "function": has_func,
        "allocation": has_alloc,
        "render_material": has_render,
        "loading": False,          # 运行时加载 trace 尚未接入
        "texture_asset": False,    # 资产/纹理需要静态资产清单补充
    }
    cov["blind_spots"] = [k for k, v in cov.items() if v is False]
    return cov


def explain_window(bundle, index, start: int, end: int) -> dict:
    rt = window_runtime(bundle, start, end)
    st = static_hints(index, rt["functions"])
    cov = coverage(bundle)
    return {"window": [start, end],
            "runtime": {
                "functions": [f.symbol for f in rt["functions"]],
                "allocations": len(rt["allocations"]),
                "render_objects": len(rt["render_objects"]),
                "callstacks": len(rt["callstacks"]),
            },
            "static": st,
            "coverage": cov,
            "evidence_class": "runtime+static",
            "limits": "静态只解释结构；加载/纹理类运行时证据不足时标缺口"}
