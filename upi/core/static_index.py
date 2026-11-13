"""静态采集 S 层（I12b）：项目快照 / 代码索引 / 资产索引 / 配置索引。

定位：补充“结构与归属”，不证明运行时事实。
所有查询返回 evidence_class=static，并明确 can_support / cannot_support。
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

SKIP_DIRS = {".git", "Library", "Temp", "Logs", "obj", "UserSettings",
             "node_modules", ".vs", "Build", "Builds"}
TEXT_EXT = {".cs", ".txt", ".json", ".asset", ".prefab", ".unity",
            ".mat", ".shader", ".hlsl", ".cginc"}
ASSET_KIND = {".png": "texture", ".jpg": "texture", ".jpeg": "texture",
              ".tga": "texture", ".psd": "texture", ".exr": "texture",
              ".mat": "material", ".prefab": "prefab",
              ".unity": "scene", ".fbx": "mesh", ".wav": "audio",
              ".mp3": "audio", ".ogg": "audio", ".anim": "animation",
              ".controller": "animator"}

_METHOD_RE = re.compile(
    r"(?:public|private|protected|internal|static|async|override|virtual|"
    r"sealed|partial|extern|unsafe|new|void|IEnumerator|Task|"
    r"int|float|double|bool|string|long|uint|byte|object)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{")
_ALLOC_RE = re.compile(r"\bnew\s+([A-Za-z_][\w<>\[\],\s\.]*)")
_CALL_RE = re.compile(r"(?<![\w.])(?P<name>[A-Za-z_]\w*)\s*\(")
_LOAD_RE = re.compile(r"\bResources\.(?:Load|LoadAsync)\s*<([^>]+)>|\bResources\.(?:Load|LoadAsync)\s*\(")
_INST_RE = re.compile(r"\bObject\.Instantiate\s*<([^>]+)>|\bInstantiate\s*\(")
_MAT_RE = re.compile(r"\.material\b|\bnew\s+Material\s*\(")


@dataclass
class MethodInfo:
    file: str
    name: str
    start_line: int
    end_line: int
    body: str
    allocations: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    loads: list[str] = field(default_factory=list)
    instantiates: int = 0
    material_access: int = 0


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _strip(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', text)


def _match_brace(text: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(text) - 1


class StaticIndex:
    """一次构建，按 snapshot_hash 复用；工程变即失效。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.snapshot_hash = self._snapshot_hash()
        self.files: list[dict] = []
        self.methods: dict[str, MethodInfo] = {}
        self.assets: list[dict] = []
        self.settings: dict[str, str] = {}
        self._build()

    # ── snapshot ─────────────────────────────────────────────
    def _snapshot_hash(self) -> str:
        git = self.root / ".git"
        if git.exists():
            try:
                head = (git / "HEAD").read_text(encoding="utf-8").strip()
                if head.startswith("ref:"):
                    ref = self.root / ".git" / head.split(" ", 1)[1]
                    if ref.exists():
                        return ref.read_text(encoding="utf-8").strip()[:16]
                return head[:16]
            except OSError:
                pass
        h = hashlib.sha256()
        for p in self._walk():
            try:
                st = p.stat()
                h.update(f"{p.relative_to(self.root).as_posix()}:{st.st_size}".encode())
            except OSError:
                continue
        return h.hexdigest()[:16]

    def _walk(self) -> Iterable[Path]:
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                yield Path(dirpath) / fn

    def _build(self) -> None:
        for p in self._walk():
            try:
                rel = p.relative_to(self.root).as_posix()
                size = p.stat().st_size
            except OSError:
                continue
            self.files.append({"file": rel, "size": size})
            ext = p.suffix.lower()
            if ext in ASSET_KIND:
                self.assets.append({"file": rel, "size": size,
                                    "kind": ASSET_KIND[ext]})
            if ext == ".cs" and "/Editor/" not in rel:
                self._index_code(p, rel)
            if rel.startswith("ProjectSettings/") and ext == ".asset":
                self.settings[rel] = self._settings_keys(_read(p))
        self.files.sort(key=lambda x: x["file"])
        self.assets.sort(key=lambda x: (-x["size"], x["file"]))

    @staticmethod
    def _settings_keys(text: str) -> str:
        keys = re.findall(r"^\s*([A-Za-z_]\w*):", text, flags=re.M)
        return ", ".join(dict.fromkeys(keys))[:400]

    def _index_code(self, p: Path, rel: str) -> None:
        raw = _read(p)
        text = _strip(raw)
        for m in _METHOD_RE.finditer(text):
            name = m.group("name")
            end = _match_brace(text, text.find("{", m.end() - 1))
            body = text[m.start():end + 1]
            start_line = text.count("\n", 0, m.start()) + 1
            end_line = text.count("\n", 0, end) + 1
            info = MethodInfo(
                file=rel, name=name, start_line=start_line, end_line=end_line,
                body=body,
                allocations=[x.strip() for x in _ALLOC_RE.findall(body)][:20],
                calls=[x for x in _CALL_RE.findall(body) if x != name][:80],
                loads=[(a or b or "Resources")
                       for a, b in _LOAD_RE.findall(body)],
                instantiates=len(_INST_RE.findall(body)),
                material_access=len(_MAT_RE.findall(body)))
            self.methods[f"{rel}::{name}"] = info

    # ── capability boundary ─────────────────────────────────
    @staticmethod
    def can_support() -> list[str]:
        return ["source_location", "call_sites", "allocation_sites",
                "asset_size", "asset_references", "settings", "static_callgraph"]

    @staticmethod
    def cannot_support() -> list[str]:
        return ["runtime_frequency", "actual_cost", "frame_timing",
                "allocation_volume", "path_executed", "hotness"]

    # ── queries ──────────────────────────────────────────────
    def source_slice(self, file: str, method: str = "",
                     before: int = 15, after: int = 55) -> str:
        rel = file.replace("\\", "/").lstrip("/")
        fp = (self.root / rel).resolve()
        try:
            fp.relative_to(self.root)
        except ValueError:
            return ""
        if not fp.is_file():
            return ""
        lines = _read(fp).splitlines()
        start = 0
        if method:
            key = f"{rel}::{method.split('(')[0].split('.')[-1]}"
            info = self.methods.get(key)
            if info:
                start = max(0, info.start_line - 1)
            else:
                name = method.split(".")[-1]
                for i, ln in enumerate(lines):
                    if name + "(" in ln:
                        start = i
                        break
        lo, hi = max(0, start - before), min(len(lines), start + after)
        return "\n".join(lines[lo:hi])

    def find_symbol(self, name: str, limit: int = 20) -> list[dict]:
        name = name.split("(")[0].split(".")[-1]
        out = [{"file": m.file, "method": m.name,
                "line": m.start_line, "end_line": m.end_line}
               for m in self.methods.values() if m.name == name]
        return out[:limit]

    def callers(self, name: str, limit: int = 20) -> list[dict]:
        name = name.split("(")[0].split(".")[-1]
        out = [{"file": m.file, "method": m.name, "line": m.start_line}
               for m in self.methods.values() if name in m.calls]
        return out[:limit]

    def allocation_sites(self, name: str = "") -> list[dict]:
        out = []
        for m in self.methods.values():
            if name and m.name != name.split(".")[-1]:
                continue
            if m.allocations:
                out.append({"file": m.file, "method": m.name,
                            "line": m.start_line,
                            "allocations": m.allocations})
        return out[:50]

    def asset_inventory(self, kind: str = "", min_size: int = 0,
                        limit: int = 200) -> list[dict]:
        rows = [a for a in self.assets
                if (not kind or a["kind"] == kind) and a["size"] >= min_size]
        return rows[:limit]

    def reference_graph(self, asset: str, limit: int = 50) -> list[dict]:
        needle = asset.replace("\\", "/").rsplit("/", 1)[-1]
        hits = []
        for f in self.files:
            rel = f["file"]
            if rel == asset or rel.endswith(needle) or f["size"] > 5_000_000:
                continue
            if not rel.endswith((".prefab", ".unity", ".mat", ".asset")):
                continue
            if needle in _read(self.root / rel):
                hits.append({"file": rel, "size": f["size"]})
            if len(hits) >= limit:
                break
        return hits

    def settings_summary(self) -> dict:
        return {"snapshot_hash": self.snapshot_hash,
                "files": dict(sorted(self.settings.items()))}
