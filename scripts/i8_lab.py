"""I8 Lab：本地病例组合体验页（安全模式：不改 base、不启动 Unity）。

用法：
    python scripts/i8_lab.py --port 8787
    open http://127.0.0.1:8787
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


def cases():
    out = []
    for d in sorted(CASES_DIR.glob("c*")):
        m = d / "manifest.json"
        if not m.exists():
            continue
        data = json.loads(m.read_text(encoding="utf-8"))
        out.append({"case_id": data.get("case_id", d.name),
                    "overlay": data.get("overlay", []),
                    "patches": data.get("patches", []),
                    "replay": data.get("replay", ""),
                    "oracle": data.get("oracle", ""),
                    "notes": data.get("notes", "")})
    return out


def preview(case_ids: list[str]) -> dict:
    dirs = [CASES_DIR / c for c in case_ids]
    for d in dirs:
        if not (d / "manifest.json").exists():
            raise ValueError(f"case 不存在：{d.name}")
    out = LAB / "project"
    run = apply_cases(ROOT / "sim" / "project", dirs, out)
    specs = []
    for d in dirs:
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        if m.get("replay"):
            specs.append(load_replay(ROOT / "sim" / "replay" / f"{m['replay']}.json"))
    merged = merge_replays(specs) if specs else None
    if merged:
        (LAB / "replay.json").write_text(merged.model_dump_json(indent=2), encoding="utf-8")
    return {"ok": True, "cases": run.case_ids, "files": run.files,
            "workdir": str(out), "replay": (merged.model_dump() if merged else None)}


def _latest_candidate_file() -> Path | None:
    lab = ROOT / "runs" / "lab" / "evidence" / "candidates.json"
    if lab.exists():
        return lab
    files = sorted((ROOT / "runs").glob("*/evidence/candidates.json"),
                   key=lambda p: p.stat().st_mtime)
    files = [p for p in files if "repaired" not in str(p)]
    return files[-1] if files else None


def run_agent_diagnosis() -> dict:
    """真实 LLM 归因：取最近候选前 2 个，调用 factor_judge。"""
    from upi.agents.factor_judge import build_hypotheses_for_issue
    from upi.config.settings import load_config

    p = _latest_candidate_file()
    if p is None:
        return {"ok": False, "error": "没有 candidates.json"}
    data = json.loads(p.read_text(encoding="utf-8"))
    cands = []
    for c in data.get("candidates", [])[:2]:
        anchors = c.get("code_anchors") or []
        cands.append((c.get("root_cause", ""), anchors))
    if not cands:
        return {"ok": False, "error": "候选为空"}
    issue = Issue(
        signature=IssueSignature(
            dim="cpu", metric="hot_path_score",
            mechanism="hot_path_repeated_work", threshold=0.0),
        link="cpu",
        evidence=[Evidence(tool="evidence_candidates", raw_ref=str(p),
                           value=float(cands and data["candidates"][0].get("score", 0)),
                           unit="score", data_tier="A2")],
        magnitude=float(data["candidates"][0].get("score", 0)),
        deviation_ratio=1.0,
    )
    cfg = load_config(ROOT / "llm_config.yaml")
    hs, archive = build_hypotheses_for_issue(cfg, issue, cands)
    out = LAB / "diagnose.json"
    out.write_text(json.dumps({"hypotheses": [h.model_dump() for h in hs],
                               "archive": archive, "source": str(p)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(out), "source": str(p),
            "hypotheses": [{"root_cause": h.root_cause, "confidence": h.confidence,
                            "code_anchors": h.code_anchors} for h in hs],
            "tokens": {k: v.get("_meta", {}) for k, v in archive.items()}}


def run_repair_plans() -> dict:
    from upi.core.candidates_v2 import CandidateV2
    from upi.core.repair_recipe import plan_for_candidate

    p = _latest_candidate_file()
    if p is None:
        return {"ok": False, "error": "没有 candidates.json"}
    data = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for c in data.get("candidates", [])[:3]:
        cand = CandidateV2(**c)
        plan = plan_for_candidate(cand, PROJECT)
        out.append({
            "root_cause": cand.root_cause,
            "playbook": (plan.playbook_id if plan else ""),
            "files": (plan.touched_assets if plan else []),
            "diffs": (len(plan.diffs) if plan else 0),
            "tier": (plan.permission_tier if plan else "no_recipe"),
        })
    return {"ok": True, "source": str(p), "plans": out}


def run_repair() -> dict:
    from upi.agents.fixer import apply_diffs
    from upi.core.candidates_v2 import CandidateV2
    from upi.core.repair_recipe import plan_for_candidate

    p = _latest_candidate_file()
    if p is None:
        return {"ok": False, "error": "没有 candidates.json"}
    data = json.loads(p.read_text(encoding="utf-8"))
    applied = []
    for c in data.get("candidates", [])[:3]:
        cand = CandidateV2(**c)
        plan = plan_for_candidate(cand, PROJECT)
        if plan is None:
            continue
        apply_diffs(plan, root=PROJECT)
        applied.append({"root_cause": cand.root_cause, "playbook": plan.playbook_id,
                        "diffs": len(plan.diffs)})
    if not applied:
        return {"ok": False, "error": "没有可执行的 recipe（可能 base 已是优化态）"}
    d = unity("recompile")
    deadline = time.time() + 20
    st = {}
    while time.time() < deadline:
        st = ((unity("recompile_status").get("data") or {}).get("result") or {})
        if st.get("status") in {"completed", "up_to_date", "failed"}:
            break
        time.sleep(1)
    if st.get("failed"):
        restore_base()
        return {"ok": False, "error": "修复编译失败，已恢复 base", "compile": st}
    return {"ok": True, "applied": applied, "compile": st}


def latest_candidates() -> dict:
    files = sorted((ROOT / "runs").glob("*/evidence/candidates.json"),
                   key=lambda p: p.stat().st_mtime)
    if not files:
        return {"ok": False, "error": "还没有 candidates.json"}
    p = files[-1]
    return {"ok": True, "path": str(p), "data": json.loads(p.read_text(encoding="utf-8"))}


def unity(*args, timeout=120) -> dict:
    p = subprocess.run(["unity", "command", *args, "--project-path", str(PROJECT),
                        "--format", "json"], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"success": False, "raw": p.stdout[-500:]}


def editor_status() -> dict:
    d = unity("editor", "status")
    if not d.get("success"):
        return {"ok": False, "error": "Unity pipeline 不可达；不自动开新窗口",
                "detail": d.get("errors") or d.get("raw")}
    s = ((d.get("data") or {}).get("result") or {})
    return {"ok": True, "status": s.get("status"), "playMode": s.get("playMode")}


def _git_clean() -> bool:
    p = subprocess.run(["git", "status", "--porcelain", "--", "sim/project"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return not (p.stdout or "").strip()


def apply_to_base() -> dict:
    f = LAB / "preview.json"
    if not f.exists():
        return {"ok": False, "error": "先组合预览"}
    if not _git_clean():
        return {"ok": False, "error": "sim/project 有未提交改动，拒绝应用"}
    pv = json.loads(f.read_text(encoding="utf-8"))
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
    (LAB / "touched.json").write_text(json.dumps(touched, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    d = unity("recompile")
    deadline = time.time() + 20
    st = {}
    while time.time() < deadline:
        st = ((unity("recompile_status").get("data") or {}).get("result") or {})
        if st.get("status") in {"completed", "up_to_date", "failed"}:
            break
        time.sleep(1)
    if st.get("failed"):
        restore_base()
        return {"ok": False, "error": "编译失败，已自动恢复 base", "compile": st}
    return {"ok": True, "touched": touched, "compile": st}


def restore_base() -> dict:
    f = LAB / "touched.json"
    touched = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    if touched:
        subprocess.run(["git", "checkout", "--", *[f"sim/project/{r}" for r in touched]],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    unity("editor.playmode stop")
    return {"ok": True, "restored": touched, "clean": _git_clean()}


def run_lab_replay() -> dict:
    f = LAB / "replay.json"
    if not f.exists():
        return {"ok": False, "error": "先组合预览"}
    es = editor_status()
    if not es.get("ok") or es.get("playMode") != "playing":
        return {"ok": False, "error": "Unity 需要在 Play Mode", "editor": es}
    spec = load_replay(f)
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
            st = {k: st.get(k) for k in ("phase", "shopOpen", "totalCleared", "accepted") if k in st}
        slim.append({**s, "state": st})
    return {"ok": True, "replay_id": spec.replay_id, "steps": slim}


def capture_candidates() -> dict:
    es = editor_status()
    if not es.get("ok") or es.get("playMode") != "playing":
        return {"ok": False, "error": "Unity 需要在 Play Mode", "editor": es}
    ev = ROOT / "runs" / "_probe" / "evidence_v1.json"
    try:
        ev.unlink()
    except OSError:
        pass
    subprocess.run(["unity", "command", "script eval", "--project-path", str(PROJECT),
                    "--file", str(ROOT / "probe" / "upi_evidence_v1.cs"), "--format", "json"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=60)
    deadline = time.time() + 10
    while time.time() < deadline and not ev.exists():
        time.sleep(0.2)
    if not ev.exists():
        return {"ok": False, "error": "collector 没有写出证据"}
    run_id = "lab"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "collect_evidence.py"),
                    "--project", str(PROJECT), "--evidence", str(ev),
                    "--run-id", run_id, "--scene", "Assets/Scenes/00_Bootstrap.unity"],
                   cwd=str(ROOT), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60)
    bundle = ROOT / "runs" / run_id / "evidence" / "evidence_v2.json"
    out = ROOT / "runs" / run_id / "evidence" / "candidates.json"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "evidence_candidates.py"),
                    "--project", str(PROJECT), "--evidence", str(bundle),
                    "--out", str(out), "--limit", "8"],
                   cwd=str(ROOT), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60)
    if not out.exists():
        return {"ok": False, "error": "候选生成失败"}
    data = json.loads(out.read_text(encoding="utf-8"))
    return {"ok": True, "path": str(out),
            "candidates": [{"kind": c.get("kind"), "root_cause": c.get("root_cause"),
                            "score": c.get("score"), "evidence_n": c.get("evidence_n")}
                           for c in data.get("candidates", [])[:8]]}


HTML = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>I8 Lab</title><style>
body{font:14px system-ui;background:#0f141b;color:#dbe7f3;max-width:1000px;margin:30px auto;padding:0 18px}
h1{font-size:22px} .card{background:#17212c;border:1px solid #2a3a4a;border-radius:10px;padding:14px;margin:12px 0}
label{display:block;margin:7px 0} button{background:#1e4e3a;color:#dbe7f3;border:1px solid #2e7a58;border-radius:7px;padding:8px 14px;cursor:pointer;margin-right:8px}
pre{white-space:pre-wrap;background:#0b1117;padding:10px;border-radius:7px;max-height:420px;overflow:auto}
.small{color:#8da2b5}
</style></head><body>
<h1>I8 Lab <span class="small">病例组合 / replay / 候选查看（安全模式）</span></h1>
<div class="card"><b>病例</b><div id="cases"></div>
<button onclick="runPreview()">组合到临时工程</button>
<button onclick="checkEditor()">检查 Unity</button>
<button onclick="applyBase()">应用到 base</button>
<button onclick="runReplay()">执行 replay</button>
<button onclick="capture()">采集候选</button>
<button onclick="diagnose()">Agent 诊断</button>
<button onclick="repairPlans()">查看修复计划</button>
<button onclick="repair()">执行修复</button>
<button onclick="restore()">恢复 base</button>
<button onclick="loadCandidates()">查看最近候选</button>
<button onclick="loadPreviewInfo()">查看当前组合</button>
<span id="msg" class="small"></span></div>
<div class="card"><b>组合结果</b><pre id="out">未运行</pre></div>
<script>
async function api(path, body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});return await r.json();}
async function init(){const d=await api('/api/cases');document.getElementById('cases').innerHTML=d.cases.map(c=>`<label><input type="checkbox" value="${c.case_id}" checked> <b>${c.case_id}</b> — ${c.notes||''} <span class="small">${c.replay}</span></label>`).join('');}
function selected(){return [...document.querySelectorAll('input[type=checkbox]:checked')].map(x=>x.value);}
function show(r){document.getElementById('msg').textContent=r.ok?'完成':'失败';document.getElementById('out').textContent=JSON.stringify(r,null,2);}
async function runPreview(){document.getElementById('msg').textContent='组合中...';show(await api('/api/preview',{cases:selected()}));}
async function checkEditor(){show(await api('/api/editor'));}
async function applyBase(){if(!confirm('会临时修改 sim/project，失败自动恢复。继续？'))return;document.getElementById('msg').textContent='应用中...';show(await api('/api/apply',{}));}
async function runReplay(){document.getElementById('msg').textContent='执行 replay...';show(await api('/api/replay',{}));}
async function capture(){document.getElementById('msg').textContent='采集中...';show(await api('/api/capture',{}));}
async function diagnose(){document.getElementById('msg').textContent='Agent 诊断中...';show(await api('/api/diagnose',{}));}
async function repairPlans(){show(await api('/api/repair_plans',{}));}
async function repair(){if(!confirm('会修改当前项目并编译，失败自动恢复。继续？'))return;document.getElementById('msg').textContent='修复中...';show(await api('/api/repair',{}));}
async function restore(){show(await api('/api/restore',{}));}
async function loadPreviewInfo(){show(await api('/api/preview_info'));}
async function loadCandidates(){show(await api('/api/candidates'));}
init();
</script></body></html>"""


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
            f = LAB / "preview.json"
            if not f.exists():
                return self._send(200, {"ok": False, "error": "还没有组合"})
            return self._send(200, json.loads(f.read_text(encoding="utf-8")))
        self._send(404, {"ok": False})

    def do_POST(self):
        p = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        if p == "/api/preview":
            try:
                r = preview(body.get("cases") or [])
                (LAB / "preview.json").write_text(json.dumps(r, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
                self._send(200, r)
            except Exception as e:  # noqa: BLE001
                self._send(409, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if p == "/api/apply":
            try:
                self._send(200, apply_to_base())
            except Exception as e:  # noqa: BLE001
                self._send(409, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if p == "/api/replay":
            try:
                self._send(200, run_lab_replay())
            except Exception as e:  # noqa: BLE001
                self._send(409, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if p == "/api/capture":
            try:
                self._send(200, capture_candidates())
            except Exception as e:  # noqa: BLE001
                self._send(409, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if p == "/api/diagnose":
            try:
                self._send(200, run_agent_diagnosis())
            except Exception as e:  # noqa: BLE001
                self._send(409, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if p == "/api/repair_plans":
            try:
                self._send(200, run_repair_plans())
            except Exception as e:  # noqa: BLE001
                self._send(409, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if p == "/api/repair":
            try:
                self._send(200, run_repair())
            except Exception as e:  # noqa: BLE001
                self._send(409, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if p == "/api/restore":
            try:
                self._send(200, restore_base())
            except Exception as e:  # noqa: BLE001
                self._send(409, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
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
