"""I26-ext E4：临时 worktree 消融，目标工程零改动。"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable


def _git(project: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(project), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def is_git(project: Path | str) -> bool:
    p = _git(Path(project), "rev-parse", "--is-inside-work-tree")
    return p.returncode == 0 and p.stdout.strip() == "true"


def _copy_project(project: Path, dest: Path) -> None:
    ignore = shutil.ignore_patterns("Library", "Temp", "Logs", "obj", ".git",
                                    "UserSettings", "Build", "Builds")
    shutil.copytree(project, dest, ignore=ignore)


def create(project: Path | str, dest: Path | None = None) -> tuple[Path, str]:
    root = Path(project).resolve()
    if dest is None:
        dest = Path(tempfile.mkdtemp(prefix="upi_ablate_")) / "project"
        dest.parent.mkdir(parents=True, exist_ok=True)
    if is_git(root):
        p = _git(root, "worktree", "add", "--detach", str(dest), "HEAD")
        if p.returncode != 0:
            raise RuntimeError(f"worktree add failed: {p.stderr[-300:]}")
        return dest, "worktree"
    _copy_project(root, dest)
    return dest, "copy"


def cleanup(project: Path | str, dest: Path, mode: str) -> None:
    root = Path(project).resolve()
    if mode == "worktree":
        p = _git(root, "worktree", "remove", "--force", str(dest))
        if p.returncode == 0:
            return
    shutil.rmtree(dest, ignore_errors=True)
    if mode == "worktree":
        _git(root, "worktree", "prune")


def patch_text(root: Path | str, rel_file: str, find: str,
               replace: str) -> None:
    p = Path(root) / rel_file
    text = p.read_text(encoding="utf-8")
    if find not in text:
        raise ValueError(f"patch find not found: {rel_file}")
    p.write_text(text.replace(find, replace, 1), encoding="utf-8")


def run(project: Path | str, *, patch: dict | None = None,
        run_fn: Callable[[Path], dict] | None = None,
        keep: bool = False) -> dict:
    root = Path(project).resolve()
    dest, mode = create(root)
    try:
        if patch:
            patch_text(dest, str(patch["file"]), str(patch["find"]),
                       str(patch["replace"]))
        result = run_fn(dest) if run_fn else {}
        return {"status": "ok", "mode": mode, "result": result,
                "patched": bool(patch)}
    finally:
        if not keep:
            cleanup(root, dest, mode)
