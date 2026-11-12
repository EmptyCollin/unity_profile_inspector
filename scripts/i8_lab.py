"""I8 考卷台：用 组合病例 + oracle + 干净 base 生成测试工程，并查看当前考卷得分。

定位（2026-11-13 修正）：
- 这里只负责"出考卷"和"看得分"，不做诊断、采集、修复；
- 用户用 `run.bat` 启动产品控制台，自己把 `test_project` 接入并跑工具；
- 得分 = 工具的机会扫描引擎（与产品控制台机会建单同一入口）在 test_project 上的检出
  与 oracle 期望的对比。oracle 不进工程。

用法：
    python scripts/i8_lab.py --port 8787
  或双击 start_lab.bat
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
from upi.core.opportunity import PATTERNS, scan  # noqa: E402

CASES_DIR = ROOT / "sim" / "cases"
ORACLE_DIR = ROOT / "sim" / "oracle" / "cases"
BASE = ROOT / "sim" / "project"
TEST_PROJECT = ROOT / "test_project"
LAB = ROOT / "runs" / "i8_lab"
PAPER = LAB / "paper.json"

PATTERN_IDS = {p.id for p in PATTERNS}


def _case_ids() -> list[str]:
    return sorted(d.name for d in CASES_DIR.glob("c*") if (d / "manifest.json").is_file())


def cases() -> list[dict]:
    out = []
    for cid in _case_ids():
        m = json.loads((CASES_DIR / cid / "manifest.json").read_text(encoding="utf-8"))
        pid = m.get("pattern_id", "")
        ex = m.get("expects") or {}
        out.append({"case_id": cid, "notes": m.get("notes", ""),
                    "pattern_id": pid, "scored": pid in PATTERN_IDS,
                    "windows": ex.get("windows", []),
                    "symbols": ex.get("symbols", [])})
    return out


def _oracle_patterns(case_ids: list[str]) -> tuple[list[str], list[str]]:
    """按 oracle 的 atomic_cases 反查期望 pattern；返回 (expected, unscored)。

    以 oracle 为权威：只有 oracle 里出现过的 pattern 才算"可判分"。
    """
    expected: set[str] = set()
    scored_cases: set[str] = set()
    for p in sorted(ORACLE_DIR.glob("c*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        atoms = set(d.get("atomic_cases") or [])
        if not atoms or not atoms <= set(case_ids):
            continue
        for e in d.get("expected_opportunities", []):
            expected.add(str(e.get("pattern_id")))
        scored_cases |= atoms
    unscored = [c for c in case_ids if c not in scored_cases]
    return sorted(expected), unscored


def generate(case_ids: list[str]) -> dict:
    dirs = [CASES_DIR / c for c in case_ids]
    for d in dirs:
        if not (d / "manifest.json").is_file():
            raise ValueError(f"case 不存在：{d.name}")
    target = TEST_PROJECT.resolve()
    if target != (ROOT / "test_project").resolve():
        raise RuntimeError(f"拒绝清理非考卷路径：{target}")
    if target.exists():
        shutil.rmtree(target)
    run = apply_cases(BASE, dirs, target)
    expected, unscored = _oracle_patterns(case_ids)
    paper = {"cases": run.case_ids, "files": run.files,
             "base_hash": tree_hash(BASE), "expected_patterns": expected,
             "unscored_cases": unscored,
             "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "project": str(target)}
    LAB.mkdir(parents=True, exist_ok=True)
    PAPER.write_text(json.dumps(paper, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "paper": paper}


def score() -> dict:
    if not PAPER.exists():
        return {"ok": False, "error": "还没有考卷，先生成"}
    paper = json.loads(PAPER.read_text(encoding="utf-8"))
    if not TEST_PROJECT.is_dir():
        return {"ok": False, "error": "test_project 不存在（可能被删了），重新生成考卷"}
    findings = scan(TEST_PROJECT, project_id="test_project")
    found_by_pattern: dict[str, list[dict]] = {}
    for f in findings:
        found_by_pattern.setdefault(f.pattern_id, []).append(
            {"file": f.code_anchor.file, "line": f.code_anchor.line_range,
             "metric": f.metric})
    expected = set(paper.get("expected_patterns") or [])
    found = set(found_by_pattern)
    tp = sorted(expected & found)
    fp = sorted(found - expected)
    fn = sorted(expected - found)
    per_case = []
    for cid in paper.get("cases", []):
        m = json.loads((CASES_DIR / cid / "manifest.json").read_text(encoding="utf-8"))
        pid = m.get("pattern_id", "")
        per_case.append({"case_id": cid, "pattern_id": pid,
                         "scored": pid in expected,
                         "hit": pid in found})
    negative = not expected
    return {"ok": True, "paper": {"cases": paper.get("cases"),
                                  "created_at": paper.get("created_at")},
            "expected": sorted(expected), "found": sorted(found),
            "tp": tp, "fp": fp, "fn": fn,
            "found_detail": {k: v for k, v in found_by_pattern.items()},
            "precision": round(len(tp) / max(1, len(found)), 4),
            "recall": round(len(tp) / max(1, len(expected)), 4),
            "negative_control": negative,
            "negative_ok": (not found) if negative else None,
            "per_case": per_case,
            "unscored_cases": paper.get("unscored_cases", []),
            "engine": "upi.core.opportunity.scan（与产品控制台机会建单同一引擎）"}


HTML = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>I8 考卷台</title><style>
body{font:14px system-ui;background:#0f141b;color:#dbe7f3;max-width:980px;margin:28px auto;padding:0 18px}
h1{font-size:22px;margin:0 0 6px}.card{background:#17212c;border:1px solid #2a3a4a;border-radius:10px;padding:14px;margin:12px 0}
label{display:block;margin:7px 0}button{background:#1e4e3a;color:#dbe7f3;border:1px solid #2e7a58;border-radius:7px;padding:8px 14px;cursor:pointer;margin:4px 6px 4px 0}
pre{white-space:pre-wrap;background:#0b1117;padding:10px;border-radius:7px;max-height:460px;overflow:auto}
.small{color:#8da2b5}.ok{color:#5ee0a0}.bad{color:#ff8b8b}.hint{background:#131c25;border-left:3px solid #2e7a58;padding:8px 10px;margin:8px 0}
</style></head><body>
<h1>I8 考卷台 <span class="small">组合病例 + oracle → test_project；看得分</span></h1>
<div class="card"><b>怎么考试</b>
<div class="hint small">1) 勾选病例 → 生成考卷（写到仓库根目录 <b>test_project/</b>，已有内容先清理）。<br>
2) 双击 <b>run.bat</b> 启动产品控制台，把 test_project 接入并跑工具。<br>
3) 回到本页点“查看得分”，对比工具检出与 oracle。</div>
</div>
<div class="card"><b>选择病例</b><div id="cases"></div></div>
<div class="card">
<button onclick="generate()">生成考卷</button>
<button onclick="loadScore()">查看得分</button>
<span id="msg" class="small"></span></div>
<div class="card"><b>当前考卷</b><pre id="paper">还没有考卷</pre></div>
<div class="card"><b>得分</b><pre id="score">未评分</pre></div>
<script>
async function api(path, body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});return await r.json();}
function sel(){return [...document.querySelectorAll('input[type=checkbox]:checked')].map(x=>x.value);}
function show(id,r){document.getElementById(id).textContent=JSON.stringify(r,null,2);}
function mark(r){const el=document.getElementById('msg');el.className=r.ok?'ok small':'bad small';el.textContent=r.ok?'完成':'失败：'+(r.error||'');}
async function init(){
  const d=await api('/api/cases');
  document.getElementById('cases').innerHTML=d.cases.map(c=>
    `<label><input type="checkbox" value="${c.case_id}" ${c.scored?'':'data-unscored="1"'}> <b>${c.case_id}</b> — ${c.notes||''}
     <span class="small">pattern:${c.pattern_id||'-'} ${c.scored?'':'（无 oracle，仅出题不计分）'}</span></label>`).join('');
  const p=await api('/api/paper'); if(p.ok) show('paper',p.paper);
}
async function generate(){const r=await api('/api/paper',{cases:sel()});mark(r);if(r.ok)show('paper',r.paper);}
async function loadScore(){const r=await api('/api/score');mark(r);if(r.ok)show('score',r);}
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
