"""I8 Lab：本地病例组合体验页（安全模式：不改 base、不启动 Unity）。

用法：
    python scripts/i8_lab.py --port 8787
    open http://127.0.0.1:8787
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.case_overlay import apply_cases  # noqa: E402
from upi.core.replay_spec import load_replay, merge_replays  # noqa: E402

CASES_DIR = ROOT / "sim" / "cases"
LAB = ROOT / "runs" / "i8_lab"


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


def latest_candidates() -> dict:
    files = sorted((ROOT / "runs").glob("*/evidence/candidates.json"),
                   key=lambda p: p.stat().st_mtime)
    if not files:
        return {"ok": False, "error": "还没有 candidates.json"}
    p = files[-1]
    return {"ok": True, "path": str(p), "data": json.loads(p.read_text(encoding="utf-8"))}


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
<button onclick="loadCandidates()">查看最近候选</button>
<button onclick="loadPreviewInfo()">查看当前组合</button>
<span id="msg" class="small"></span></div>
<div class="card"><b>组合结果</b><pre id="out">未运行</pre></div>
<script>
async function api(path, body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});return await r.json();}
async function init(){const d=await api('/api/cases');document.getElementById('cases').innerHTML=d.cases.map(c=>`<label><input type="checkbox" value="${c.case_id}" checked> <b>${c.case_id}</b> — ${c.notes||''} <span class="small">${c.replay}</span></label>`).join('');}
function selected(){return [...document.querySelectorAll('input[type=checkbox]:checked')].map(x=>x.value);}
async function runPreview(){document.getElementById('msg').textContent='组合中...';const r=await api('/api/preview',{cases:selected()});document.getElementById('msg').textContent=r.ok?'完成':'失败';document.getElementById('out').textContent=JSON.stringify(r,null,2);}
async function loadPreviewInfo(){const r=await api('/api/preview_info');document.getElementById('out').textContent=JSON.stringify(r,null,2);}
async function loadCandidates(){const r=await api('/api/candidates');document.getElementById('out').textContent=JSON.stringify(r,null,2);}
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
