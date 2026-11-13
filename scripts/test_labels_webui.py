"""I12e：标签审核 Web 通道（只有 UI 批准才生效）。"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents.webui import WebUI  # noqa: E402
from upi.config.project_profile import (ProjectProfile,  # noqa: E402
                                        register_profile, use_profile)
from upi.core import events as E  # noqa: E402
from upi.core.labels import LabelProposal, LabelStore  # noqa: E402

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_labels_web_"))
E.isolate(TMP / "runs")
register_profile(ProjectProfile(profile_id="labtest",
                                project_path=ROOT / "sim" / "project",
                                git_root=ROOT, subjects=["SampleScene"],
                                clean_subject=""), activate=True)
use_profile("labtest")

store = LabelStore("labtest")
rec, _ = store.propose(LabelProposal(
    project_id="labtest", mechanism="per_instance_material",
    file="Assets/A.cs", method="A.Build", frame_start=10,
    metric="drawCalls", confidence=0.7, rationale="test"))


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


ui = WebUI(port=0, token="t").start()
threading.Thread(target=ui.serve_forever, daemon=True).start()
time.sleep(0.2)
base = f"http://127.0.0.1:{ui.port}"
try:
    code, html = get(base + "/?token=t")
    ok("导航含标签页", code == 200 and 'data-page="labels"' in html
       and 'id="page-labels"' in html)
    code, body = get(base + "/api/labels?token=t")
    d = json.loads(body)
    ok("待审列表可见", code == 200 and len(d["pending"]) == 1, body[:160])
    ok("未批前 active 为空", d["active"] == [])
    code, d2 = post(base + "/api/labels/decision?token=t",
                    {"label_id": rec["label_id"], "decision": "approve",
                     "reviewer": "user", "reason": "确认"})
    ok("UI 批准后生效", code == 200 and d2.get("result") == "approved", d2)
    code, body = get(base + "/api/labels?token=t")
    ok("active 出现", len(json.loads(body)["active"]) == 1)
    code, d3 = post(base + "/api/labels/decision?token=t",
                    {"label_id": rec["label_id"], "decision": "reject",
                     "reviewer": "user"})
    ok("已批不可再改", code == 400 and d3.get("ok") is False, d3)
    code, d4 = post(base + "/api/labels/decision?token=t",
                    {"label_id": rec["label_id"], "decision": "approve",
                     "reviewer": ""})
    ok("缺审批人被拒", code == 400, d4)
finally:
    ui.stop()

print(f"\n==== 标签审核 UI 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
