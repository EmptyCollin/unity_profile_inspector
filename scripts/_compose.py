"""测试夹具：把病例组合成一个临时工程（干净 base + overlays/patches）。"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from upi.core.case_overlay import apply_cases  # noqa: E402


def compose(cases: list[str], prefix: str = "compose_") -> Path:
    """临时组合工程放在 runs/_compose（不能放系统 temp：路径含 Temp 会被扫描器跳过）。"""
    out = ROOT / "runs" / "_compose" / f"{prefix}{uuid.uuid4().hex[:8]}"
    out.mkdir(parents=True, exist_ok=False)
    apply_cases(ROOT / "sim" / "project",
                [ROOT / "sim" / "cases" / c for c in cases], out)
    return out


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
