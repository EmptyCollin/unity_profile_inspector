"""统一探针控制（I3b）：安装/卸载/采样开关/状态（文件 + Unity CLI）。

迁移纪律：
- 装新探针前先卸旧 `UpiBreachProbe`（`pb_cmd.txt:unload`）与旧 sampler
  （`cmd.txt:stop`）；旧回调无法从新 eval 卸载（I9），所以不能并存。
- 控制走 `probe_cmd.txt`，结果 `probe_res.txt`，心跳 `pb_hb.txt`；
- 安装走 `unity command script eval_file --file probe/upi_probe_v4.cs`（I8：
  命令存在性由运行时决定，不假设）。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from upi.core import events as E
from upi.tooling.unity_cli import run_unity

ROOT = Path(__file__).resolve().parents[2]
PROBE_FILE = ROOT / "probe" / "upi_probe_v4.cs"


def _probe_dir() -> Path:
    return E.RUNS / "_probe"


def _write(path: Path, text: str):
    E.atomic_write_text(path, text + "\n")


def _wait_response(before_mtime: float, timeout_s: float) -> str:
    """等 probe_res.txt 被新写；超时返回空（不冒充成功）。"""
    p = _probe_dir() / "probe_res.txt"
    deadline = time.time() + max(0.0, timeout_s)
    while time.time() < deadline:
        try:
            if p.exists():
                mt = p.stat().st_mtime
                if mt > before_mtime:
                    txt = p.read_text(encoding="utf-8", errors="replace").strip()
                    if txt:
                        return txt
        except OSError:
            pass
        time.sleep(0.08)
    return ""


def send_cmd(cmd: str, *, wait: bool = True, timeout_s: float = 3.0) -> str:
    d = _probe_dir()
    d.mkdir(parents=True, exist_ok=True)
    res = d / "probe_res.txt"
    before = res.stat().st_mtime if res.exists() else 0.0
    _write(d / "probe_cmd.txt", cmd)
    if not wait:
        return ""
    out = _wait_response(before, timeout_s)
    return out or "ERR no-response"


def legacy_stop() -> None:
    """卸旧 Editor 探针 + 停旧 sampler；失败不抛（启动路径不能被旧残留卡死）。"""
    d = _probe_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        _write(d / "pb_cmd.txt", "unload")
    except Exception:  # noqa: BLE001
        pass
    try:
        _write(d / "cmd.txt", "stop")
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.25)


def set_window(window: str, *, wait: bool = False) -> str:
    """设置当前动作窗口；探针会写入 breach JSON 的 window 字段。"""
    return send_cmd("window " + str(window or ""), wait=wait, timeout_s=2.0)


def install(project: Optional[Path | str] = None, *, timeout_s: int = 180) -> dict:
    if not PROBE_FILE.exists():
        return {"ok": False, "detail": f"probe file missing: {PROBE_FILE}"}
    if project is None:
        try:
            from upi.config.project_profile import active_profile
            project = active_profile().abs_project
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": f"profile unavailable: {e}"}
    legacy_stop()
    # 旧统一探针回调无法从新 eval 卸载（I9）；先 unload，再删心跳，
    # 否则 Main() 看到旧心跳新鲜会直接 return，装出“alive 但仍是旧版本”。
    try:
        send_cmd("unload", wait=True, timeout_s=3.0)
    except Exception:  # noqa: BLE001
        pass
    try:
        (_probe_dir() / "pb_hb.txt").unlink()
    except OSError:
        pass
    # 无探针时 unload 命令没人消费；不清掉会被新装的回调立刻读到并自杀。
    for name in ("probe_cmd.txt", "probe_res.txt"):
        try:
            (_probe_dir() / name).unlink()
        except OSError:
            pass
    r = run_unity(
        ["command", "script eval_file", "--project-path", str(project),
         "--file", str(PROBE_FILE)],
        timeout_s=timeout_s,
    )
    payload = r.data or {}
    inner = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(inner, dict):
        inner = payload if isinstance(payload, dict) else {}
    detail = str(inner.get("result") or payload.get("result") or "")[:400]
    ok = bool(r.success) and inner.get("success", True) is not False
    if not ok and r.errors:
        detail = detail or r.errors[0].message
    if "install_error" in detail or detail.startswith("ERR"):
        ok = False
    return {"ok": ok, "detail": detail or (r.errors[0].message if r.errors else ""),
            "project": str(project)}


def unload() -> dict:
    resp = send_cmd("unload")
    legacy_stop()
    try:
        (_probe_dir() / "pb_hb.txt").unlink()
    except OSError:
        pass
    ok = resp == "unloaded" or "unloaded" in resp
    return {"ok": ok, "detail": resp}


def sample(on: bool = True, window: int = 300) -> dict:
    if on:
        win = max(30, min(600, int(window or 300)))
        resp = send_cmd(f"sample:on:{win}")
        ok = resp.startswith("sample=on")
        return {"ok": ok, "detail": resp, "window": win}
    resp = send_cmd("sample:off")
    return {"ok": resp.startswith("sample=off") or "sample=off" in resp,
            "detail": resp}


def wait_alive(timeout_s: float = 8.0) -> dict:
    """等待探针心跳/状态可用；用于编译/安装后的稳定点，不冒充成功。"""
    deadline = time.time() + max(0.0, timeout_s)
    while True:
        s = status()
        if s["state"] == "alive" and (s.get("status") or {}).get("state") == "ok":
            return s
        if time.time() >= deadline:
            return s
        time.sleep(0.25)


def status() -> dict:
    d = _probe_dir()
    status_fp = d / "probe_status.json"
    hb = d / "pb_hb.txt"
    age = None
    try:
        age = round(time.time() - hb.stat().st_mtime, 1)
    except OSError:
        pass
    raw = {}
    try:
        import json
        raw = json.loads(status_fp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        raw = {}
    state = "missing" if age is None else ("alive" if age <= 5 else "stale")
    return {"state": state, "heartbeat_age_s": age, "status": raw,
            "probe_file": str(PROBE_FILE)}


def clear() -> dict:
    resp = send_cmd("clear")
    return {"ok": "cleared" in resp, "detail": resp}
