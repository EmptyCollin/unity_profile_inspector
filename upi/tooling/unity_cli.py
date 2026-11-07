"""工具层：Unity CLI 子进程封装（DESIGN §2 Tool Layer / AGENTS I8）。

红线：
- 不假设命令名：`eval`/`screenshot` 等是否存在由 `unity command` 的运行时返回决定（I8）。
- 失败从 stdout 的 envelope（success/errors）读取，不解析 stderr。
- 停止编辑器只按 PID（G10），本模块不提供按名字杀进程的能力。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from typing import Any, Optional

from pydantic import BaseModel, Field


class UnityError(BaseModel):
    code: str
    message: str = ""


class Envelope(BaseModel):
    """`unity --format json` 的标准信封。"""

    success: bool
    command: str = ""
    data: Any = None
    errors: list[UnityError] = Field(default_factory=list)
    warnings: list[Any] = Field(default_factory=list)
    exit_code: int = 0

    @property
    def error_code(self) -> Optional[str]:
        return self.errors[0].code if self.errors else None


def args_hash(args: list[str]) -> str:
    """trace span 记录参数用哈希而非原文（AGENTS I12：含凭据参数不落值）。"""
    return hashlib.sha256(" ".join(args).encode()).hexdigest()[:16]


def run_unity(argv: list[str], timeout_s: int = 60) -> Envelope:
    """执行 unity CLI（自动附加 --format json --no-banner）。永不抛业务异常，失败在 envelope 里。"""
    cmd = ["unity", *argv, "--format", "json", "--no-banner", "--non-interactive"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return Envelope(success=False, command=argv[0] if argv else "", exit_code=127,
                        errors=[UnityError(code="CLI_NOT_FOUND", message="unity 不在 PATH")])
    except subprocess.TimeoutExpired:
        return Envelope(success=False, command=argv[0] if argv else "", exit_code=124,
                        errors=[UnityError(code="CLI_TIMEOUT", message=f"{timeout_s}s 超时")])
    out = (p.stdout or "").strip()
    if not out:
        return Envelope(success=(p.returncode == 0), command=argv[0] if argv else "", exit_code=p.returncode,
                        errors=[] if p.returncode == 0 else [UnityError(code="EMPTY_STDOUT",
                                message="非零退出且 stdout 为空（该命令未迁移到 envelope 输出，属已知 bug 面）")])
    try:
        env = Envelope.model_validate_json(out)
        env.exit_code = p.returncode
        return env
    except Exception:  # noqa: BLE001 — ndjson/多行输出退化路径
        lines = [ln for ln in out.splitlines() if ln.lstrip().startswith("{")]
        for ln in reversed(lines):
            try:
                env = Envelope.model_validate_json(ln)
                env.exit_code = p.returncode
                return env
            except Exception:  # noqa: BLE001
                continue
        return Envelope(success=False, command=argv[0], exit_code=p.returncode,
                        errors=[UnityError(code="UNPARSABLE_OUTPUT", message=out[:200])])


class Capability(BaseModel):
    """运行时探测到的命令面（I8）。探测不到 → 调用方降级 C 档并标 static_estimate。"""

    reachable: bool
    safe_mode: bool = False
    commands: set[str] = Field(default_factory=set)
    project_path: str = ""

    def has(self, name: str) -> bool:
        return name in self.commands

    def missing(self, names: list[str]) -> list[str]:
        return [n for n in names if not self.has(n)]


def list_commands(project_path: str) -> Capability:
    """unity command --project-path <p>：既是可达性探测，也是真实命令目录（不假设名字）。"""
    env = run_unity(["command", "--project-path", project_path, "--detail", "compact"], timeout_s=45)
    if env.success:
        names: set[str] = set()
        data = env.data
        rows = data if isinstance(data, list) else (data or {}).get("commands", []) if isinstance(data, dict) else []
        for r in rows:
            if isinstance(r, dict):
                n = r.get("name") or r.get("command")
                if n:
                    names.add(str(n))
            elif isinstance(r, str):
                names.add(r)
        return Capability(reachable=True, commands=names, project_path=project_path)
    safe = False
    probe = run_unity(["pipeline", "list", "--format", "json"], timeout_s=45)
    if probe.success and isinstance(probe.data, dict):
        summary = probe.data.get("summary") or {}
        safe = bool(summary.get("instancesInSafeMode"))
    return Capability(reachable=False, safe_mode=safe, project_path=project_path)


def editor_pid(project_path: str) -> Optional[int]:
    """从 pipeline list 读取实例 PID（仅用于 G10 的按 PID 停止）。"""
    env = run_unity(["pipeline", "list"], timeout_s=45)
    if not (env.success and isinstance(env.data, dict)):
        return None
    for inst in env.data.get("instances", []):
        pp = (inst.get("projectPath") or "").replace("\\", "/").lower()
        if pp.endswith(project_path.replace("\\", "/").lower().rstrip("/")):
            pid = inst.get("pid")
            return int(pid) if pid else None
    return None
