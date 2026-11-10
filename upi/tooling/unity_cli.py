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


def normalize_envelope(d: Any) -> dict:
    """把 `unity command` 的返回统一成 {success, data:{result}, errors} 信封。

    ⚠️ 为什么需要（实测，2026-11-10 CLI 1.0.0-beta.17）：新版 CLI 对
      `unity command <x>` 直接返回**载荷本体**（get_performance →
      {"render","memory","frameTiming"}；editor status → {"status",...}；
      script eval → {"output","diagnostics","success","result"}；
      playmode → 裸字符串），不再裹 {success,data:{result}} 信封。
      消费方（sync 判稳、采样取 render、fixer 取 recompile）全按旧信封读
      `data.result` ⇒ 新版下恒拿 {}——表现为"采前同步 240s 超时""渲染值全空"，
      看着像编辑器没就绪，其实是读错层（真工程验收第一次把它撞出来；
      夹具下次真采样同样会断）。

    判据（不猜、按结构）：带 success 且（带 data 或带 errors）= 已是信封，
    原样返回（含失败信封，让上层照读 success/errors）；否则视为裸载荷，
    包成成功信封。裸载荷自身可能带 success（eval），但它没有 data/errors
    键，故仍走包装分支——包装后 eval 的内层 result 落在 data.result.result，
    与旧消费方读法逐字一致。
    """
    if isinstance(d, dict) and "success" in d and ("data" in d or "errors" in d):
        return d
    return {"success": True, "data": {"result": d}, "errors": []}


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
    # 解析顺序（实测教训）：**先解原始 JSON，再归一，最后建模**。
    # 反过来的话，新版 `script eval` 的裸载荷 {output,diagnostics,success,result}
    # 会因为自带 success 键而被 Envelope 模型当成"成功但 data=None 的信封"接受，
    # 归一层根本没机会跑——消费方照旧拿到 None（我第一版就踩在这）。
    try:
        raw = json.loads(out)
    except json.JSONDecodeError:
        raw = None
    if raw is not None:
        env = Envelope.model_validate(normalize_envelope(raw))
        env.exit_code = p.returncode
        return env
    # 非单一 JSON 文档（ndjson/多行输出）：逐行退化
    lines = [ln for ln in out.splitlines() if ln.lstrip().startswith("{")]
    for ln in reversed(lines):
        try:
            env = Envelope.model_validate(normalize_envelope(json.loads(ln)))
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
