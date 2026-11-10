"""trace 埋点（DESIGN §7.1 / I10 / G13）。

- 载体：runs/<run_id>/trace.jsonl，追加写、schema 版本化。
- 控制台只读聚合表，不读原始 jsonl（避免看图把管线跑崩）。
- 含凭据的参数只落 args_hash，不落值（I12）。
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from upi.core.models import TraceSpan

SCHEMA_VERSION = 1


class Tracer:
    def __init__(self, run_id: str, root: Path | str = "runs",
                 channel: str = "user", env_fingerprint: str = "") -> None:
        self.run_id = run_id
        self.channel = channel
        self.env_fingerprint = env_fingerprint
        self.dir = Path(root) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "trace.jsonl"
        self._seq = 0

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    def emit(self, span: TraceSpan) -> None:
        rec = span.model_dump()
        rec["schema_version"] = SCHEMA_VERSION
        # 落盘时刻（span 结束后补记）。契约 TraceSpan 里没有这个字段也不该有——
        # 它是"这条记录何时写下去"的事实，不是 span 的语义属性。历史 75 条 span
        # 全无时间戳，导致统计层只能拿文件 mtime 反推轮次时刻（近似且会被后续
        # 写入推移）；补上之后新数据的时间轴才是实测的，旧数据由聚合层如实降级。
        rec["emitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    @contextmanager
    def stage(self, stage: str, issue_id: Optional[str] = None,
              attempt_id: Optional[str] = None) -> Iterator[dict]:
        """用法：with tracer.stage('detect', issue_id='I-1') as ctx: ... ctx['exit_status']=0"""
        t0 = time.perf_counter()
        ctx: dict = {"tool_calls": [], "llm": None, "evidence_refs": [],
                     "exit_status": 0}
        try:
            yield ctx
        finally:
            self.emit(TraceSpan(
                run_id=self.run_id, stage=stage, seq=self._next(),
                wallclock_ms=int((time.perf_counter() - t0) * 1000),
                exit_status=int(ctx.get("exit_status", 0)),
                issue_id=issue_id, attempt_id=attempt_id,
                tool_calls=list(ctx.get("tool_calls", [])),
                llm=ctx.get("llm"), evidence_refs=list(ctx.get("evidence_refs", [])),
                environment_fingerprint=self.env_fingerprint,
                channel=self.channel,  # type: ignore[arg-type]
            ))

    def tool_call(self, ctx: dict, kind: str, name: str, args: str = "",
                  duration_ms: int = 0, exit_status: int = 0) -> None:
        """记录一次外部调用。args 只取哈希，绝不落原文（可能含凭据）。"""
        import hashlib
        ctx.setdefault("tool_calls", []).append({
            "kind": kind, "name": name,
            "args_hash": hashlib.sha256(args.encode()).hexdigest()[:16] if args else "",
            "duration_ms": duration_ms, "exit_status": exit_status,
        })


def environment_fingerprint(project_path: str = "",
                            profile_id: Optional[str] = None) -> str:
    """Unity/URP 版本 × GPU 驱动 × 电源计划 × 工程标识（G7 环境漂移门禁的输入）。

    采集失败时返回带 unknown 标记的串，而不是猜测值（I1）。
    ⚠️ 中文 Windows 的控制台输出编码不稳定（ver/powercfg 直出会乱码），
       因此对外只暴露 sha256 摘要 + 关键 GUID，保证跨会话可比。

    阶段 7 批 2：标签位从"工程目录名"改为 profile_id（档案标识）。
    为什么目录名不够：两个工程可以同名（同一模板复制出两份 MyGame，
    或 CI 把工程 checkout 到不同父目录但末级同名）——那时目录名相同而
    内容完全不同，指纹却一致，等于 G7 形同虚设。profile_id 是接入时
    人为声明、必须全局唯一的标识。
    ⚠️ 内置档案的 profile_id 恰好等于其目录名（都是 upi-fixture），
      已实测两种写法产生的 sha256 摘要逐字节相同 ⇒ 历史 38 轮次的
      env_fingerprint 不改写也继续可比，不必因这次改动整库作废。
      project_path 参数保留：没传 profile_id 时退回目录名，老调用点行为不变。
    """
    import hashlib
    import re

    raw: list[str] = []
    ok = True
    try:
        import subprocess
        for cmd in (["cmd.exe", "/c", "ver"],
                    ["cmd.exe", "/c", "powercfg /getactivescheme"]):
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                               encoding="utf-8", errors="replace")
            raw.append(p.stdout or "")
    except Exception:  # noqa: BLE001
        ok = False
    raw.append(os.sys.version.split()[0])
    label = profile_id or (Path(project_path).name if project_path else "")
    if label:
        raw.append(label)
    blob = "|".join(raw).encode("utf-8", "replace")
    power_guid = ""
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                  "|".join(raw), re.I)
    if m:
        power_guid = m.group(1)
    return f"sha256:{hashlib.sha256(blob).hexdigest()[:16]}|power:{power_guid or 'unknown'}|collect:{'ok' if ok else 'partial'}"
