"""工具能力运行：在指定工程上跑 replay → 证据采集 → 候选 → findings.json。

不读 oracle、不读 case 编号、不做正则匹配：只记录工具自己从运行时证据
得到的候选（机制 + 代码锚点 + 证据引用），供考卷按能力判分。

前提：目标工程已在 Unity 中打开（pipeline 可达）。
用法：
    python scripts/capability_run.py --project test_project --project-id test_project \
        --replay runs/i8_lab/paper_replay.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.findings import (Anchor, AttributionInfo,  # noqa: E402
                               RunFindings, ToolFinding, new_run_id,
                               normalize_file, normalize_method, save_run)
from upi.core.protocol import build as fp_build, digest as fp_digest, file_hash  # noqa: E402
from upi.adapters.devsim import ReplayExecutor  # noqa: E402
from upi.core.replay_spec import load_replay  # noqa: E402

def _render_probe(src: Path, name: str) -> Path:
    """按 UPI_GAME_TYPE/UPI_BOARD_TYPE 生成探针，避免测试工程类名写死。"""
    game = os.environ.get("UPI_GAME_TYPE", "DevSim.ChromaGame")
    board = os.environ.get("UPI_BOARD_TYPE", "DevSim.BoardModel")
    gsimple = game.rsplit(".", 1)[-1]
    bsimple = board.rsplit(".", 1)[-1]
    text = src.read_text(encoding="utf-8")
    text = (text.replace("DevSim.ChromaGame", game)
                .replace("DevSim.BoardModel", board)
                .replace("ChromaGame", gsimple)
                .replace("BoardModel", bsimple))
    out = ROOT / "runs" / "_probe" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


UNIFIED_PROBE = _render_probe(ROOT / "probe" / "upi_unified_probe.cs",
                              "generated_unified_probe.cs")
UNIFIED_CFG = ROOT / "runs" / "_probe" / "unified_cfg.json"
UNIFIED_OUT = ROOT / "runs" / "_probe" / "unified_probe.json"
EV = ROOT / "runs" / "_probe" / "marker_evidence.json"

SHALLOW_ON = ("UnityEngine.Profiling.Profiler.enabled=true;"
              "UnityEditorInternal.ProfilerDriver.enabled=true;"
              "UnityEditorInternal.ProfilerDriver.profileEditor=true;"
              "UnityEditorInternal.ProfilerDriver.deepProfiling=false;"
              "UnityEngine.Profiling.Profiler.enableAllocationCallstacks=false;"
              "return \"on\";")
PROFILER_OFF = ("UnityEngine.Profiling.Profiler.enableAllocationCallstacks=false;"
                "UnityEditorInternal.ProfilerDriver.deepProfiling=false;"
                "UnityEditorInternal.ProfilerDriver.enabled=false;"
                "UnityEngine.Profiling.Profiler.enabled=false;"
                "UnityEditorInternal.ProfilerDriver.ClearAllFrames();return \"off\";")


def unity(*args, timeout: int = 120) -> dict:
    p = subprocess.run(["unity", "command", *args, "--format", "json"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except Exception:  # noqa: BLE001
        return {"success": False, "raw": (p.stdout or p.stderr)[-300:]}


def _status(project: Path) -> dict:
    d = unity("editor status", "--project-path", str(project))
    if not d.get("success"):
        return {}
    return (d.get("data") or {}).get("result") or {}


def _eval(project: Path, code: str, timeout: int = 60) -> dict:
    d = unity("script eval", "--code", code, "--project-path", str(project),
              "--timeout", str(timeout))
    return ((d.get("data") or {}).get("result") or {})


def _profiler_frame(project: Path) -> int:
    try:
        r = _eval(project, "return UnityEditorInternal.ProfilerDriver.lastFrameIndex;")
        return int(r.get("result", r.get("output", -1)))
    except Exception:  # noqa: BLE001
        return -1


def ensure_play(project: Path) -> dict:
    st = _status(project)
    if not st:
        raise RuntimeError(f"工程未在 Unity 打开或 pipeline 不可达：{project}")
    if st.get("playMode") == "playing":
        return st
    unity("editor.playmode start", "--project-path", str(project))
    for _ in range(60):
        time.sleep(0.5)
        st = _status(project)
        if st.get("playMode") == "playing":
            return st
    raise RuntimeError("进入 Play Mode 超时")


def _write_unified(doc: dict, run_id: str) -> None:
    """把统一探针输出落成 evidence 输入 + counters 输入。"""
    rows = doc.get("counters") or []
    frames = [int(x.get("frame_idx") or 0) for x in rows]
    funcs = doc.get("functions") or []
    alloc_rows = doc.get("allocations") or []
    ev_allocs = [{
        "frame_idx": int(x.get("frame") or 0),
        "bytes": int(x.get("bytes") or 0), "count": 1,
        "stack": [{"symbol": str(x.get("path") or ""),
                   "source": None, "native": False, "address": ""}],
        "data_tier": "A2"} for x in alloc_rows]
    ev_doc = {"run_id": run_id, "project_id": "", "scene": "",
              "protocol_fingerprint": "",
              "frames": frames, "functions": funcs, "callstacks": [],
              "allocations": ev_allocs, "render_objects": [], "blindspots": [],
              "raw_refs": [], "meta": {"probe": "unified",
                                       "mode": doc.get("mode", ""),
                                       "counter_lag_frames": 4,
                                       "edges": doc.get("edges") or [],
                                       "paths": doc.get("paths") or [],
                                       "allocations": alloc_rows}}
    EV.write_text(json.dumps(ev_doc, ensure_ascii=False), encoding="utf-8")
    names = set()
    for x in rows:
        names.update((x.get("counters") or {}).keys())
    counters = {}
    for k in names:
        counters[k] = {"series": [x.get("counters", {}).get(k)
                                  for x in rows
                                  if x.get("counters", {}).get(k) is not None]}
    out = ROOT / "runs" / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "counters.json").write_text(json.dumps(
        {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "frame_last": doc.get("last_frame"),
         "counter_lag_frames": 4,
         "counters": counters},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "unified_probe.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


SESSION_TRIGGER = ROOT / "runs" / "_probe" / "unified_trigger.txt"
SESSION_STOP = ROOT / "runs" / "_probe" / "unified_stop.txt"
SESSION_TRIGGER_DIR = ROOT / "runs" / "_probe" / "triggers"
SESSION_WINDOW_DIR = ROOT / "runs" / "_probe" / "windows"
SESSION_ACK_DIR = ROOT / "runs" / "_probe" / "acks"
SESSION_HEARTBEAT = ROOT / "runs" / "_probe" / "heartbeat.json"
SESSION_LEASE = ROOT / "runs" / "_probe" / "lease.txt"
SESSION_FINISH = ROOT / "runs" / "_probe" / "finish.txt"
_SESSION_STARTED = False
_LEASE_THREAD = None
_LEASE_STOP = False


def start_full_probe(project: Path, mode: str) -> None:
    UNIFIED_CFG.parent.mkdir(parents=True, exist_ok=True)
    UNIFIED_OUT.unlink(missing_ok=True)
    SESSION_FINISH.unlink(missing_ok=True)
    UNIFIED_CFG.write_text(json.dumps({
        "mode": mode, "frames": 100000, "budget_ms": 600000,
        "finish_file": str(SESSION_FINISH)}), encoding="utf-8")
    d = unity("script.eval_file", "--file", str(UNIFIED_PROBE),
              "--project-path", str(project), "--timeout", "120")
    if not d.get("success"):
        raise RuntimeError("unified full probe install 失败：" +
                           json.dumps(d.get("errors"), ensure_ascii=False)[:300])


def finish_full_probe(project: Path, run_id: str, limit: int,
                      since: str | None = None) -> list[dict]:
    SESSION_FINISH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FINISH.write_text("finish", encoding="utf-8")
    deadline = time.time() + 120
    while time.time() < deadline and not UNIFIED_OUT.exists():
        time.sleep(0.3)
    if not UNIFIED_OUT.exists():
        raise RuntimeError("unified full probe 文件未落盘")
    doc = json.loads(UNIFIED_OUT.read_text(encoding="utf-8"))
    _write_unified(doc, run_id)
    return _collect_run(project, run_id, limit, since, UNIFIED_OUT)


def run_full_capture(project: Path, run_id: str, limit: int,
                     replay_path: Path, mode: str) -> list[dict]:
    _clear_console(project)
    start_full_probe(project, mode)
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    spec = load_replay(replay_path) if replay_path.exists() else None
    if spec is None:
        return []
    ex = ReplayExecutor(project)
    for cmd in spec.commands:
        ex._one(cmd)  # noqa: SLF001
    cands = finish_full_probe(project, run_id, max(30, limit), since=started)
    run_dir = ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "merged_candidates.json").write_text(
        json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")
    errs = _console_errors(project)
    (run_dir / "console_errors.json").write_text(
        json.dumps(errs, ensure_ascii=False, indent=2), encoding="utf-8")
    if errs:
        raise RuntimeError(f"控制台有 {len(errs)} 条 error，拒绝使用该轮采样")
    return cands


def _lease_loop() -> None:
    while not _LEASE_STOP:
        try:
            SESSION_LEASE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_LEASE.write_text("alive", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)


def start_session(project: Path) -> None:
    """一次 replay 只装一次常驻探针；心跳/租约/trigger 队列。"""
    global _SESSION_STARTED, _LEASE_THREAD, _LEASE_STOP
    if _SESSION_STARTED:
        return
    UNIFIED_CFG.parent.mkdir(parents=True, exist_ok=True)
    for d in (SESSION_TRIGGER_DIR, SESSION_WINDOW_DIR, SESSION_ACK_DIR):
        d.mkdir(parents=True, exist_ok=True)
        for p in d.glob("*.json"):
            p.unlink(missing_ok=True)
    SESSION_TRIGGER.unlink(missing_ok=True)
    SESSION_STOP.unlink(missing_ok=True)
    SESSION_HEARTBEAT.unlink(missing_ok=True)
    _LEASE_STOP = False
    _LEASE_THREAD = threading.Thread(target=_lease_loop, daemon=True)
    _LEASE_THREAD.start()
    UNIFIED_CFG.write_text(json.dumps({
        "mode": "resident", "budget_ms": 600000,
        "pre_frames": 8, "post_frames": 8,
        "trigger_dir": str(SESSION_TRIGGER_DIR),
        "window_dir": str(SESSION_WINDOW_DIR),
        "ack_dir": str(SESSION_ACK_DIR),
        "heartbeat": str(SESSION_HEARTBEAT),
        "lease": str(SESSION_LEASE), "lease_ms": 10000,
        "stop_file": str(SESSION_STOP)}), encoding="utf-8")
    d = unity("script.eval_file", "--file", str(UNIFIED_PROBE),
              "--project-path", str(project), "--timeout", "120")
    if not d.get("success"):
        raise RuntimeError("unified session install 失败：" +
                           json.dumps(d.get("errors"), ensure_ascii=False)[:300])
    deadline = time.time() + 10
    while time.time() < deadline and not SESSION_HEARTBEAT.exists():
        time.sleep(0.2)
    if not SESSION_HEARTBEAT.exists():
        raise RuntimeError("unified session 心跳未出现")
    _SESSION_STARTED = True


def stop_session() -> None:
    """显式清理常驻探针：stop → 等心跳消失 → 停租约。"""
    global _SESSION_STARTED, _LEASE_STOP
    _LEASE_STOP = True
    _SESSION_STARTED = False
    try:
        SESSION_STOP.parent.mkdir(parents=True, exist_ok=True)
        SESSION_STOP.write_text("stop", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    deadline = time.time() + 10
    while time.time() < deadline and SESSION_HEARTBEAT.exists():
        time.sleep(0.2)
    try:
        SESSION_LEASE.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def arm_trigger(tag: str) -> None:
    SESSION_TRIGGER_DIR.mkdir(parents=True, exist_ok=True)
    (SESSION_TRIGGER_DIR / f"{tag}.json").write_text(
        json.dumps({"tag": tag}), encoding="utf-8")


def finish_trigger(project: Path, run_id: str, tag: str, limit: int,
                   since: str | None = None) -> list[dict]:
    p = SESSION_WINDOW_DIR / f"{tag}.json"
    ack = SESSION_ACK_DIR / f"{tag}.json"
    deadline = time.time() + 60
    while time.time() < deadline and not (p.exists() and ack.exists()):
        time.sleep(0.2)
    if not p.exists():
        raise RuntimeError(f"resident trigger {tag} 未落盘")
    doc = json.loads(p.read_text(encoding="utf-8"))
    _write_unified(doc, run_id)
    return _collect_run(project, run_id, limit, since, p)


def start_window(project: Path, run_id: str, start_frame: int = -1,
                 mode: str = "shallow") -> None:
    """动作开始前安装统一探针；finish_window 读取。"""
    UNIFIED_CFG.parent.mkdir(parents=True, exist_ok=True)
    UNIFIED_CFG.write_text(json.dumps({
        "mode": mode, "frames": 300,
        "start_frame": start_frame,
        "budget_ms": 15000}), encoding="utf-8")
    try:
        UNIFIED_OUT.unlink(missing_ok=True)
    except OSError:
        pass
    d = unity("script.eval_file", "--file", str(UNIFIED_PROBE),
              "--project-path", str(project), "--timeout", "120")
    if not d.get("success"):
        raise RuntimeError("unified probe install 失败：" +
                           json.dumps(d.get("errors"), ensure_ascii=False)[:300])


def _collect_run(project: Path, run_id: str, limit: int,
                 since: str | None = None, raw_ref: Path | None = None) -> list[dict]:
    since = since or time.strftime("%Y-%m-%dT%H:%M:%S")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "collect_evidence.py"),
                    "--project", str(project), "--evidence", str(EV),
                    "--run-id", run_id, "--scene",
                    "Assets/Scenes/00_Bootstrap.unity",
                    "--events-since", since],
                   cwd=str(ROOT), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60)
    bundle = ROOT / "runs" / run_id / "evidence" / "evidence_v2.json"
    out = ROOT / "runs" / run_id / "evidence" / "candidates.json"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "evidence_candidates.py"),
                    "--project", str(project), "--evidence", str(bundle),
                    "--out", str(out), "--limit", str(limit)],
                   cwd=str(ROOT), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60)
    if not out.exists():
        return []
    return json.loads(out.read_text(encoding="utf-8")).get("candidates", [])


def finish_window(project: Path, run_id: str, limit: int,
                  since: str | None = None) -> list[dict]:
    since = since or time.strftime("%Y-%m-%dT%H:%M:%S")
    deadline = time.time() + 60
    while time.time() < deadline and not UNIFIED_OUT.exists():
        time.sleep(0.3)
    if not UNIFIED_OUT.exists():
        raise RuntimeError("unified probe 文件未落盘")
    doc = json.loads(UNIFIED_OUT.read_text(encoding="utf-8"))
    _write_unified(doc, run_id)
    return _collect_run(project, run_id, limit, since, UNIFIED_OUT)


def capture_window(project: Path, run_id: str, limit: int,
                   since: str | None = None,
                   start_frame: int = -1,
                   mode: str = "shallow") -> list[dict]:
    start_window(project, run_id, start_frame=start_frame, mode=mode)
    return finish_window(project, run_id, limit, since=since)


ACTION_CMDS = {"set_board", "resolve", "resolve_board", "swap", "open_shop"}
DEEP_ON = ("UnityEngine.Profiling.Profiler.enabled=true;"
           "UnityEditorInternal.ProfilerDriver.enabled=true;"
           "UnityEditorInternal.ProfilerDriver.profileEditor=true;"
           "UnityEditorInternal.ProfilerDriver.deepProfiling=true;"
           "UnityEngine.Profiling.Profiler.enableAllocationCallstacks=true;"
           "UnityEditorInternal.ProfilerDriver.ClearAllFrames();return \"deep\";")


def deep_capture(project: Path, run_id: str, limit: int) -> list[dict]:
    """一次 bounded 深档采集：统一探针，同一帧写 counters/functions。"""
    _eval(project, PROFILER_OFF, timeout=30)
    try:
        return capture_window(project, run_id, limit, mode="deep")
    finally:
        _eval(project, PROFILER_OFF, timeout=30)


def _frame(project: Path) -> int:
    try:
        r = _eval(project, "return UnityEngine.Time.frameCount;", timeout=20)
        return int(r.get("result") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _windows_since(project: Path, started: str) -> list[dict]:
    from upi.core.anomaly_candidates import windows_from_events
    from upi.core.probe_events import EventLog
    events, _bad = EventLog().read_all()
    recent = [e for e in events
              if str(e.ts) >= started
              and (not e.project_id or e.project_id == project.name)]
    return windows_from_events(recent)


def deep_windows_capture(project: Path, run_id: str, replay_path: Path,
                         windows: list[dict], timeline: list[dict],
                         limit: int) -> list[dict]:
    """r′：完整重放；只在窗口对应命令区间内开深档并采集。"""
    parsed = []
    for w in windows:
        start, end = int(w["start_frame"]), int(w["end_frame"])
        idxs = [t["cmd_index"] for t in timeline
                if start <= int(t["frame"]) <= end]
        if not idxs:
            # 窗口落在命令之间：用最接近的后一条命令
            later = [t["cmd_index"] for t in timeline
                     if int(t["frame"]) >= start]
            if not later:
                continue
            idxs = [later[0]]
        parsed.append({"start_idx": min(idxs), "end_idx": max(idxs),
                       "window": w})
    if not parsed:
        return []
    starts = {p["start_idx"] for p in parsed}
    ends: dict[int, list[dict]] = {}
    for p in parsed:
        ends.setdefault(p["end_idx"], []).append(p)
    merged: dict[str, dict] = {}
    spec = load_replay(replay_path)
    ex = ReplayExecutor(project)
    _eval(project, PROFILER_OFF, timeout=30)
    deep = False
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        for i, cmd in enumerate(spec.commands):
            if i in starts and not deep:
                _eval(project, DEEP_ON, timeout=30)
                deep = True
            ex._one(cmd)  # noqa: SLF001
            if i in ends and deep:
                for k, p in enumerate(ends[i]):
                    tag = f"{run_id}_w{k}"
                    try:
                        cands = capture_window(
                            project, tag, limit, since=started,
                            start_frame=-1, mode="deep")
                        for c in cands:
                            key = str(c.get("root_cause") or "")
                            if key and key not in merged:
                                merged[key] = c
                    except Exception as e:  # noqa: BLE001
                        print("deep window failed:", type(e).__name__, e)
                _eval(project, PROFILER_OFF, timeout=30)
                deep = False
        return list(merged.values())
    finally:
        _eval(project, PROFILER_OFF, timeout=30)


def capture_window_with_counters(project: Path, tag: str, limit: int,
                                 since: str | None = None,
                                 meta: dict | None = None,
                                 start_frame: int = -1) -> list[dict]:
    cands = capture_window(project, tag, limit, since=since,
                           start_frame=start_frame)
    try:
        d = ROOT / "runs" / tag
        d.mkdir(parents=True, exist_ok=True)
        (d / "window_meta.json").write_text(
            json.dumps(meta or {}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("counters skipped:", type(e).__name__, e)
    return cands


def _console_errors(project: Path) -> list[str]:
    try:
        r = unity("console.get", "--project-path", str(project), timeout=30)
        entries = ((r.get("data") or {}).get("result") or {}).get("entries") or []
        return [f"{x.get('message','')} | {str(x.get('stackTrace',''))[:200]}"
                for x in entries if str(x.get("level", "")).lower() == "error"]
    except Exception as e:  # noqa: BLE001
        return [f"console.get 失败：{type(e).__name__}: {e}"]


def _clear_console(project: Path) -> None:
    try:
        unity("console.clear", "--project-path", str(project), timeout=30)
    except Exception:  # noqa: BLE001
        pass


def run_and_capture(project: Path, run_id: str, limit: int,
                    replay_path: Path, *, enable_deep: bool = True,
                    fast: bool = False, target_windows: bool = False) -> list[dict]:
    """滚动运行 replay：每个动作命令后采一窗；记录帧时间线与异常窗口。"""
    off_first = _eval(project, PROFILER_OFF, timeout=30)
    on = _eval(project, SHALLOW_ON, timeout=30)
    if not on.get("success", True):
        raise RuntimeError("无法开启浅档 profiler：" +
                           json.dumps(on, ensure_ascii=False)[:200])
    _clear_console(project)
    start_session(project)
    merged: dict[str, dict] = {}
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    timeline: list[dict] = []
    try:
        spec = load_replay(replay_path) if replay_path.exists() else None
        if spec is None:
            return []
        ex = ReplayExecutor(project)
        fast_done = False
        last_action = {}
        for idx, cmd in enumerate(spec.commands):
            if cmd.type in ACTION_CMDS:
                last_action[cmd.type] = idx
        for i, cmd in enumerate(spec.commands):
            if fast and cmd.type == "stop" and not fast_done:
                for c in capture_window_with_counters(
                        project, f"{run_id}_end", limit, since=started):
                    k = str(c.get("root_cause") or "")
                    if k and k not in merged:
                        merged[k] = c
                fast_done = True
            f = _frame(project)
            take = ((cmd.type in ACTION_CMDS)
                    and (target_windows
                         and last_action.get(cmd.type) == i
                         or (not fast and not target_windows)))
            win_start = _profiler_frame(project) if take else -1
            tag = f"{run_id}_a{i}"
            if take:
                print("session trigger", i, cmd.type, tag, flush=True)
                arm_trigger(tag)
            if cmd.type in ACTION_CMDS:
                try:
                    from upi.tooling import probe_control as _PC
                    _PC.set_window(cmd.type, wait=False)
                except Exception:  # noqa: BLE001
                    pass
            ex._one(cmd)
            timeline.append({"cmd_index": i, "type": cmd.type, "frame": f})
            if take:
                for c in finish_trigger(project, tag, tag, limit,
                                        since=started):
                    k = str(c.get("root_cause") or "")
                    if k and k not in merged:
                        merged[k] = c
        if fast and not fast_done:
            for c in capture_window_with_counters(
                    project, f"{run_id}_end", limit, since=started):
                k = str(c.get("root_cause") or "")
                if k and k not in merged:
                    merged[k] = c
        if not fast and not target_windows:
            for c in capture_window_with_counters(
                    project, f"{run_id}_end", limit):
                k = str(c.get("root_cause") or "")
                if k and k not in merged:
                    merged[k] = c
        _errs = _console_errors(project)
        _err_path = ROOT / "runs" / run_id / "console_errors.json"
        _err_path.parent.mkdir(parents=True, exist_ok=True)
        _err_path.write_text(
            json.dumps(_errs, ensure_ascii=False, indent=2), encoding="utf-8")
        if _errs:
            raise RuntimeError(f"控制台有 {len(_errs)} 条 error，拒绝使用该轮采样")
        wins = _windows_since(project, started)
        try:
            from upi.core.anomaly_candidates import windows_from_bundle
            from upi.core.evidence_v2 import EvidenceBundleV2
            evs = list((ROOT / "runs").glob(
                f"{run_id}*/evidence/evidence_v2.json"))
            for evp in evs:
                try:
                    b = EvidenceBundleV2(
                        **json.loads(evp.read_text(encoding="utf-8")))
                    wins = wins + windows_from_bundle(b)
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001 — 证据触发失败不阻断
            pass
        run_dir = ROOT / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "pass1_timeline.json").write_text(
            json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "pass1_windows.json").write_text(
            json.dumps(wins, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            from upi.core.trace_analysis import write_trace
            write_trace(run_id, ROOT / "runs")
        except Exception:  # noqa: BLE001 — trace 生成失败不阻断采集
            pass
        if not enable_deep:
            (run_dir / "merged_candidates.json").write_text(
                json.dumps(list(merged.values()), ensure_ascii=False, indent=2),
                encoding="utf-8")
            return list(merged.values())
        try:
            from upi.core.capture_policy import plan_for
            metric = str(wins[0].get("metric", "")) if wins else ""
            trigger = {"frame_time_ms": "frame_time",
                       "cpuMainThreadFrameTimeMs": "frame_time",
                       "gc_alloc_per_frame": "gc_alloc",
                       "drawCalls": "draw_calls",
                       "total_used_memory": "memory"}.get(metric, "frame_time")
            plan = plan_for("regression", trigger,
                            severity=float(wins[0].get("score", 0)) if wins else 0.0)
            plan_path = run_dir / "capture_plan.json"
            plan_path.write_text(json.dumps(
                {**plan.model_dump(), "anomaly_windows": wins[:5],
                 "fallback_low_coverage": len(merged) < 6},
                ensure_ascii=False, indent=2), encoding="utf-8")
            if wins or len(merged) < 6:
                for c in deep_capture(project, f"{run_id}_deep", limit):
                    k = str(c.get("root_cause") or "")
                    if k and k not in merged:
                        merged[k] = c
            else:
                print("deep capture skipped: no anomaly trigger")
        except Exception as e:  # noqa: BLE001
            print("deep capture skipped:", type(e).__name__, e)
        (run_dir / "merged_candidates.json").write_text(
            json.dumps(list(merged.values()), ensure_ascii=False, indent=2),
            encoding="utf-8")
        return list(merged.values())
    finally:
        stop_session()
        _eval(project, PROFILER_OFF, timeout=30)


def run_two_pass_capture(project: Path, run_id: str, limit: int,
                         replay_path: Path) -> list[dict]:
    """r 浅采全程 → r′ 深采全程；深采只用于定因。"""
    light = run_full_capture(project, run_id, limit, replay_path, "shallow")
    deep = run_full_capture(project, f"{run_id}_deep", limit,
                            replay_path, "deep")
    merged = {str(c.get("root_cause") or ""): c for c in light}
    for c in deep:
        merged.setdefault(str(c.get("root_cause") or ""), c)
    out = list(merged.values())
    run_dir = ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "merged_candidates.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "two_pass.json").write_text(json.dumps(
        {"status": "done", "light": len(light), "deep": len(deep),
         "mode": "full"}, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _anchor_of(c: dict) -> list[Anchor]:
    out: list[Anchor] = []
    for s in c.get("sources") or []:
        f = normalize_file(str(s.get("file") or ""))
        if not f:
            continue
        out.append(Anchor(file=f, method=normalize_method(str(s.get("symbol") or "")),
                          start_line=int(s.get("start_line") or 0),
                          end_line=int(s.get("end_line") or 0)))
    if not out:
        for a in c.get("code_anchors") or []:
            sp = str(a)
            f, _, line = sp.rpartition(":")
            try:
                ln = int(line)
            except ValueError:
                f, ln = sp, 0
            f = normalize_file(f)
            if f:
                out.append(Anchor(file=f, start_line=ln, end_line=ln))
    return out


def asset_findings(project: Path) -> list[ToolFinding]:
    """资产能力：Resources 下超大文本/资产 → 包体/内存机会（度量，不是模式匹配）。"""
    res = project / "Assets" / "Resources"
    out: list[ToolFinding] = []
    if not res.is_dir():
        return out
    for p in res.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in (".txt", ".bytes", ".json", ".asset"):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size <= 128 * 1024:
            continue
        rel = normalize_file(p.relative_to(project).as_posix())
        out.append(ToolFinding(
            finding_id=f"asset-{len(out)}", mechanism="oversized_resource_asset",
            metric="total_used_memory", anchors=[Anchor(file=rel)],
            attribution=AttributionInfo(confidence=1.0), state="confirmed"))
    return out


def _compile(project: Path) -> dict:
    unity("recompile", "--project-path", str(project))
    deadline = time.time() + 90
    last: dict = {}
    while time.time() < deadline:
        d = unity("recompile_status", "--project-path", str(project))
        st = ((d.get("data") or {}).get("result") or {})
        last = st
        if st.get("status") in {"completed", "up_to_date", "failed"}:
            return st
        time.sleep(1)
    return last or {"status": "timeout"}


def _smoke(project: Path) -> dict:
    """可运行性冒烟：编译后能进 Play 并正常启动一局（不跑玩法自测）。"""
    unity("editor.playmode stop", "--project-path", str(project))
    unity("scene open", "--path", "Assets/Scenes/00_Bootstrap.unity",
          "--project-path", str(project))
    unity("editor.playmode start", "--project-path", str(project))
    time.sleep(2.0)
    try:
        st = ReplayExecutor(project).start_game()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    if isinstance(st, str):
        try:
            st = json.loads(st)
        except Exception:  # noqa: BLE001
            pass
    ok = isinstance(st, dict) and str(st.get("phase") or "") == "Playing"
    unity("editor.playmode stop", "--project-path", str(project))
    return {"ok": ok, "state": st}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=str(ROOT / "test_project"))
    ap.add_argument("--project-id", default="")
    ap.add_argument("--replay", default=str(ROOT / "runs" / "i8_lab" / "paper_replay.json"))
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--no-save", action="store_true",
                    help="只采集证据，不写 findings（考试时由诊断产物负责记账）")
    ap.add_argument("--two-pass", action="store_true",
                    help="r 轻采→选≤3窗→r′ 完整重放、窗口内深采")
    ap.add_argument("--light", action="store_true",
                    help="只做一次轻采，不做 deep（验证/快速基线）")
    ap.add_argument("--full-shallow", action="store_true",
                    help="一次 replay 浅采全程（回放基线）")
    ap.add_argument("--full-deep", action="store_true",
                    help="一次 replay 深采全程（回放定因）")
    ap.add_argument("--fast", action="store_true",
                    help="整轮 replay 只在末尾采一窗，不逐 action 采")
    ap.add_argument("--target-windows", action="store_true",
                    help="只在该类动作最后一次发生时采窗")
    ap.add_argument("--run-id", default="",
                    help="固定 run_id（adapter 驱动时使用）")
    a = ap.parse_args()
    if a.two_pass and a.light:
        raise SystemExit("--two-pass 与 --light 不能同时使用")
    if a.fast and a.target_windows:
        raise SystemExit("--fast 与 --target-windows 不能同时使用")
    project = Path(a.project).resolve()
    project_id = a.project_id or project.name
    replay_path = Path(a.replay)

    ensure_play(project)
    run_id = a.run_id or new_run_id(project_id)
    cands = ((run_full_capture(project, run_id, a.limit, replay_path, "shallow"))
             if a.full_shallow else
             (run_full_capture(project, run_id, a.limit, replay_path, "deep"))
             if a.full_deep else
             (run_two_pass_capture(project, run_id, a.limit, replay_path))
             if a.two_pass else
             run_and_capture(project, run_id, a.limit, replay_path,
                             enable_deep=not a.light, fast=a.fast,
                             target_windows=a.target_windows))

    findings: list[ToolFinding] = []
    for i, c in enumerate(cands):
        anchors = _anchor_of(c)
        if not anchors:
            continue      # 没有位置的候选不入账（能力口径要求可定位）
        findings.append(ToolFinding(
            finding_id=f"fd-{run_id}-{i}",
            mechanism=str(c.get("mechanism") or c.get("kind") or ""),
            metric=str(c.get("metric") or ""),
            link="",
            anchors=anchors,
            evidence_refs=[str(x) for x in (c.get("evidence_ids") or [])],
            attribution=AttributionInfo(confidence=0.0, factors={}),
            state="candidate",
        ))
    from upi.core.asset_opportunity import scan as _scan_opportunities
    opportunities = _scan_opportunities(project)
    try:
        (ROOT / "runs" / run_id).mkdir(parents=True, exist_ok=True)
        (ROOT / "runs" / run_id / "opportunities.json").write_text(
            json.dumps([o.as_dict() for o in opportunities],
                       ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    run = RunFindings(
        run_id=run_id, project_id=project_id, mode="resident",
        protocol_fingerprint=fp_digest(fp_build(
            replay_script_hash=file_hash(replay_path), data_tier="A2",
            editor_mode="gui")),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        findings=findings, repairs=[],
    )
    p = "" if a.no_save else str(save_run(run))
    print(json.dumps({"ok": True, "run_id": run_id, "project_id": project_id,
                      "findings": len(findings), "path": p,
                      "saved": not a.no_save},
                     ensure_ascii=False))
    for f in findings[:8]:
        print(f"  {f.mechanism:14s} {f.anchors[0].file}::{f.anchors[0].method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
