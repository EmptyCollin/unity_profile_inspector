"""G7 协议指纹：判定、记账、回归三处共用一份构造与摘要。

为什么单独一个模块：指纹字段散落在 detection/verdict/outcomes 三处，
各写一遍拼接迟早漂移——漂移的表现是"同一份数据被判成不可比"或反过来
"不同协议被当成同一把尺子"。这里只做规范化与摘要，不做判定。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

FIELDS = (
    "scene", "replay_script_hash", "quality_tier", "warmup_samples",
    "window_samples", "n_samples", "poll_rate", "data_tier", "editor_mode",
    "scale_factor", "graphics_api", "environment_fingerprint",
    "reset_mode", "warmup_s", "focus", "run_in_background",
)


def _norm(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return f"{float(v):.6g}"
    return str(v).strip()


def build(**kw) -> dict:
    """构造规范化指纹；缺省一律空串（不猜、不沿用当前运行态）。"""
    return {k: _norm(kw.get(k, "")) for k in FIELDS}


def digest(fp: dict) -> str:
    """指纹摘要：只对 FIELDS 里的键排序摘要，额外字段不参与。"""
    payload = {k: _norm(fp.get(k, "")) for k in FIELDS}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return "fp-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def file_hash(path: str | Path) -> str:
    """回放脚本内容哈希；文件不存在时如实返回 none-v0（不假装有回放）。"""
    p = Path(path)
    try:
        if not p.is_file():
            return "none-v0"
        return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return "none-v0"


def comparable(a: dict | str, b: dict | str) -> bool:
    """两个指纹是否可对比：摘要相同即可比；空指纹不可比（宁严勿松）。"""
    da = a if isinstance(a, str) and a else digest(a)
    db = b if isinstance(b, str) and b else digest(b)
    return bool(da) and bool(db) and da == db
