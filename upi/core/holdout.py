"""I8e：H 集冻结清单 + 工程测试语义泄漏扫描。

评估者工具，不进入 Agent 输入。审计失败不是“代码错”，而是“不能拿它证明泛化”。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Iterable, Optional

from pydantic import BaseModel, Field

# 这些词在真实生产工程里可能出现，但作为“泛化证据”的工程不能靠它们。
LEAK_TERMS = ("legacy_", "case_id", "DevSimScenarios", "oracle", "expected_",
              "defect", "故意埋", "测试专用", "sim/tests")


class LeakHit(BaseModel):
    file: str
    line: int
    term: str
    text: str


class HoldoutManifest(BaseModel):
    holdout_id: str
    case_ids: list[str] = Field(default_factory=list)
    frozen_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    source_hashes: dict[str, str] = Field(default_factory=dict)
    notes: str = ""

    def assert_not_tuning(self, used_ids: Iterable[str]) -> None:
        used = set(used_ids)
        overlap = sorted(used & set(self.case_ids))
        if overlap:
            raise ValueError(f"H 集被用于调参：{overlap}")


def _text_files(project_root: Path):
    for f in sorted(project_root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(project_root)
        if any(p in {"Library", "Temp", "Logs", "obj", "Obj", "Build", "Builds"}
               for p in rel.parts):
            continue
        if f.suffix.lower() not in {".cs", ".json", ".unity", ".asset", ".prefab", ".txt", ".md"}:
            continue
        yield f, rel.as_posix()


def audit_leakage(project_root: Path | str,
                  terms: Optional[Iterable[str]] = None) -> list[LeakHit]:
    root = Path(project_root).resolve()
    needles = tuple(terms or LEAK_TERMS)
    hits: list[LeakHit] = []
    for f, rel in _text_files(root):
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, start=1):
            for term in needles:
                if term in line:
                    hits.append(LeakHit(file=rel, line=i, term=term,
                                        text=line.strip()[:240]))
    return hits


def source_hash(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def freeze_manifest(holdout_id: str, case_ids: list[str],
                    project_root: Path | str) -> HoldoutManifest:
    root = Path(project_root).resolve()
    files = [p for p in root.rglob("*") if p.is_file()
             and p.suffix.lower() in {".cs", ".unity", ".asset", ".prefab"}]
    return HoldoutManifest(
        holdout_id=holdout_id, case_ids=list(case_ids),
        source_hashes={str(p.relative_to(root)).replace("\\", "/"): source_hash(p)
                       for p in files},
    )


def write_manifest(manifest: HoldoutManifest, path: Path | str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out
