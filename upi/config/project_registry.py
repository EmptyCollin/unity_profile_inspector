"""I7b：修复对象工程的注册表（唯一事实源）。

旧 `projects/*.json` 只保留给既有测试/夹具；UI 与运行时新增工程一律走
`projects/registry.json`。没有兼容迁移、没有双读 fallback。
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]
_PROJECT_ID_RE = re.compile(r"[^a-z0-9_-]+")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _slug(text: str) -> str:
    s = _PROJECT_ID_RE.sub("-", (text or "").strip().lower()).strip("-")
    return s or "project"


class ProjectValidation(BaseModel):
    ok: bool = False
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    checked_at: str = ""


class ProjectEntry(BaseModel):
    project_id: str
    display_name: str
    project_path: Path
    git_root: Path
    unity_version: str = ""
    default_scene: str = ""
    quality_tier: str = "urp-balanced"
    replay_mode: str = "none"
    replay_command: str = ""
    status: Literal["active", "archived", "invalid"] = "active"
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    created_at: str = Field(default_factory=_now)
    last_used_at: Optional[str] = None
    validation: ProjectValidation = Field(default_factory=ProjectValidation)
    # 仅给高级接入用；正常 UI 不暴露。不要放旧 profile 的兼容字段。
    profile_overrides: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _paths(self):
        self.project_path = Path(str(self.project_path)).resolve()
        self.git_root = Path(str(self.git_root)).resolve()
        if not self.project_path.is_absolute() or not self.git_root.is_absolute():
            raise ValueError("project_path/git_root 必须是绝对路径")
        try:
            self.project_path.relative_to(self.git_root)
        except ValueError:
            raise ValueError(f"project_path {self.project_path} 不在 git_root {self.git_root} 下") from None
        return self

    def to_profile(self):
        """把 registry 条目转成管线运行用的 ProjectProfile。"""
        from upi.config.project_profile import ProjectProfile

        data = {
            "profile_id": self.project_id,
            "display_name": self.display_name,
            "project_path": self.project_path,
            "git_root": self.git_root,
            "quality_tier": self.quality_tier,
            "scenes_dir": "Assets/Scenes",
            "scene_template": "{subject}.unity",
            "assets_root": "Assets",
            "subjects": [],
            "clean_subject": "",
            "floor_targets_rel": "",
            "fix_subject": "",
            "subject_file": "",
            "defect_signature": "",
            "conformance_tier": "none",
        }
        # 默认场景若符合 scenes_dir/scene_template，就作为可执行 scene 的 subject。
        scene = self.default_scene.replace("\\", "/")
        prefix = "Assets/Scenes/"
        if scene.startswith(prefix) and scene.endswith(".unity"):
            subject = scene[len(prefix):-len(".unity")]
            if subject and "/" not in subject:
                data["fix_subject"] = subject
        data.update(self.profile_overrides or {})
        data["profile_id"] = self.project_id
        return ProjectProfile(**data)


class ProjectRegistry:
    """`projects/registry.json` 的读写与 CRUD。"""

    VERSION = 1
    RECENT_MAX = 10

    def __init__(self, root: Optional[Path | str] = None):
        if root is None:
            from upi.config.project_profile import projects_dir
            root = projects_dir()
        self.root = Path(root)
        self.path = self.root / "registry.json"

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": self.VERSION, "active_project_id": None,
                    "entries": [], "recent": []}
        import json
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw.setdefault("version", self.VERSION)
        raw.setdefault("active_project_id", None)
        raw.setdefault("entries", [])
        raw.setdefault("recent", [])
        return raw

    def _save(self, raw: dict) -> None:
        import json
        from upi.core import events as E
        self.root.mkdir(parents=True, exist_ok=True)
        E.atomic_write_text(self.path, json.dumps(raw, ensure_ascii=False, indent=2,
                                                  default=str))

    def entries(self, *, include_archived: bool = False) -> list[ProjectEntry]:
        raw = self._read()
        out = []
        for item in raw["entries"]:
            try:
                e = ProjectEntry(**item)
            except Exception:  # noqa: BLE001 - 坏条目不让整页 500
                continue
            if include_archived or e.status != "archived":
                out.append(e)
        return out

    def get(self, project_id: str) -> Optional[ProjectEntry]:
        for e in self.entries(include_archived=True):
            if e.project_id == project_id:
                return e
        return None

    def active_id(self) -> Optional[str]:
        return self._read().get("active_project_id")

    def active(self) -> Optional[ProjectEntry]:
        aid = self.active_id()
        return self.get(aid) if aid else None

    def recent(self) -> list[ProjectEntry]:
        raw = self._read()
        by_id = {e.project_id: e for e in self.entries(include_archived=True)}
        out = []
        for pid in raw.get("recent", []):
            if pid in by_id and by_id[pid].status != "archived":
                out.append(by_id[pid])
        return out[:self.RECENT_MAX]

    def add(self, *, project_path: Path | str, display_name: str = "",
            default_scene: str = "", quality_tier: str = "urp-balanced",
            replay_mode: str = "none", replay_command: str = "",
            tags: Optional[list[str]] = None, notes: str = "") -> ProjectEntry:
        p = Path(str(project_path)).resolve()
        if not str(project_path).strip():
            raise ValueError("工程目录不能为空")
        if not p.exists() or not p.is_dir():
            raise ValueError(f"工程目录不存在：{p}")
        name = (display_name or p.name).strip()
        pid = _slug(name)
        existing = {e.project_id for e in self.entries(include_archived=True)}
        base, i = pid, 2
        while pid in existing:
            pid = f"{base}-{i}"
            i += 1
        entry = ProjectEntry(
            project_id=pid, display_name=name, project_path=p,
            git_root=self._detect_git_root(p), unity_version=self._read_unity_version(p),
            default_scene=default_scene.strip(), quality_tier=quality_tier,
            replay_mode=replay_mode, replay_command=replay_command.strip(),
            tags=list(tags or []), notes=notes,
        )
        entry = self.validate(entry)
        raw = self._read()
        raw["entries"].append(entry.model_dump(mode="json"))
        self._save(raw)
        return entry

    def update(self, project_id: str, **fields) -> ProjectEntry:
        e = self.get(project_id)
        if e is None:
            raise ValueError(f"工程不存在：{project_id}")
        allowed = {"display_name", "default_scene", "quality_tier", "replay_mode",
                   "replay_command", "tags", "notes", "status"}
        changed = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if "display_name" in changed:
            e.display_name = str(changed["display_name"]).strip() or e.display_name
        for k in ("default_scene", "quality_tier", "replay_mode", "replay_command",
                  "notes", "status"):
            if k in changed:
                setattr(e, k, changed[k])
        if "tags" in changed:
            e.tags = list(changed["tags"] or [])
        e = self.validate(e)
        self._replace(e)
        return e

    def activate(self, project_id: str) -> ProjectEntry:
        e = self.get(project_id)
        if e is None:
            raise ValueError(f"工程不存在：{project_id}")
        if e.status != "active":
            raise ValueError(f"工程 {project_id} 状态为 {e.status}，不能激活")
        e.last_used_at = _now()
        e = self.validate(e)
        if not e.validation.ok:
            raise ValueError("工程校验失败：" + "; ".join(e.validation.errors))
        raw = self._read()
        raw["active_project_id"] = e.project_id
        recent = [x for x in raw.get("recent", []) if x != e.project_id]
        raw["recent"] = [e.project_id, *recent][:self.RECENT_MAX]
        for i, item in enumerate(raw["entries"]):
            if item.get("project_id") == e.project_id:
                raw["entries"][i] = e.model_dump(mode="json")
        self._save(raw)
        # 进程内切换；失败时不改指针由调用方处理。
        from upi.config.project_profile import register_profile, set_active_profile_id
        register_profile(e.to_profile(), activate=True)
        set_active_profile_id(e.project_id, root=self.root)
        return e

    def archive(self, project_id: str) -> ProjectEntry:
        e = self.get(project_id)
        if e is None:
            raise ValueError(f"工程不存在：{project_id}")
        e.status = "archived"
        self._replace(e)
        raw = self._read()
        if raw.get("active_project_id") == project_id:
            raw["active_project_id"] = None
            raw["recent"] = [x for x in raw.get("recent", []) if x != project_id]
            self._save(raw)
            from upi.config.project_profile import reset_profile
            reset_profile()
        return e

    def delete(self, project_id: str, *, force: bool = False) -> None:
        e = self.get(project_id)
        if e is None:
            raise ValueError(f"工程不存在：{project_id}")
        if not force and self.has_history(e):
            raise ValueError("该工程有历史 run，只允许归档；如确需删除请二次确认")
        raw = self._read()
        raw["entries"] = [x for x in raw["entries"]
                          if x.get("project_id") != project_id]
        raw["recent"] = [x for x in raw.get("recent", []) if x != project_id]
        if raw.get("active_project_id") == project_id:
            raw["active_project_id"] = None
        self._save(raw)
        if raw.get("active_project_id") is None:
            from upi.config.project_profile import reset_profile
            reset_profile()

    def validate(self, e: ProjectEntry) -> ProjectEntry:
        from upi.core import events as E
        errors: list[str] = []
        warnings: list[str] = []
        p = Path(e.project_path)
        if not p.exists() or not p.is_dir():
            errors.append("工程目录不存在")
        if not (p / "Assets").is_dir():
            errors.append("缺少 Assets/")
        if not (p / "ProjectSettings").is_dir():
            errors.append("缺少 ProjectSettings/")
        if e.default_scene:
            scene = Path(e.default_scene)
            if scene.is_absolute() or ".." in scene.parts:
                errors.append("默认场景必须是工程内相对路径")
            else:
                candidates = [p / scene, p / "Assets" / scene]
                if not any(c.is_file() for c in candidates):
                    errors.append(f"默认场景不存在：{e.default_scene}")
        if not (p / ".git").exists() and Path(e.git_root) == p:
            warnings.append("工程目录不是 git 仓库；快照/回滚需要 git_root")
        e.validation = ProjectValidation(ok=not errors, warnings=warnings,
                                         errors=errors, checked_at=_now())
        return e

    def has_history(self, e: ProjectEntry) -> bool:
        from upi.core import events as E
        runs = Path(E.RUNS)
        try:
            for st in runs.glob("*/state.json"):
                try:
                    text = st.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if str(e.project_path) in text or e.project_id in text:
                    return True
        except OSError:
            return False
        return False

    def _replace(self, e: ProjectEntry) -> None:
        raw = self._read()
        for i, item in enumerate(raw["entries"]):
            if item.get("project_id") == e.project_id:
                raw["entries"][i] = e.model_dump(mode="json")
                self._save(raw)
                return
        raise ValueError(f"工程不存在：{e.project_id}")

    @staticmethod
    def _detect_git_root(p: Path) -> Path:
        if (p / ".git").exists():
            return p
        try:
            p.relative_to(ROOT)
            return ROOT
        except ValueError:
            return p

    @staticmethod
    def _read_unity_version(p: Path) -> str:
        f = p / "ProjectSettings" / "ProjectVersion.txt"
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("m_EditorVersion:"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
        return ""
