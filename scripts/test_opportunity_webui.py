"""I7d Web：从当前工程机会建工单。"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents.webui import WebUI  # noqa: E402
from upi.config.project_profile import reset_profile  # noqa: E402
from upi.config.project_registry import ProjectRegistry  # noqa: E402
from upi.core import events as E  # noqa: E402

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_opp_web_"))
E.isolate(TMP / "runs")
reset_profile()
reg = ProjectRegistry()
entry = reg.add(project_path=ROOT / "sim" / "project", display_name="Chroma Rogue",
                default_scene="Assets/Scenes/00_Bootstrap.unity")
reg.activate(entry.project_id)
(Path(tempfile.gettempdir()) / "devsim_active_features.json").write_text(
    json.dumps({"project_path": str(ROOT / "sim" / "project"),
                "legacy_ui": True, "legacy_material": True,
                "legacy_match_alloc": True, "sync_shop_load": True}),
    encoding="utf-8")

ui = WebUI(port=0, token="t").start()
threading.Thread(target=ui.serve_forever, daemon=True).start()
time.sleep(0.15)


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


try:
    req = urllib.request.Request(
        f"http://127.0.0.1:{ui.port}/api/tickets/opportunities?token=t",
        data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    ok("机会建单 API", data.get("ok") is True and data.get("ticket"), data)
    items = (data.get("ticket") or {}).get("items") or []
    ok("工单包含机会", len(items) >= 4, len(items))
    ok("工单带 project_id", (data.get("ticket") or {}).get("project_id") == "chroma-rogue")
    ok("至少一个 auto plan", any(i.get("permission_tier") == "auto" and i.get("plan")
                                  for i in items))
finally:
    ui.stop()

print(f"\n==== 机会 Web 工单测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
