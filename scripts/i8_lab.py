"""I8 考卷台：用 组合病例 + oracle + 干净 base 生成测试工程，并按能力口径判分。

定位（2026-11-13 修正）：
- 只负责"出考卷"和"看得分"，不做诊断、采集、修复；
- 用户用 `run.bat` 启动产品控制台，自己拿 `test_project` 去跑工具；
- **判分不匹配正则、不匹配 case 编号**：只读工具运行产出的能力结果
  （findings / repairs / verdict），与 oracle 的"机制 + 位置 + 效果"结构化比对。
  没有产物 = 未评估，不拿静态扫描凑分。

oracle 只提供结果性期望（哪条机制、哪个文件/方法、修完应该怎样），不进工程。

用法：
    python scripts/i8_lab.py --port 8787
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.case_overlay import apply_cases, tree_hash  # noqa: E402
from upi.core.replay_spec import load_replay, merge_replays  # noqa: E402

CASES_DIR = ROOT / "sim" / "cases"
ORACLE_DIR = ROOT / "sim" / "oracle" / "cases"
BASE = ROOT / "sim" / "project"
TEST_PROJECT = ROOT / "test_project"
TEST_PROJECT_ID = "test_project"
LAB = ROOT / "runs" / "i8_lab"
PAPER = LAB / "paper.json"


def _case_ids() -> list[str]:
    return sorted(d.name for d in CASES_DIR.glob("c*") if (d / "manifest.json").is_file())


def cases() -> list[dict]:
    out = []
    for cid in _case_ids():
        m = json.loads((CASES_DIR / cid / "manifest.json").read_text(encoding="utf-8"))
        ex = m.get("expects") or {}
        out.append({"case_id": cid, "notes": m.get("notes", ""),
                    "windows": ex.get("windows", []),
                    "symbols": ex.get("symbols", [])})
    return out


def _capability_expectations(case_ids: list[str]) -> list[dict]:
    """oracle + case 自带 capability → 结果性期望（机制 + 锚点 + 可接受方法）。不含正则。"""
    expected: list[dict] = []
    for p in sorted(ORACLE_DIR.glob("c*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        atoms = set(d.get("atomic_cases") or [])
        if not atoms or not atoms <= set(case_ids):
            continue
        for e in d.get("expected_opportunities", []):
            expected.append({"mechanism": e.get("pattern_id"),
                             "anchor": e.get("code_anchor", ""),
                             "metric": e.get("metric", ""),
                             "accept_methods": e.get("accept_methods") or []})
    for cid in case_ids:
        m = json.loads((CASES_DIR / cid / "manifest.json").read_text(encoding="utf-8"))
        cap = m.get("capability")
        if cap:
            expected.append({"mechanism": cap.get("mechanism", ""),
                             "anchor": cap.get("anchor", ""),
                             "metric": cap.get("metric", ""),
                             "accept_methods": cap.get("accept_methods") or []})
    seen, uniq = set(), []
    for e in expected:
        k = (e["mechanism"], e["anchor"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq


def generate(case_ids: list[str]) -> dict:
    dirs = [CASES_DIR / c for c in case_ids]
    for d in dirs:
        if not (d / "manifest.json").is_file():
            raise ValueError(f"case 不存在：{d.name}")
    target = TEST_PROJECT.resolve()
    if target != (ROOT / "test_project").resolve():
        raise RuntimeError(f"拒绝清理非考卷路径：{target}")
    if target.exists():
        # 工程被 Unity 打开时删除会撞文件锁：先明确要求关闭，不硬删
        try:
            import subprocess as _sp
            p = _sp.run(["unity", "command", "editor status",
                         "--project-path", str(target), "--format", "json"],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=20)
            if '"success": true' in (p.stdout or "").replace("\n", ""):
                raise RuntimeError(
                    "test_project 正在 Unity 中打开：请先关闭该编辑器，再重新生成考卷")
        except RuntimeError:
            raise
        except Exception:  # noqa: BLE001 — 探测失败按“未打开”处理
            pass
        shutil.rmtree(target)
    run = apply_cases(BASE, dirs, target)
    # 合并 replay：考卷的“考试负载”由所选病例的 replay 合并而来（start/stop 各一次）
    specs = []
    for d in dirs:
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        if m.get("replay"):
            specs.append(load_replay(ROOT / "sim" / "replay" / f"{m['replay']}.json"))
    replay_id, replay_path = "", ""
    if specs:
        merged = merge_replays(specs)
        LAB.mkdir(parents=True, exist_ok=True)
        replay_path = str(LAB / "paper_replay.json")
        Path(replay_path).write_text(merged.model_dump_json(indent=2), encoding="utf-8")
        replay_id = merged.replay_id
    paper = {"cases": run.case_ids, "files": run.files,
             "base_hash": tree_hash(BASE),
             "expectations": _capability_expectations(case_ids),
             "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "project": str(target), "project_id": TEST_PROJECT_ID,
             "replay_id": replay_id, "replay_path": replay_path}
    LAB.mkdir(parents=True, exist_ok=True)
    PAPER.write_text(json.dumps(paper, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "paper": paper}


def _latest_findings(created_at: str) -> tuple[dict | None, str]:
    """读工具在 test_project 上的运行产物；没有就明确"未评估"。"""
    try:
        from upi.core.findings import load_runs
        runs = load_runs(TEST_PROJECT_ID)
    except Exception as e:  # noqa: BLE001
        return None, f"findings 契约不可用：{type(e).__name__}: {e}"
    if not runs:
        return None, "未运行工具（没有 findings.json）"
    after = [r for r in runs
             if str(r.created_at).replace("T", " ") >= str(created_at).replace("T", " ")]
    if not after:
        return None, "未运行工具（本考卷生成之后没有运行记录）"
    return after[-1], ""


def score() -> dict:
    if not PAPER.exists():
        return {"ok": False, "error": "还没有考卷，先生成"}
    paper = json.loads(PAPER.read_text(encoding="utf-8"))
    if not TEST_PROJECT.is_dir():
        return {"ok": False, "error": "test_project 不存在（可能被删了），重新生成考卷"}
    run, why = _latest_findings(paper.get("created_at", ""))
    if run is None:
        return {"ok": True, "evaluated": False, "reason": why,
                "paper": {"cases": paper.get("cases"),
                          "created_at": paper.get("created_at")},
                "dimensions": {"discovery": None, "no_false_positive": None,
                               "repair": None, "reproducible": None}}
    from upi.core.findings import score_run
    return {"ok": True, "evaluated": True,
            "paper": {"cases": paper.get("cases"),
                      "created_at": paper.get("created_at")},
            **score_run(paper, run)}


HTML = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>I8 考卷台</title><style>
body{font:14px system-ui;background:#0f141b;color:#dbe7f3;max-width:980px;margin:28px auto;padding:0 18px}
h1{font-size:22px;margin:0 0 6px}.card{background:#17212c;border:1px solid #2a3a4a;border-radius:10px;padding:14px;margin:12px 0}
label{display:block;margin:7px 0}button{background:#1e4e3a;color:#dbe7f3;border:1px solid #2e7a58;border-radius:7px;padding:8px 14px;cursor:pointer;margin:4px 6px 4px 0}
pre{white-space:pre-wrap;background:#0b1117;padding:10px;border-radius:7px;max-height:460px;overflow:auto}
.small{color:#8da2b5}.ok{color:#5ee0a0}.bad{color:#ff8b8b}.hint{background:#131c25;border-left:3px solid #2e7a58;padding:8px 10px;margin:8px 0}
.grp{margin:10px 0}.bar{display:flex;align-items:center;gap:8px;margin:3px 0}
.bar span{width:64px;color:#8da2b5}.bar b{width:32px;text-align:right}
.track{flex:1;background:#0b1117;border-radius:6px;height:12px;overflow:hidden}
.track i{display:block;height:100%}.okbar{background:#2e7a58}.badbar{background:#a33}.warnbar{background:#b8862e}
</style></head><body>
<h1>I8 考卷台 <span class="small">出考卷 + 按能力看得分</span></h1>
<div class="card"><b>怎么考试</b>
<div class="hint small">1) 勾选病例 → 生成考卷（写到仓库根目录 <b>test_project/</b>，已有内容先清理）。<br>
2) 双击 <b>run.bat</b> 启动产品控制台，把 test_project 接入并跑工具。<br>
3) 回本页点“查看得分”：只读工具运行产物，按能力判分，不做正则匹配。</div>
</div>
<div class="card"><b>选择病例</b><div id="cases"></div></div>
<div class="card">
<button onclick="generate()">生成考卷</button>
<button onclick="loadScore()">查看得分</button>
<span id="msg" class="small"></span></div>
<div class="card"><b>当前考卷</b><pre id="paper">还没有考卷</pre></div>
<div class="card"><b>能力得分</b><div id="scoreChart"></div><pre id="score">未评估</pre></div>
<script>
async function api(path, body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});return await r.json();}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function sel(){return [...document.querySelectorAll('input[type=checkbox]:checked')].map(x=>x.value);}
function show(id,r){document.getElementById(id).textContent=JSON.stringify(r,null,2);}
function mark(r){const el=document.getElementById('msg');el.className=r.ok?'ok small':'bad small';el.textContent=r.ok?(r.evaluated===false?'未评估：'+(r.reason||''):'完成'):'失败：'+(r.error||'');}
async function init(){
  const d=await api('/api/cases');
  document.getElementById('cases').innerHTML=d.cases.map(c=>
    `<label><input type="checkbox" value="${c.case_id}"> <b>${c.case_id}</b> — ${c.notes||''}</label>`).join('');
  const p=await api('/api/paper'); if(p.ok) show('paper',p.paper);
}
async function generate(){const r=await api('/api/paper',{cases:sel()});mark(r);if(r.ok)show('paper',r.paper);}
function bar(label, value, max, cls){const p=max>0?Math.min(100,Math.round(value/max*100)):0;
  return `<div class="bar"><span>${label}</span><div class="track"><i class="${cls}" style="width:${p}%"></i></div><b>${value}</b></div>`;}
function renderChart(r){
  const el=document.getElementById('scoreChart'); const d=r.dimensions||{};
  if(!r.evaluated){el.innerHTML='<div class="small">未评估：'+(r.reason||'')+'</div>';return;}
  const dv=d.discovery||{}, rp=d.repair||{}, rs=d.reproducible||{};
  const maxD=Math.max(1,dv.expected||0);
  const maxR=Math.max(1,rp.matched||0);
  el.innerHTML=`
    <div class="small">run ${esc(r.run&&r.run.run_id||'')} · 精度 ${dv.precision??'-'} · 召回 ${dv.recall??'-'}</div>
    <div class="grp"><b>发现</b>${bar('TP',dv.tp||0,maxD,'okbar')}${bar('FP',dv.fp||0,maxD,'badbar')}${bar('FN',dv.fn||0,maxD,'warnbar')}</div>
    <div class="grp"><b>修复</b>${bar('编译',rp.compiled||0,maxR,'okbar')}${bar('可运行',rp.runnable||0,maxR,'okbar')}${bar('有效',rp.effective||0,maxR,'warnbar')}</div>
    <div class="small">归因：Top1 ${(d.attribution&&d.attribution.top1_hit)?'命中':'未命中'} · 证据引用 ${d.attribution&&d.attribution.evidence_refs_ratio!=null?d.attribution.evidence_refs_ratio:'-'} · ECE ${d.attribution&&d.attribution.ece!=null?d.attribution.ece:'样本不足'}</div>
    <div class="small">可复现：${rs.consistent===null?'无法评估（1 次运行）':(rs.consistent?'一致':'不一致')} · 运行数 ${rs.runs||1}</div>`;
}
async function loadScore(){const r=await api('/api/score');mark(r);renderChart(r);show('score',r);}
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
        if p == "/api/paper":
            if not PAPER.exists():
                return self._send(200, {"ok": False, "error": "还没有考卷"})
            return self._send(200, {"ok": True,
                                    "paper": json.loads(PAPER.read_text(encoding="utf-8"))})
        if p == "/api/score":
            try:
                return self._send(200, score())
            except Exception as e:  # noqa: BLE001
                return self._send(409, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        self._send(404, {"ok": False})

    def do_POST(self):
        p = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        if p == "/api/paper":
            try:
                return self._send(200, generate([str(c) for c in (body.get("cases") or [])]))
            except Exception as e:  # noqa: BLE001
                return self._send(409, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        self._send(404, {"ok": False})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    LAB.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"I8 考卷台: http://127.0.0.1:{a.port}")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
