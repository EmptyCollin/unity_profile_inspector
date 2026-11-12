"""I7b Web API：工程列表/新增/激活/归档。"""
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

from upi.agents.webui import WebUI          # noqa: E402
from upi.core import events as E            # noqa: E402

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_proj_web_"))
E.isolate(TMP / "runs")
proj = TMP / "Game"
(proj / "Assets" / "Scenes").mkdir(parents=True)
(proj / "ProjectSettings").mkdir()
(proj / ".git").mkdir()
(proj / "Assets" / "Scenes" / "Main.unity").write_text("%YAML 1.1\n", encoding="utf-8")


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{ui.port}{path}", timeout=5) as r:
        return r.status, r.read()


def post(path, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{ui.port}{path}",
        data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


ui = WebUI(port=0, token="t").start()
threading.Thread(target=ui.serve_forever, daemon=True).start()
time.sleep(0.15)
try:
    code, body = get("/?token=t")
    html = body.decode("utf-8", "replace")
    ok("工程页/顶栏切换器已注入", code == 200
       and 'id="projectSelect"' in html and 'id="page-projects"' in html)

    code, body = get("/api/projects?token=t")
    ok("初始工程列表", code == 200 and json.loads(body)["entries"] == [])

    code, data = post("/api/projects?token=t", {
        "project_path": str(proj), "display_name": "Game",
        "default_scene": "Assets/Scenes/Main.unity"})
    ok("新增工程 API", code == 200 and data["project"]["project_id"] == "game", data)
    pid = data["project"]["project_id"]

    code, data = post(f"/api/projects/{pid}/activate?token=t", {})
    ok("激活工程 API", code == 200 and data["active"] == pid, data)
    code, body = get("/api/status?token=t")
    ok("状态绑定 active", json.loads(body)["projects"]["active"] == pid)

    code, data = post(f"/api/projects/{pid}/archive?token=t", {})
    ok("归档工程 API", code == 200 and data["project"]["status"] == "archived")
    code, data = post(f"/api/projects/{pid}/activate?token=t", {})
    ok("归档不能激活 API", code == 409, (code, data))
finally:
    ui.stop()

print(f"\n==== 工程 Web API 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
