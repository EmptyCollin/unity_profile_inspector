"""按 project_id 隔离的学习资产路径（I10）。

知识库、指纹、成功率、影子决策都只能属于某个项目；没有全局库。
路径：runs/projects/<project_id>/knowledge/（E.RUNS 可被测试隔离）。
"""
from __future__ import annotations

from pathlib import Path

from upi.core import events as E


def active_project_id() -> str:
    try:
        from upi.config.project_profile import active_profile
        return active_profile().profile_id
    except Exception:  # noqa: BLE001
        return ""


def _safe(pid: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in pid) or "_unassigned"


def project_knowledge_dir(project_id: str | None = None) -> Path:
    pid = project_id or active_project_id() or "_unassigned"
    return Path(E.RUNS) / "projects" / _safe(pid) / "knowledge"


def knowledge_db(project_id: str | None = None) -> Path:
    return project_knowledge_dir(project_id) / "patterns.db"


def fingerprint_db(project_id: str | None = None) -> Path:
    return project_knowledge_dir(project_id) / "fingerprints.db"
