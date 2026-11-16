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
    ev_doc = {"run_id": run_id, "project_id": "", "scene": "",
              "protocol_fingerprint": "",
              "frames": frames, "functions": funcs, "callstacks": [],
              "allocations": [], "render_objects": [], "blindspots": [],
              "raw_refs": [], "meta": {"probe": "unified",
                                       "mode": doc.get("mode", "")}}
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
         "frame_last": doc.get("last_frame"), "counters": counters},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "unified_probe.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def capture_window(project: Path, run_id: str, limit: int,
                   since: str | None = None,
                   start_frame: int = -1,
                   mode: str = "shallow") -> list[dict]:
    """采一窗：统一探针同时写 counters/functions。"""
    UNIFIED_CFG.parent.mkdir(parents=True, exist_ok=True)
    UNIFIED_CFG.write_text(json.dumps({
        "mode": mode, "frames": 300,
        "start_frame": start_frame,
        "budget_ms": 15000}), encoding="utf-8")
    try:
        UNIFIED_OUT.unlink(missing_ok=True)
    except OSError:
        pass
    since = since or time.strftime("%Y-%m-%dT%H:%M:%S")
    d = unity("script.eval_file", "--file", str(UNIFIED_PROBE),
              "--project-path", str(project), "--timeout", "120")
    if not d.get("success"):
        raise RuntimeError("unified probe eval 失败：" +
                           json.dumps(d.get("errors"), ensure_ascii=False)[:300])
    deadline = time.time() + 60
    while time.time() < deadline and not UNIFIED_OUT.exists():
        time.sleep(0.3)
    if not UNIFIED_OUT.exists():
        raise RuntimeError("unified probe 文件未落盘")
    doc = json.loads(UNIFIED_OUT.read_text(encoding="utf-8"))
    _write_unified(doc, run_id)
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
                            start_frame=int(p["window"].get("start_frame", -1)),
                            mode="deep")
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


def run_and_capture(project: Path, run_id: str, limit: int,
                    replay_path: Path, *, enable_deep: bool = True,
                    fast: bool = False, target_windows: bool = False) -> list[dict]:
    """滚动运行 replay：每个动作命令后采一窗；记录帧时间线与异常窗口。"""
    off_first = _eval(project, PROFILER_OFF, timeout=30)
    on = _eval(project, SHALLOW_ON, timeout=30)
    if not on.get("success", True):
        raise RuntimeError("无法开启浅档 profiler：" +
                           json.dumps(on, ensure_ascii=False)[:200])
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
            if cmd.type in ACTION_CMDS:
                try:
                    from upi.tooling import probe_control as _PC
                    _PC.set_window(cmd.type, wait=False)
                except Exception:  # noqa: BLE001
                    pass
            ex._one(cmd)
            timeline.append({"cmd_index": i, "type": cmd.type, "frame": f})
            if take:
                for c in capture_window_with_counters(
                        project, f"{run_id}_a{i}", limit,
                        start_frame=win_start):
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
        _eval(project, PROFILER_OFF, timeout=30)


def run_two_pass_capture(project: Path, run_id: str, limit: int,
                         replay_path: Path) -> list[dict]:
    """r 轻采 → 选 ≤3 窗 → r′ 完整重放、窗口内深采。"""
    from upi.core.two_pass import plan_deep_windows
    light = run_and_capture(project, run_id, limit, replay_path,
                            enable_deep=False)
    run_dir = ROOT / "runs" / run_id
    wins = json.loads((run_dir / "pass1_windows.json").read_text(encoding="utf-8"))
    timeline = json.loads((run_dir / "pass1_timeline.json").read_text(encoding="utf-8"))
    planned = plan_deep_windows(wins, max_n=3, margin=12)
    if not planned:
        (run_dir / "merged_candidates.json").write_text(
            json.dumps(light, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "two_pass.json").write_text(json.dumps(
            {"status": "no_window", "windows": [],
             "reason": "r 未产生异常窗口"}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return light
    deep = deep_windows_capture(project, run_id, replay_path,
                                [w.model_dump() for w in planned],
                                timeline, limit)
    merged = {str(c.get("root_cause") or ""): c for c in light}
    for c in deep:
        merged.setdefault(str(c.get("root_cause") or ""), c)
    merged_list = list(merged.values())
    (run_dir / "merged_candidates.json").write_text(
        json.dumps(merged_list, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "two_pass.json").write_text(json.dumps(
        {"status": "done", "windows": [w.model_dump() for w in planned],
         "light": len(light), "deep": len(deep)},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return merged_list


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
    cands = ((run_two_pass_capture(project, run_id, a.limit, replay_path))
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
