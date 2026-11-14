"""I20：执行 planner 声明的静态不变量，不再推导模式。"""
from __future__ import annotations

from pathlib import Path

from upi.core.mechanism_infer import count_in_method


def invariant_ok(project: Path, file: str, method: str,
                 pattern: str) -> tuple[bool, str]:
    n = count_in_method(Path(project), file, method, pattern)
    return n == 0, f"sites={n}"
