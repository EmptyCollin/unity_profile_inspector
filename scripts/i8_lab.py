"""I8 Lab：本地体验页。两个模式严格分开，不要混用结论。

A. 真实诊断（diagnostic）
   单个 case / 单组 case → apply → replay → 定向采集该 case 需要的窗口
   → 候选按 case 作用域过滤 → Agent 归因 → 修复。
   不把别的 case 的候选混进来，也不做跨 case 排名。

B. 组合自测（combo）
   只验证 overlay/patch 能否叠加、能否编译、合并 replay 能否跑，
   以及每个 case 的文件/指令是否真的被覆盖（存在性断言）。
   不产候选、不做 Agent 归因、不承诺跨 case top8。

安全约定沿用：不改 base 原始副本；apply 前要求 sim/project 干净；
编译失败自动 restore；不按进程名杀 Unity；不删 Scene Backup。

用法：
    python scripts/i8_lab.py --port 8787
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.case_overlay import apply_cases  # noqa: E402
from upi.core.models import Evidence, Issue, IssueSignature  # noqa: E402
from upi.core.replay_exec import ReplayExecutor  # noqa: E402
from upi.core.replay_spec import load_replay, merge_replays  # noqa: E402

CASES_DIR = ROOT / "sim" / "cases"
LAB = ROOT / "runs" / "i8_lab"
PROJECT = ROOT / "sim" / "project"
DIAG = LAB / "evidence" / "candidates.json"
PREVIEW = LAB / "preview.json"
TOUCHED = LAB / "touched.json"
COMPILE = LAB / "compile.json"
REPLAY = LAB / "replay.json"

# 取证参数：用户可改（文件或页面），探针每次采集前读取。
CAPTURE_CFG = ROOT / "config" / "i8_lab.json"
PROBE_CFG = ROOT / "runs" / "_probe" / "capture_cfg.json"
CFG_DEFAULTS = {
    "evidence_threads": 8,
    "evidence_frames": 8,
    "evidence_budget_ms": 6000,
    "board_threads": 8,
    "board_frames": 4,
    "board_budget_ms": 5000,
}
CFG_LIMITS = {
    "evidence_threads": (1, 64),
    "evidence_frames": (1, 32),
    "evidence_budget_ms": (500, 20000),
    "board_threads": (1, 64),
    "board_frames": (1, 8),
    "board_budget_ms": (500, 20000),
}


def load_capture_cfg() -> dict:
    cfg = dict(CFG_DEFAULTS)
    try:
        raw = json.loads(CAPTURE_CFG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        raw = {}
    for k, (lo, hi) in CFG_LIMITS.items():
        v = raw.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            cfg[k] = max(lo, min(hi, v))
    return cfg


def save_capture_cfg(body: dict) -> dict:
    cfg = load_capture_cfg()
    for k, (lo, hi) in CFG_LIMITS.items():
        v = body.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{k} 必须是整数")
        iv = int(v)
        if not (lo <= iv <= hi):
            raise ValueError(f"{k} 超出范围 [{lo},{hi}]：{iv}")
        cfg[k] = iv
    CAPTURE_CFG.parent.mkdir(parents=True, exist_ok=True)
    CAPTURE_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return cfg


def _write_probe_cfg() -> dict:
    cfg = load_capture_cfg()
    PROBE_CFG.parent.mkdir(parents=True, exist_ok=True)
    PROBE_CFG.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return cfg

MODES = {"diagnostic", "combo"}
CAPTURE_LIMIT = 60


# ─────────────────────────── case 元数据 ───────────────────────────

def _case_ids() -> list[str]:
    return sorted(d.name for d in CASES_DIR.glob("c*") if (d / "manifest.json").is_file())


def _manifest(case_id: str) -> dict:
    p = CASES_DIR / case_id / "manifest.json"
    if not p.is_file():
        raise ValueError(f"case 不存在：{case_id}")
    return json.loads(p.read_text(encoding="utf-8"))


def _expects(m: dict) -> dict:
    """case 期望：需要哪些采集窗口、应出现哪些符号、资产断言。"""
    return m.get("expects") or {"windows": [], "symbols": [], "assert": {}}


def cases() -> list[dict]:
    out = []
    for cid in _case_ids():
        m = _manifest(cid)
        ex = _expects(m)
        out.append({"case_id": m.get("case_id", cid),
                    "overlay": m.get("overlay", []),
                    "patches": m.get("patches", []),
                    "replay": m.get("replay", ""),
                    "oracle": m.get("oracle", ""),
                    "notes": m.get("notes", ""),
                    "windows": ex.get("windows", []),
                    "symbols": ex.get("symbols", [])})
    return out


def _case_files(m: dict) -> list[str]:
    files = list(m.get("overlay", []))
    for p in m.get("patches", []):
        if p.get("file") and p["file"] not in files:
            files.append(p["file"])
    return files


# ─────────────────────────── 组合 ───────────────────────────

def preview(case_ids: list[str]) -> dict:
    dirs = [CASES_DIR / c for c in case_ids]
    for d in dirs:
        if not (d / "manifest.json").exists():
            raise ValueError(f"case 不存在：{d.name}")
    out = LAB / "project"
    run = apply_cases(PROJECT, dirs, out)
    specs = []
    per_case = {}
    for d in dirs:
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        per_case[m.get("case_id", d.name)] = _case_files(m)
        if m.get("replay"):
            specs.append(load_replay(ROOT / "sim" / "replay" / f"{m['replay']}.json"))
    merged = merge_replays(specs) if specs else None
    if merged:
        REPLAY.write_text(merged.model_dump_json(indent=2), encoding="utf-8")
    return {"ok": True, "cases": run.case_ids, "files": run.files,
            "per_case": per_case, "workdir": str(out),
            "replay_id": (merged.replay_id if merged else ""),
            "replay_commands": (len(merged.commands) if merged else 0)}


def _git_clean() -> bool:
    p = subprocess.run(["git", "status", "--porcelain", "--", "sim/project"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return not (p.stdout or "").strip()


def unity(*args, timeout=120) -> dict:
    p = subprocess.run(["unity", "command", *args, "--project-path", str(PROJECT),
                        "--format", "json"], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"success": False, "raw": p.stdout[-500:]}


def _eval(code: str, timeout: int = 60) -> dict:
    d = unity("script eval", "--code", code, "--timeout", str(timeout))
    r = ((d.get("data") or {}).get("result") or {})
    return {"ok": bool(d.get("success") and r.get("success")),
            "result": r.get("result"),
            "errors": r.get("errors") or d.get("errors")}


def _compile_and_wait() -> dict:
    unity("recompile")
    deadline = time.time() + 30
    st: dict = {}
    while time.time() < deadline:
        st = ((unity("recompile_status").get("data") or {}).get("result") or {})
        if st.get("status") in {"completed", "up_to_date", "failed"}:
            break
        time.sleep(1)
    COMPILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    return st


def apply_to_base() -> dict:
    if not PREVIEW.exists():
        return {"ok": False, "error": "先组合预览"}
    if not _git_clean():
        return {"ok": False, "error": "sim/project 有未提交改动，拒绝应用"}
    pv = json.loads(PREVIEW.read_text(encoding="utf-8"))
    src = Path(pv["workdir"])
    backup = LAB / "backup"
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir(parents=True)
    touched = []
    for rel in pv["files"]:
        s = src / rel
        d = PROJECT / rel
        b = backup / rel
        b.parent.mkdir(parents=True, exist_ok=True)
        if d.exists():
            shutil.copy2(d, b)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        touched.append(rel)
    TOUCHED.write_text(json.dumps(touched, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    st = _compile_and_wait()
    if st.get("failed"):
        restore_base()
        return {"ok": False, "error": "编译失败，已自动恢复 base", "compile": st}
    ready = _wait_editor_ready(120)
    if not ready.get("ok"):
        return {"ok": False, "error": "应用后编辑器未就绪", "compile": st,
                "editor": ready.get("last")}
    return {"ok": True, "touched": touched, "compile": st}


def restore_base() -> dict:
    touched = json.loads(TOUCHED.read_text(encoding="utf-8")) if TOUCHED.exists() else []
    if touched:
        subprocess.run(["git", "checkout", "--", *[f"sim/project/{r}" for r in touched]],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    # 恢复后清掉“已应用/已编译”记录，避免下次覆盖检查拿旧状态当本次证据。
    for f in (TOUCHED, COMPILE):
        try:
            f.unlink()
        except OSError:
            pass
    _auto_tick(False)
    unity("editor.playmode stop")
    return {"ok": True, "restored": touched, "clean": _git_clean()}


# ─────────────────────────── replay ───────────────────────────

def editor_status() -> dict:
    d = unity("editor status")
    if not d.get("success"):
        return {"ok": False, "error": "Unity pipeline 不可达；不自动开新窗口",
                "detail": d.get("errors") or d.get("raw")}
    s = ((d.get("data") or {}).get("result") or {})
    return {"ok": True, "status": s.get("status"), "playMode": s.get("playMode")}


def _in_play() -> str:
    es = editor_status()
    if not es.get("ok"):
        return "Unity pipeline 不可达"
    if es.get("playMode") != "playing":
        return f"Unity 需要在 Play Mode（当前 {es.get('playMode')}）"
    return ""


def _wait_editor_ready(timeout: float = 120.0) -> dict:
    """等编辑器结束编译/域重载。apply 一落盘就会触发重编译，此时 pipeline 会短暂失联。"""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        d = unity("editor status")
        if d.get("success"):
            s = ((d.get("data") or {}).get("result") or {})
            last = s
            # Play Mode 下 status 会是 playing 而不是 ready；只以编译/域重载为阻塞判据。
            if not s.get("compiling") and not s.get("domainReloadInProgress"):
                return {"ok": True, **s}
        time.sleep(1)
    return {"ok": False, "error": "编辑器未就绪（编译或域重载未完成）", "last": last}


def _auto_tick(enabled: bool = True) -> dict:
    """失焦时保持编辑器出帧；会话级设置，不做持久化残留。"""
    return unity("editor set_auto_tick", "--enabled", "true" if enabled else "false",
                 "--interval-ms", "16", "--persist", "false")


def _editor_mem() -> dict:
    """读正在跑本工程的 Unity Editor 进程内存；采集前后各看一次，防止把桌面拖崩。"""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='Unity.exe'\" | "
          "Where-Object { $_.CommandLine -and $_.CommandLine -notmatch 'batchMode' "
          "-and $_.CommandLine -match 'sim[\\\\/]project' } | "
          "Select-Object ProcessId,@{n='mb';e={[int]($_.WorkingSetSize/1MB)}} | "
          "ConvertTo-Json -Compress")
    p = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30)
    raw = (p.stdout or "").strip()
    if not raw:
        return {}
    try:
        v = json.loads(raw)
    except Exception:
        return {}
    if isinstance(v, list):
        v = max(v, key=lambda x: x.get("mb") or 0) if v else {}
    return v if isinstance(v, dict) else {}


def _ensure_play(wait: float = 25.0) -> dict:
    """需要时自动进 Play Mode；超时如实失败，不假设已进入。"""
    _wait_editor_ready(90)
    es = editor_status()
    if not es.get("ok"):
        return {"ok": False, "error": "Unity pipeline 不可达", "editor": es}
    if es.get("playMode") == "playing":
        _auto_tick(True)
        return {"ok": True, "entered": False, "playMode": "playing"}
    d = unity("editor.playmode start")
    if not d.get("success"):
        return {"ok": False, "error": "进入 Play Mode 失败",
                "detail": d.get("errors") or d.get("raw")}
    _auto_tick(True)
    deadline = time.time() + wait
    while time.time() < deadline:
        es = editor_status()
        if es.get("playMode") == "playing":
            return {"ok": True, "entered": True, "playMode": "playing"}
        time.sleep(0.5)
    return {"ok": False, "error": "进入 Play Mode 超时", "editor": es}


def run_lab_replay() -> dict:
    if not REPLAY.exists():
        return {"ok": False, "error": "先组合预览"}
    play = _ensure_play()
    if not play.get("ok"):
        return {"ok": False, "error": play.get("error"), "editor": play.get("editor")}
    spec = load_replay(REPLAY)
    steps = ReplayExecutor(PROJECT).run(spec)
    slim = []
    for s in steps:
        st = s.get("state")
        if isinstance(st, str) and len(st) > 300:
            try:
                st = json.loads(st)
            except Exception:
                st = st[:300]
        if isinstance(st, dict):
            st = {k: st.get(k) for k in ("phase", "shopOpen", "totalCleared", "accepted",
                                         "round", "score", "coins") if k in st}
        slim.append({**s, "state": st})
    return {"ok": True, "replay_id": spec.replay_id, "steps": slim}


# ─────────────────────────── 证据采集 ───────────────────────────

PROFILER_OFF_CODE = (
    "UnityEngine.Profiling.Profiler.enableAllocationCallstacks = false;"
    "UnityEditorInternal.ProfilerDriver.deepProfiling = false;"
    "UnityEditorInternal.ProfilerDriver.enabled = false;"
    "UnityEngine.Profiling.Profiler.enabled = false;"
    "UnityEditorInternal.ProfilerDriver.ClearAllFrames();"
    "return \"off\";")


PROFILER_ON_DEEP_CODE = (
    "UnityEngine.Profiling.Profiler.enabled = true;"
    "UnityEditorInternal.ProfilerDriver.enabled = true;"
    "UnityEditorInternal.ProfilerDriver.profileEditor = true;"
    "UnityEditorInternal.ProfilerDriver.deepProfiling = true;"
    "UnityEngine.Profiling.Profiler.enableAllocationCallstacks = true;"
    "return \"on\";")

PROFILER_ON_SHALLOW_CODE = (
    "UnityEngine.Profiling.Profiler.enabled = true;"
    "UnityEditorInternal.ProfilerDriver.enabled = true;"
    "UnityEditorInternal.ProfilerDriver.profileEditor = true;"
    "UnityEditorInternal.ProfilerDriver.deepProfiling = false;"
    "UnityEngine.Profiling.Profiler.enableAllocationCallstacks = false;"
    "return \"on\";")


def _force_profiler_off() -> dict:
    """无条件收尾：无论取证成功、超时还是抛异常，都不允许 profiler 留在开启态。

    单次深档采集若在回调里被打断（域重载/主线程被占），探针的 finally 不会执行，
    实测可以把编辑器内存推到 17GB 并让 pipeline 失联 —— 这一层是硬兜底。
    """
    try:
        r = _eval(PROFILER_OFF_CODE, timeout=30)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": bool(r.get("ok")), "result": r.get("result"),
            "errors": r.get("errors")}


def _emergency_stop_if_runaway() -> dict:
    """break-glass：编辑器被主线程卡死且内存失控时，按确认 PID 停掉，保护桌面。"""
    m = _editor_mem()
    mb = m.get("mb") or 0
    pid = m.get("ProcessId")
    if mb >= 9000 and pid:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Stop-Process -Id {int(pid)} -Force"],
                       capture_output=True, text=True, timeout=60)
        return {"killed": True, "pid": pid, "mb": mb}
    return {"killed": False, "pid": pid, "mb": mb}


def _run_probe_file(path: Path, ev: Path) -> dict:
    """跑一个取证 .cs，并显式检查 eval 的外层/内层结果；收尾必关 profiler。"""
    try:
        try:
            ev.unlink()
        except OSError:
            pass
        p = subprocess.run(["unity", "command", "script eval", "--project-path", str(PROJECT),
                            "--file", str(path), "--timeout", "90", "--format", "json"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        try:
            d = json.loads(p.stdout)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"取证 eval 输出不是 JSON：{(p.stdout or p.stderr)[-300:]}") from e
        if not d.get("success"):
            raise RuntimeError("Unity 拒绝取证 eval：" +
                               json.dumps(d.get("errors"), ensure_ascii=False)[:300])
        inner = ((d.get("data") or {}).get("result") or {})
        if not inner.get("success", True):
            raise RuntimeError("取证脚本内部失败：" +
                               json.dumps(inner.get("errors") or inner.get("result"),
                                          ensure_ascii=False)[:300])
        # 采集脚本自带 4s 主线程预算；这里留足 eval+落盘余量。
        deadline = time.time() + 45
        while time.time() < deadline and not ev.exists():
            time.sleep(0.2)
        if not ev.exists():
            raise RuntimeError("取证文件未落盘：" + str(ev) +
                               "（Unity 可能已退出，见 sim/project/Logs/Editor.log）")
        return inner
    finally:
        off = _force_profiler_off()
        if not off.get("ok"):
            stop = _emergency_stop_if_runaway()
            raise RuntimeError(
                "profiler 收尾失败（主线程可能被取证占住）；"
                + (f"编辑器内存 {stop['mb']}MB 已按 PID {stop['pid']} 强制停止。"
                   if stop.get("killed") else
                   "编辑器仍可用，但本次取证作废。") +
                json.dumps(off.get("errors") or off.get("error"), ensure_ascii=False)[:160])
        m = _editor_mem()
        if (m.get("mb") or 0) > 6000:
            stop = _emergency_stop_if_runaway()
            raise RuntimeError(
                f"取证后编辑器占用 {m['mb']}MB，超出安全线；"
                + (f"已按 PID {stop['pid']} 强制停止。" if stop.get("killed")
                   else "请先重启编辑器再做下一次取证。"))


def _capture_once(tag: str, limit: int = 8) -> list[dict]:
    ev = ROOT / "runs" / "_probe" / "evidence_v1.json"
    _run_probe_file(ROOT / "probe" / "upi_evidence_v1.cs", ev)
    run_id = f"lab_{tag}"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "collect_evidence.py"),
                    "--project", str(PROJECT), "--evidence", str(ev),
                    "--run-id", run_id, "--scene", "Assets/Scenes/00_Bootstrap.unity"],
                   cwd=str(ROOT), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60)
    bundle = ROOT / "runs" / run_id / "evidence" / "evidence_v2.json"
    out = ROOT / "runs" / run_id / "evidence" / "candidates.json"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "evidence_candidates.py"),
                    "--project", str(PROJECT), "--evidence", str(bundle),
                    "--out", str(out), "--limit", str(limit)],
                   cwd=str(ROOT), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60)
    if not out.exists():
        return []
    return json.loads(out.read_text(encoding="utf-8")).get("candidates", [])


def _capture_board(limit: int = 8) -> list[dict]:
    ev = ROOT / "runs" / "_probe" / "board_evidence.json"
    _run_probe_file(ROOT / "probe" / "upi_board_evidence.cs", ev)
    run_id = "lab_board"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "collect_evidence.py"),
                    "--project", str(PROJECT), "--evidence", str(ev),
                    "--run-id", run_id, "--scene", "Assets/Scenes/00_Bootstrap.unity"],
                   cwd=str(ROOT), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60)
    bundle = ROOT / "runs" / run_id / "evidence" / "evidence_v2.json"
    out = ROOT / "runs" / run_id / "evidence" / "candidates.json"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "evidence_candidates.py"),
                    "--project", str(PROJECT), "--evidence", str(bundle),
                    "--out", str(out), "--limit", str(limit)],
                   cwd=str(ROOT), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60)
    if not out.exists():
        return []
    return json.loads(out.read_text(encoding="utf-8")).get("candidates", [])


GAME = 'var g=UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();'


def _force_board_resolve() -> dict:
    colors = [0, 0, 0] + [((i // 6 + i % 6) % 5) for i in range(3, 36)]
    layers = [1] * 36
    code = (GAME + 'if(g==null) return "no-game";'
            f'g.DebugSetBoard(6,new int[]{{{",".join(str(x) for x in colors)}}},'
            f'new int[]{{{",".join(str(x) for x in layers)}}});'
            'return g.DebugResolveStep();')
    return _eval(code)


def _hud_ready() -> dict:
    return _eval(GAME + 'if(g==null) return "no-game"; return g.DebugState();')


def _touch_hud() -> dict:
    return _eval(GAME + 'if(g==null) return "no-game"; return g.DebugSetScore(0);')


def _open_shop() -> dict:
    code = (GAME + 'if(g==null) return "no-game";'
            'var m=g.GetType().GetMethod("OpenShop",'
            'System.Reflection.BindingFlags.NonPublic|'
            'System.Reflection.BindingFlags.Instance);'
            'm.Invoke(g,null); return g.DebugState();')
    return _eval(code)


def _parse_state(r: dict) -> dict:
    v = r.get("result")
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {"raw": v[:200]}
    return v if isinstance(v, dict) else {}


def _candidate_texts(c: dict) -> list[str]:
    out = [str(c.get("root_cause") or "")]
    out += [str(x) for x in (c.get("code_anchors") or [])]
    for s in c.get("sources") or []:
        if isinstance(s, dict):
            out.append(str(s.get("symbol") or ""))
    return [t.lower() for t in out if t]


def _matches_symbols(c: dict, symbols: list[str]) -> bool:
    if not symbols:
        return False
    texts = _candidate_texts(c)
    return any(sym.lower() in t for sym in symbols for t in texts)


def _dedupe(items: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for c in items:
        k = str(c.get("root_cause") or "")
        if k not in merged or c.get("score", 0) > merged[k].get("score", 0):
            merged[k] = c
    return sorted(merged.values(), key=lambda c: c.get("score", 0), reverse=True)


def _slim(c: dict) -> dict:
    return {"kind": c.get("kind"), "root_cause": c.get("root_cause"),
            "score": c.get("score"), "evidence_n": c.get("evidence_n"),
            "anchor": (c.get("code_anchors") or [""])[0]}


def capture_diagnostic(case_ids: list[str]) -> dict:
    """只采当前目标需要的窗口，并只保留该目标作用域内的候选。"""
    if not case_ids:
        return {"ok": False, "error": "先选择 case"}
    # 本次采集开始时先作废旧证据：采集失败后不允许 diagnose 读到上一轮的候选。
    try:
        DIAG.unlink()
    except OSError:
        pass
    play = _ensure_play()
    if not play.get("ok"):
        return {"ok": False, "error": play.get("error"), "editor": play.get("editor")}
    cfg = _write_probe_cfg()
    mem0 = _editor_mem()
    if (mem0.get("mb") or 0) > 5000:
        return {"ok": False,
                "error": (f"编辑器已占 {mem0.get('mb')}MB：先重启编辑器再采集，"
                          "避免再次触发 dwm/DSH 的 OOM"),
                "editor_memory": mem0}
    metas = [_manifest(c) for c in case_ids]
    windows: list[str] = []
    for m in metas:
        for w in _expects(m).get("windows", []):
            if w not in windows:
                windows.append(w)
    if not windows:
        return {"ok": False, "error": "所选 case 未声明采集窗口（见 manifest.expects）"}

    raw: list[dict] = []
    window_info: dict[str, dict] = {}
    asset: dict = {}

    if "hud" in windows:
        probe = _hud_ready()
        st = _parse_state(probe)
        if probe.get("result") == "no-game" or st.get("raw") == "no-game":
            return {"ok": False, "error": "游戏未启动：先执行 replay（start_game）"}
        if st.get("phase") != "Playing":
            return {"ok": False,
                    "error": f"游戏不在 Playing（当前 {st.get('phase')}）：先执行 replay",
                    "state": {k: st.get(k) for k in ("phase", "round")}}
        hud: list[dict] = []
        _touch_hud()
        hud += _capture_once("hud", CAPTURE_LIMIT)
        hud = _dedupe(hud)
        raw += hud
        window_info["hud"] = {"samples": 1, "candidates": len(hud),
                              "phase": st.get("phase"), "score": st.get("score")}

    if "board" in windows:
        # 板子取证器自己触发 resolve（外部触发会因 eval 延迟错过那一帧）；
        # 这里只负责在 resolve 之前把 profiler 打开。
        _force_profiler_off()
        on = _eval(PROFILER_ON_SHALLOW_CODE, timeout=30)
        if not on.get("ok"):
            raise RuntimeError("打开 profiler 失败：" +
                               json.dumps(on.get("errors"), ensure_ascii=False)[:200])
        # 深档开启会重启采样流；不等几帧，探针看到的历史只有 0~1 帧（实测）。
        time.sleep(0.5)
        board = _capture_board(CAPTURE_LIMIT)
        raw += board
        st = _parse_state(_hud_ready())
        window_info["board"] = {"candidates": len(board), "phase": st.get("phase")}

    if "shop" in windows:
        opened = _open_shop()
        s = _parse_state(opened)
        payload = PROJECT / "Assets/Resources/DevSim/payload_0.txt"
        asset = {"payload": "Assets/Resources/DevSim/payload_0.txt",
                 "bytes": (payload.stat().st_size if payload.exists() else None),
                 "phase": s.get("phase"), "shopOpen": s.get("shopOpen"),
                 "state": {k: s.get(k) for k in ("phase", "shopOpen", "coins", "round")}}
        window_info["shop"] = {"phase": s.get("phase"), "shopOpen": s.get("shopOpen")}

    all_cands = _dedupe(raw)
    mem1 = _editor_mem()
    mem = {"before_mb": mem0.get("mb"), "after_mb": mem1.get("mb"),
           "pid": mem1.get("ProcessId") or mem0.get("ProcessId")}
    if mem.get("before_mb") and mem.get("after_mb"):
        mem["delta_mb"] = mem["after_mb"] - mem["before_mb"]
    if (mem1.get("mb") or 0) > 6000:
        raise RuntimeError(
            f"取证后编辑器占用 {mem1['mb']}MB：已强制关闭 profiler，"
            "请先重启编辑器再做下一次取证")
    scoped_union: list[dict] = []
    per_case = []
    for cid, m in zip(case_ids, metas):
        ex = _expects(m)
        syms = ex.get("symbols", [])
        matched = [c for c in all_cands if _matches_symbols(c, syms)]
        item = {"case_id": cid, "windows": ex.get("windows", []),
                "expected_symbols": syms,
                "found": [_slim(c) for c in matched[:8]],
                "found_count": len(matched),
                "ok": bool(matched)}
        want = ex.get("assert") or {}
        if want:
            got = {k: asset.get(k) for k in want}
            item["ok"] = all(got.get(k) == v for k, v in want.items())
            item["assert"] = {"expected": want, "observed": got}
        elif not syms:
            item["ok"] = False
            item["note"] = "无符号期望，也无资产断言"
        per_case.append(item)
        for c in matched:
            if all(c.get("root_cause") != x.get("root_cause") for x in scoped_union):
                scoped_union.append(c)
    scoped_union = sorted(scoped_union, key=lambda c: c.get("score", 0), reverse=True)

    DIAG.parent.mkdir(parents=True, exist_ok=True)
    DIAG.write_text(json.dumps({
        "mode": "diagnostic", "cases": case_ids, "windows": windows,
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "editor_memory": mem,
        "per_case": per_case, "asset": asset, "window_info": window_info,
        "candidates": scoped_union,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    out = {"ok": True, "path": str(DIAG), "mode": "diagnostic", "cases": case_ids,
           "windows": windows, "per_case": per_case, "asset": asset,
           "editor_memory": mem,
           "candidates": [_slim(c) for c in scoped_union[:8]],
           "all_candidate_count": len(all_cands)}
    if mem.get("delta_mb") is not None and mem["delta_mb"] > 800:
        out["memory_warning"] = (f"本次采集编辑器内存 +{mem['delta_mb']}MB，"
                                 "超过护栏；请检查探针是否关闭了 profiler")
    return out


# ─────────────────────────── 组合自测覆盖检查 ───────────────────────────

def combo_coverage() -> dict:
    if not PREVIEW.exists():
        return {"ok": False, "error": "先组合预览"}
    if not COMPILE.exists():
        return {"ok": False, "error": "还没有本次编译记录：先“应用并编译”"}
    pv = json.loads(PREVIEW.read_text(encoding="utf-8"))
    touched = json.loads(TOUCHED.read_text(encoding="utf-8")) if TOUCHED.exists() else []
    compile_ = json.loads(COMPILE.read_text(encoding="utf-8"))
    compile_ok = (not compile_.get("failed")
                  and compile_.get("status") in {"completed", "up_to_date"})
    merged = (load_replay(REPLAY) if REPLAY.exists() else None)
    replay_cmds: dict[str, int] = {}
    if merged:
        for cid in pv.get("cases", []):
            try:
                spec = load_replay(ROOT / "sim" / "replay" / f"{_manifest(cid).get('replay')}.json")
            except Exception:
                spec = None
            replay_cmds[cid] = (len([x for x in spec.commands if x.type not in ("start_game", "stop")])
                                if spec else 0)
    per = []
    for cid in pv.get("cases", []):
        m = _manifest(cid)
        files = _case_files(m)
        applied = [f for f in files if f in touched]
        covered = compile_ok and bool(files) and len(applied) == len(files)
        per.append({"case_id": cid, "files": files, "applied_files": applied,
                    "files_covered": bool(files) and len(applied) == len(files),
                    "replay_commands": replay_cmds.get(cid, 0),
                    "replay_covered": replay_cmds.get(cid, 0) > 0,
                    "expects_windows": _expects(m).get("windows", []),
                    "expects_symbols": _expects(m).get("symbols", []),
                    "covered": covered and replay_cmds.get(cid, 0) > 0})
    return {"ok": True, "mode": "combo", "cases": pv.get("cases", []),
            "files": pv.get("files", []), "replay_id": pv.get("replay_id", ""),
            "replay_commands": (len(merged.commands) if merged else 0),
            "compile_ok": compile_ok,
            "compile_failed": bool(compile_.get("failed")),
            "compile_status": compile_.get("status"),
            "per_case": per,
            "all_covered": compile_ok and bool(per) and all(p["covered"] for p in per),
            "note": ("组合自测只断言叠加/编译/replay/覆盖；不产候选排名，"
                     "也不代表多个病灶能同时进 top8。")}


# ─────────────────────────── Agent / 修复 ───────────────────────────

def _diag_payload() -> tuple[dict, dict | None]:
    if not DIAG.exists():
        return {}, {"ok": False, "error": "还没有定向采集结果（先选 case → 采集）"}
    data = json.loads(DIAG.read_text(encoding="utf-8"))
    if data.get("mode") != "diagnostic":
        return data, {"ok": False, "error": "组合自测不做 Agent 归因；请切到真实诊断"}
    if not data.get("candidates"):
        return data, {"ok": False,
                      "error": "当前目标没有候选；资产类 case 只做人工检测/修复"}
    return data, None


def run_agent_diagnosis() -> dict:
    """真实 LLM 归因：取本次定向采集的目标候选前 2 个。"""
    from upi.agents.factor_judge import build_hypotheses_for_issue
    from upi.config.settings import load_config

    data, err = _diag_payload()
    if err:
        return err
    cands = []
    for c in data["candidates"][:2]:
        anchors = c.get("code_anchors") or []
        cands.append((c.get("root_cause", ""), anchors))
    issue = Issue(
        signature=IssueSignature(
            dim="cpu", metric="hot_path_score",
            mechanism="hot_path_repeated_work", threshold=0.0),
        link="cpu",
        evidence=[Evidence(tool="evidence_candidates", raw_ref=str(DIAG),
                           value=float(data["candidates"][0].get("score", 0)),
                           unit="score", data_tier="A2")],
        magnitude=float(data["candidates"][0].get("score", 0)),
        deviation_ratio=1.0,
    )
    cfg = load_config(ROOT / "llm_config.yaml")
    hs, archive = build_hypotheses_for_issue(cfg, issue, cands)
    out = LAB / "diagnose.json"
    out.write_text(json.dumps({"mode": "diagnostic", "cases": data.get("cases", []),
                               "hypotheses": [h.model_dump() for h in hs],
                               "archive": archive, "source": str(DIAG)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    used = sum(int((v.get("_meta") or {}).get("token_in", 0) or 0)
               + int((v.get("_meta") or {}).get("token_out", 0) or 0)
               for v in archive.values() if isinstance(v, dict))
    return {"ok": True, "path": str(out), "source": str(DIAG),
            "cases": data.get("cases", []),
            "hypotheses": [{"root_cause": h.root_cause, "confidence": h.confidence,
                            "code_anchors": h.code_anchors} for h in hs],
            "tokens": {k: v.get("_meta", {}) for k, v in archive.items()},
            "token_total": used}


def run_repair_plans() -> dict:
    from upi.core.candidates_v2 import CandidateV2
    from upi.core.repair_recipe import plan_for_candidate

    data, err = _diag_payload()
    if err:
        return err
    out = []
    for c in data["candidates"][:3]:
        cand = CandidateV2(**c)
        plan = plan_for_candidate(cand, PROJECT)
        out.append({"root_cause": cand.root_cause,
                    "playbook": (plan.playbook_id if plan else ""),
                    "files": (plan.touched_assets if plan else []),
                    "diffs": (len(plan.diffs) if plan else 0),
                    "tier": (plan.permission_tier if plan else "no_recipe")})
    return {"ok": True, "source": str(DIAG), "plans": out}


def run_repair() -> dict:
    from upi.agents.fixer import apply_diffs
    from upi.core.candidates_v2 import CandidateV2
    from upi.core.repair_recipe import plan_for_candidate

    data, err = _diag_payload()
    if err:
        return err
    applied = []
    for c in data["candidates"][:3]:
        cand = CandidateV2(**c)
        plan = plan_for_candidate(cand, PROJECT)
        if plan is None:
            continue
        apply_diffs(plan, root=PROJECT)
        applied.append({"root_cause": cand.root_cause, "playbook": plan.playbook_id,
                        "diffs": len(plan.diffs)})
    if not applied:
        return {"ok": False, "error": "没有可执行的 recipe（可能 base 已是优化态）"}
    st = _compile_and_wait()
    if st.get("failed"):
        restore_base()
        return {"ok": False, "error": "修复编译失败，已恢复 base", "compile": st}
    return {"ok": True, "applied": applied, "compile": st}


def latest_candidates() -> dict:
    if DIAG.exists():
        d = json.loads(DIAG.read_text(encoding="utf-8"))
        return {"ok": True, "path": str(DIAG), "mode": d.get("mode"),
                "cases": d.get("cases", []), "per_case": d.get("per_case", []),
                "count": len(d.get("candidates", []))}
    return {"ok": False, "error": "还没有定向采集结果"}


# ─────────────────────────── UI ───────────────────────────

HTML = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>I8 Lab</title><style>
body{font:14px system-ui;background:#0f141b;color:#dbe7f3;max-width:1040px;margin:28px auto;padding:0 18px}
h1{font-size:22px;margin:0 0 6px} .card{background:#17212c;border:1px solid #2a3a4a;border-radius:10px;padding:14px;margin:12px 0}
label{display:block;margin:7px 0} button{background:#1e4e3a;color:#dbe7f3;border:1px solid #2e7a58;border-radius:7px;padding:8px 14px;cursor:pointer;margin:4px 6px 4px 0}
button.tab{background:#1a2530;border-color:#2a3a4a} button.tab.on{background:#2b5d8a;border-color:#3f7fbb}
pre{white-space:pre-wrap;background:#0b1117;padding:10px;border-radius:7px;max-height:460px;overflow:auto}
.small{color:#8da2b5} .ok{color:#5ee0a0} .bad{color:#ff8b8b} .hint{background:#131c25;border-left:3px solid #2e7a58;padding:8px 10px;margin:8px 0}
</style></head><body>
<h1>I8 Lab <span class="small">真实诊断 / 组合自测</span></h1>
<div class="card"><b>模式</b><br>
<button class="tab on" id="tabDiag" onclick="setMode('diagnostic')">A. 真实诊断（单 case）</button>
<button class="tab" id="tabCombo" onclick="setMode('combo')">B. 组合自测（覆盖检查）</button>
<div class="hint small" id="modeHint"></div>
</div>
<div class="card"><b>选择 case</b><div id="cases"></div>
<div class="small" id="selInfo"></div></div>

<div class="card"><b>取证参数</b> <span class="small">（下一次采集生效）</span><br>
取证线程 <input id="cfgThreads" type="number" min="1" max="64" style="width:70px">
取证帧数 <input id="cfgFrames" type="number" min="1" max="32" style="width:70px">
预算ms <input id="cfgBudget" type="number" min="500" max="20000" style="width:90px">
板子帧数 <input id="cfgBoardFrames" type="number" min="1" max="8" style="width:70px">
<button onclick="saveCfg()">保存参数</button> <span id="cfgMsg" class="small"></span></div>

<div class="card" id="diagPanel"><b>真实诊断流程</b>
<div class="hint small">单 case 单独跑、单独看候选。c01 用 Board 窗口；c02/c03 用 HUD 窗口；c04 只做资产检测。
顺序：组合 → 检查 Unity → 应用 → 执行 replay → 定向采集 → Agent → 修复计划 → 执行修复 → 恢复。</div>
<button onclick="runPreview()">组合到临时工程</button>
<button onclick="checkEditor()">检查 Unity</button>
<button onclick="applyBase()">应用到 base</button>
<button onclick="runReplay()">执行 replay</button>
<button onclick="capture()">定向采集</button>
<button onclick="diagnose()">Agent 诊断</button>
<button onclick="repairPlans()">查看修复计划</button>
<button onclick="repair()">执行修复</button>
</div>

<div class="card" id="comboPanel" style="display:none"><b>组合自测流程</b>
<div class="hint small">只验证 overlay/patch 叠加、编译、合并 replay、每个 case 的覆盖存在性。
不做候选排名，也不代表多个病能同时进 top8。</div>
<button onclick="runPreview()">组合到临时工程</button>
<button onclick="checkEditor()">检查 Unity</button>
<button onclick="applyBase()">应用并编译</button>
<button onclick="runReplay()">执行合并 replay</button>
<button onclick="coverage()">覆盖检查</button>
</div>

<div class="card"><button onclick="restore()">恢复 base</button>
<button onclick="loadCandidates()">查看最近采集</button>
<button onclick="loadPreviewInfo()">查看当前组合</button>
<span id="msg" class="small"></span></div>
<div class="card"><b>输出</b><pre id="out">未运行</pre></div>
<script>
let MODE='diagnostic';
const HINT={diagnostic:'真实诊断：一次只针对一个目标（c01 或 c02+c03）。采集只保留该目标窗口与符号。',
            combo:'组合自测：可以多选，但只断言覆盖/编译/replay，不产候选排名。'};
function setMode(m){MODE=m;
  document.getElementById('tabDiag').className='tab'+(m==='diagnostic'?' on':'');
  document.getElementById('tabCombo').className='tab'+(m==='combo'?' on':'');
  document.getElementById('diagPanel').style.display=(m==='diagnostic'?'block':'none');
  document.getElementById('comboPanel').style.display=(m==='combo'?'block':'none');
  document.getElementById('modeHint').textContent=HINT[m];
}
async function api(path, body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});return await r.json();}
async function init(){
  const d=await api('/api/cases');
  document.getElementById('cases').innerHTML=d.cases.map(c=>`<label><input type="checkbox" value="${c.case_id}"> <b>${c.case_id}</b> — ${c.notes||''} <span class="small">窗口:${(c.windows||[]).join(',')||'-'} 期望:${(c.symbols||[]).join(',')||'-'}</span></label>`).join('');
  setMode('diagnostic');
  loadCfg();
}
async function loadCfg(){const d=await api('/api/config');if(!d.ok)return;
  const c=d.config||{};
  document.getElementById('cfgThreads').value=c.evidence_threads;
  document.getElementById('cfgFrames').value=c.evidence_frames;
  document.getElementById('cfgBudget').value=c.evidence_budget_ms;
  document.getElementById('cfgBoardFrames').value=c.board_frames;}
async function saveCfg(){
  const body={evidence_threads:+document.getElementById('cfgThreads').value,
              evidence_frames:+document.getElementById('cfgFrames').value,
              evidence_budget_ms:+document.getElementById('cfgBudget').value,
              board_frames:+document.getElementById('cfgBoardFrames').value};
  const d=await api('/api/config',body);
  document.getElementById('cfgMsg').textContent=d.ok?('已保存：线程 '+d.config.evidence_threads
      +' / 帧 '+d.config.evidence_frames+' / 预算 '+d.config.evidence_budget_ms+'ms'):('失败：'+d.error);}
function selected(){return [...document.querySelectorAll('input[type=checkbox]:checked')].map(x=>x.value);}
function show(r){const ok=r&&r.ok;document.getElementById('msg').innerHTML=ok?'<span class="ok">完成</span>':'<span class="bad">失败</span>';
  document.getElementById('out').textContent=JSON.stringify(r,null,2);}
function busy(t){document.getElementById('msg').textContent=t;}
async function runPreview(){busy('组合中...');show(await api('/api/preview',{mode:MODE,cases:selected()}));}
async function checkEditor(){show(await api('/api/editor'));}
async function applyBase(){if(!confirm('会临时修改 sim/project，失败自动恢复。继续？'))return;busy('应用中...');show(await api('/api/apply',{mode:MODE}));}
async function runReplay(){busy('执行 replay...');show(await api('/api/replay',{mode:MODE}));}
async function capture(){busy('定向采集中...');show(await api('/api/capture',{mode:MODE,cases:selected()}));}
async function diagnose(){busy('Agent 诊断中...');show(await api('/api/diagnose',{mode:MODE}));}
async function repairPlans(){show(await api('/api/repair_plans',{mode:MODE}));}
async function repair(){if(!confirm('会修改当前项目并编译，失败自动恢复。继续？'))return;busy('修复中...');show(await api('/api/repair',{mode:MODE}));}
async function coverage(){show(await api('/api/coverage',{mode:MODE}));}
async function restore(){show(await api('/api/restore',{mode:MODE}));}
async function loadCandidates(){show(await api('/api/candidates'));}
async function loadPreviewInfo(){show(await api('/api/preview_info'));}
init();</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/":
            b = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if p == "/api/cases":
            return self._send(200, {"ok": True, "cases": cases()})
        if p == "/api/candidates":
            return self._send(200, latest_candidates())
        if p == "/api/editor":
            return self._send(200, editor_status())
        if p == "/api/preview_info":
            if not PREVIEW.exists():
                return self._send(200, {"ok": False, "error": "还没有组合"})
            return self._send(200, json.loads(PREVIEW.read_text(encoding="utf-8")))
        if p == "/api/config":
            return self._send(200, {"ok": True, "config": load_capture_cfg(),
                                    "defaults": CFG_DEFAULTS, "limits": CFG_LIMITS})
        self._send(404, {"ok": False})

    def do_POST(self):
        p = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        mode = body.get("mode") or "diagnostic"
        if mode not in MODES:
            return self._send(400, {"ok": False, "error": f"未知模式：{mode}"})
        diag_only = {"/api/capture", "/api/diagnose",
                     "/api/repair_plans", "/api/repair"}
        if mode == "combo" and p in diag_only:
            return self._send(409, {"ok": False,
                                    "error": "组合自测不做候选/Agent/修复；请切到真实诊断"})
        try:
            if p == "/api/preview":
                r = preview(body.get("cases") or [])
                PREVIEW.write_text(json.dumps(r, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
                return self._send(200, r)
            if p == "/api/apply":
                return self._send(200, apply_to_base())
            if p == "/api/replay":
                return self._send(200, run_lab_replay())
            if p == "/api/capture":
                return self._send(200, capture_diagnostic(body.get("cases") or []))
            if p == "/api/diagnose":
                return self._send(200, run_agent_diagnosis())
            if p == "/api/repair_plans":
                return self._send(200, run_repair_plans())
            if p == "/api/repair":
                return self._send(200, run_repair())
            if p == "/api/coverage":
                return self._send(200, combo_coverage())
            if p == "/api/config":
                try:
                    return self._send(200, {"ok": True,
                                            "config": save_capture_cfg(body)})
                except ValueError as e:
                    return self._send(400, {"ok": False, "error": str(e)})
            if p == "/api/restore":
                return self._send(200, restore_base())
        except Exception as e:  # noqa: BLE001
            return self._send(409, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        self._send(404, {"ok": False})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    LAB.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"I8 Lab: http://127.0.0.1:{a.port}")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
