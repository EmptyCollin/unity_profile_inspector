"""取证参数单一事实源。

两个入口共用：I8 Lab（开发验收台）与产品控制台（用户入口）。
- 文件：`config/i8_lab.json`（用户可手改）
- 探针读取：`runs/_probe/capture_cfg.json`（每次取证前由调用方写入）
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "i8_lab.json"
PROBE_CONFIG_PATH = ROOT / "runs" / "_probe" / "capture_cfg.json"

DEFAULTS: dict[str, int] = {
    "evidence_threads": 8,
    "evidence_frames": 8,
    "evidence_budget_ms": 6000,
    "board_threads": 8,
    "board_frames": 4,
    "board_budget_ms": 5000,
    "probe_sample_window": 300,
    "profile_store_gb": 20,
}

LIMITS: dict[str, tuple[int, int]] = {
    "evidence_threads": (1, 64),
    "evidence_frames": (1, 32),
    "evidence_budget_ms": (500, 20000),
    "board_threads": (1, 64),
    "board_frames": (1, 8),
    "board_budget_ms": (500, 20000),
    "probe_sample_window": (30, 3600),
    "profile_store_gb": (1, 500),
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        raw = {}
    for k, (lo, hi) in LIMITS.items():
        v = raw.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            cfg[k] = max(lo, min(hi, v))
    return cfg


def save(body: dict) -> dict:
    cfg = load()
    for k, (lo, hi) in LIMITS.items():
        v = body.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{k} 必须是整数")
        iv = int(v)
        if not (lo <= iv <= hi):
            raise ValueError(f"{k} 超出范围 [{lo},{hi}]：{iv}")
        cfg[k] = iv
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return cfg


def write_probe_config() -> dict:
    """把当前配置写到探针读取的位置；返回生效值。"""
    cfg = load()
    PROBE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROBE_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return cfg
