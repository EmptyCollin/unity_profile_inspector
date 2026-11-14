"""I23：配置候选（只提议，不自动应用）。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ConfigProposal:
    path: str
    section: str
    index: int
    field: str
    before: object
    after: object
    reason: str
    expected_effect: str = "gpu"


def _quality_items(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    items: list[dict] = []
    in_sec = False
    cur: dict | None = None
    for ln in lines:
        if re.match(r"^\s*m_QualitySettings:\s*$", ln):
            in_sec = True
            continue
        if not in_sec:
            continue
        m = re.match(r"^\s*-\s*(.*)$", ln)
        if m:
            if cur is not None:
                items.append(cur)
            cur = {}
            if m.group(1).strip():
                k, _, v = m.group(1).partition(":")
                if k:
                    cur[k.strip()] = v.strip()
            continue
        if cur is not None:
            m2 = re.match(r"^\s*([A-Za-z_]\w*):\s*(.+?)\s*$", ln)
            if m2:
                cur[m2.group(1)] = m2.group(2)
    if cur is not None:
        items.append(cur)
    return items


def propose(project: Path | str, *,
            max_shadow_distance: int = 40,
            max_anti_aliasing: int = 0) -> list[ConfigProposal]:
    root = Path(project).resolve()
    q = root / "ProjectSettings" / "QualitySettings.asset"
    if not q.is_file():
        return []
    out = []
    for i, item in enumerate(_quality_items(q)):
        shadow = item.get("shadowDistance")
        if shadow and shadow.isdigit() and int(shadow) > max_shadow_distance:
            out.append(ConfigProposal(
                "ProjectSettings/QualitySettings.asset",
                "m_QualitySettings", i, "shadowDistance",
                int(shadow), max_shadow_distance,
                f"shadowDistance {shadow} > {max_shadow_distance}",
                "gpu"))
        aa = item.get("antiAliasing")
        if aa and aa.isdigit() and int(aa) > max_anti_aliasing:
            out.append(ConfigProposal(
                "ProjectSettings/QualitySettings.asset",
                "m_QualitySettings", i, "antiAliasing",
                int(aa), max_anti_aliasing,
                f"antiAliasing {aa} > {max_anti_aliasing}", "gpu"))
    return out
