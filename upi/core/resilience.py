"""韧性层（I14）：错误分类、重试策略、checkpoint。

目标不是无限重试，而是：
- 临时错误有界重试；
- 数据不足降级；
- 确定性错误立即停；
- 每步可落盘、可从断点续跑。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

ErrorKind = Literal["transient", "data", "deterministic", "logic", "unknown"]

_TRANSIENT = ("timeout", "timed out", "connection", "refused", "reset",
              "temporarily", "rate limit", "429", "502", "503", "504",
              "busy", "unavailable", "broken pipe", "eof")
_DATA = ("empty", "no candidates", "no evidence", "not found",
         "insufficient", "missing bundle", "no findings")
_DETERMINISTIC = ("permission", "denied", "invalid", "schema", "unsupported",
                  "no such project", "bad replay", "not configured",
                  "unauthorized", "forbidden")
_LOGIC = ("no plan", "no_new_hypothesis", "stagnant", "no hypothesis",
          "cannot plan", "insufficient_evidence")


def classify_error(err: object) -> ErrorKind:
    text = str(err or "").lower()
    if not text:
        return "unknown"
    if any(k in text for k in _DETERMINISTIC):
        return "deterministic"
    if any(k in text for k in _LOGIC):
        return "logic"
    if any(k in text for k in _DATA):
        return "data"
    if any(k in text for k in _TRANSIENT):
        return "transient"
    return "unknown"


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int
    backoff_s: float

    @property
    def retryable(self) -> bool:
        return self.attempts > 0


def policy_for(kind: ErrorKind) -> RetryPolicy:
    if kind == "transient":
        return RetryPolicy(attempts=2, backoff_s=1.0)
    if kind == "data":
        return RetryPolicy(attempts=1, backoff_s=0.5)
    return RetryPolicy(attempts=0, backoff_s=0.0)


def call_with_retry(fn: Callable[[], object], *, stage: str,
                    on_error: Optional[Callable[[str, BaseException], None]] = None):
    """执行 fn；按错误分类有界重试。返回 (ok, result_or_error, kind, attempts)。"""
    last: Optional[BaseException] = None
    kind: ErrorKind = "unknown"
    attempts = 0
    for i in range(6):
        attempts = i + 1
        try:
            return True, fn(), "unknown", attempts
        except BaseException as e:  # noqa: BLE001
            last = e
            kind = classify_error(e)
            pol = policy_for(kind)
            if on_error is not None:
                try:
                    on_error(stage, e)
                except Exception:  # noqa: BLE001
                    pass
            if not pol.retryable or i >= pol.attempts:
                break
            time.sleep(pol.backoff_s * (i + 1))
    return False, last, kind, attempts


class Checkpoint:
    """run 级断点：state.json，原子写；只记录，不替业务做决定。"""

    def __init__(self, run_dir: Path | str):
        self.dir = Path(run_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def save(self, *, step: str, status: str, data: dict | None = None) -> dict:
        state = self.load()
        steps = state.setdefault("steps", {})
        steps[step] = {"status": status,
                       "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if data:
            state.update(data)
        state["last_step"] = step
        state["last_status"] = status
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.path)
        return state

    def done(self, step: str) -> bool:
        return (self.load().get("steps", {}).get(step, {})
                .get("status") == "done")

    def resume_point(self, order: list[str]) -> str:
        """返回第一个未完成的步骤；全完成返回 'done'。"""
        for s in order:
            if not self.done(s):
                return s
        return "done"
