"""I20：机制不变量检查（从代码模式推导，不依赖类名）。"""
from __future__ import annotations

from pathlib import Path

from upi.core.mechanism_infer import count_in_method, infer_from_text


def invariant_for_text(find: str) -> tuple[str, str] | None:
    return infer_from_text(find)


def invariant_ok(project: Path, file: str, method: str,
                 pattern: str) -> tuple[bool, str]:
    n = count_in_method(Path(project), file, method, pattern)
    return n == 0, f"sites={n}"
