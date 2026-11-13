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
from upi.core.replay_exec import ReplayExecutor  # noqa: E402
from upi.core.replay_spec import load_replay  # noqa: E402

PROBE = ROOT / "probe" / "upi_marker_evidence.cs"
EV = ROOT / "runs" / "_probe" / "marker_evidence.json"
CFG = ROOT / "runs" / "_probe" / "capture_cfg.json"

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


def capture_window(project: Path, run_id: str, limit: int) -> list[dict]:
    """采一窗（不跑 replay）：扫最近若干帧的 marker，生成候选。"""
    CFG.parent.mkdir(parents=True, exist_ok=True)
    CFG.write_text(json.dumps({"marker_frames": 300, "board_threads": 8,
                               "marker_budget_ms": 15000}), encoding="utf-8")
    try:
        EV.unlink(missing_ok=True)
    except OSError:
        pass
    d = unity("script eval", "--file", str(PROBE), "--project-path", str(project),
              "--timeout", "120")
    if not d.get("success"):
        raise RuntimeError("marker 取证 eval 失败：" +
                           json.dumps(d.get("errors"), ensure_ascii=False)[:300])
    deadline = time.time() + 60
    while time.time() < deadline and not EV.exists():
        time.sleep(0.3)
    if not EV.exists():
        raise RuntimeError("marker 取证文件未落盘")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "collect_evidence.py"),
                    "--project", str(project), "--evidence", str(EV),
                    "--run-id", run_id, "--scene",
                    "Assets/Scenes/00_Bootstrap.unity"],
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
EV_DEEP = ROOT / "runs" / "_probe" / "evidence_v1.json"
DEEP_ON = ("UnityEngine.Profiling.Profiler.enabled=true;"
           "UnityEditorInternal.ProfilerDriver.enabled=true;"
           "UnityEditorInternal.ProfilerDriver.profileEditor=true;"
           "UnityEditorInternal.ProfilerDriver.deepProfiling=true;"
           "UnityEngine.Profiling.Profiler.enableAllocationCallstacks=true;"
           "UnityEditorInternal.ProfilerDriver.ClearAllFrames();return \"deep\";")


def deep_capture(project: Path, run_id: str, limit: int) -> list[dict]:
    """一次 bounded 深档采集（8 帧/8 线程），补分配点与调用栈候选。"""
    CFG.write_text(json.dumps({"evidence_threads": 8, "evidence_frames": 8,
                               "evidence_budget_ms": 6000}), encoding="utf-8")
    try:
        EV_DEEP.unlink(missing_ok=True)
    except OSError:
        pass
    _eval(project, PROFILER_OFF, timeout=30)
    _eval(project, DEEP_ON, timeout=30)
    try:
        time.sleep(1.2)
        d = unity("script eval", "--file", str(ROOT / "probe" / "upi_evidence_v1.cs"),
                  "--project-path", str(project), "--timeout", "120")
        if not d.get("success"):
            return []
        deadline = time.time() + 45
        while time.time() < deadline and not EV_DEEP.exists():
            time.sleep(0.3)
        if not EV_DEEP.exists():
            return []
        subprocess.run([sys.executable, str(ROOT / "scripts" / "collect_evidence.py"),
                        "--project", str(project), "--evidence", str(EV_DEEP),
                        "--run-id", run_id, "--scene",
                        "Assets/Scenes/00_Bootstrap.unity"],
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
    finally:
        _eval(project, PROFILER_OFF, timeout=30)


def run_and_capture(project: Path, run_id: str, limit: int,
                    replay_path: Path) -> list[dict]:
    """滚动运行 replay：每个动作命令后采一窗，合并候选（覆盖 replay 全程）。"""
    off_first = _eval(project, PROFILER_OFF, timeout=30)
    on = _eval(project, SHALLOW_ON, timeout=30)
    if not on.get("success", True):
        raise RuntimeError("无法开启浅档 profiler：" +
                           json.dumps(on, ensure_ascii=False)[:200])
    merged: dict[str, dict] = {}
    try:
        spec = load_replay(replay_path) if replay_path.exists() else None
        if spec is None:
            return []
        ex = ReplayExecutor(project)
        for i, cmd in enumerate(spec.commands):
            ex._one(cmd)  # noqa: SLF001 - 复用同一执行器
            if cmd.type in ACTION_CMDS:
                for c in capture_window(project, f"{run_id}_a{i}", limit):
                    k = str(c.get("root_cause") or "")
                    if k and k not in merged:
                        merged[k] = c
        # 尾窗：捕获未归类动作（例如自动回落）后的状态
        for c in capture_window(project, f"{run_id}_end", limit):
            k = str(c.get("root_cause") or "")
            if k and k not in merged:
                merged[k] = c
        # 深档一窗：补分配点/调用栈（marker 看不到的 GC 证据）
        try:
            for c in deep_capture(project, f"{run_id}_deep", limit):
                k = str(c.get("root_cause") or "")
                if k and k not in merged:
                    merged[k] = c
        except Exception as e:  # noqa: BLE001 — 深档失败不拖垮整个采集
            print("deep capture skipped:", type(e).__name__, e)
        return list(merged.values())
    finally:
        _eval(project, PROFILER_OFF, timeout=30)


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
    a = ap.parse_args()
    project = Path(a.project).resolve()
    project_id = a.project_id or project.name
    replay_path = Path(a.replay)

    ensure_play(project)
    run_id = new_run_id(project_id)
    cands = run_and_capture(project, run_id, a.limit, replay_path)

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
    findings.extend(asset_findings(project))
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
