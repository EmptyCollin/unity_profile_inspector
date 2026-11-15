"""I26：运行时 ablation 开关（文件驱动，不重复编译）。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FLAGS = "runs/_probe/ablation_flags.txt"


@dataclass
class AblationSpec:
    site_id: str
    description: str = ""

    def guard(self) -> str:
        return f'if (UpiAblation.Disabled("{self.site_id}"))'


def flags_path(root: Path | str = ".") -> Path:
    return Path(root) / FLAGS


def read_flags(root: Path | str = ".") -> dict[str, bool]:
    p = flags_path(root)
    out: dict[str, bool] = {}
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip() == "1"
    return out


def write_flags(root: Path | str, flags: dict[str, bool]) -> Path:
    p = flags_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# site_id=1 means disabled"]
    for k in sorted(flags):
        lines.append(f"{k}={1 if flags[k] else 0}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def set_flag(root: Path | str, site_id: str, disabled: bool) -> Path:
    f = read_flags(root)
    f[site_id] = disabled
    return write_flags(root, f)
