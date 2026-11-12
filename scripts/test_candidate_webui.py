"""I8d Web API：最近证据候选 → 工单。"""
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
sys.path.insert(0, str(ROOT / "scripts"))

from _compose import compose  # noqa: E402

from upi.agents.webui import WebUI  # noqa: E402
from upi.config.project_profile import reset_profile  # noqa: E402
from upi.config.project_registry import ProjectRegistry  # noqa: E402
from upi.core import events as E  # noqa: E402
from upi.core.evidence_v2 import (EvidenceBundleV2, FunctionStat,  # noqa: E402
                                  SourceSpan)
from upi.core.source_index import SourceIndex  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


TMP = Path(tempfile.mkdtemp(prefix="upi_cand_web_"))
E.isolate(TMP / "runs")
PROJECT = compose(["c02"])
reset_profile()
reg = ProjectRegistry(E.PROJECTS)
e = reg.add(project_path=PROJECT, display_name="Chroma Rogue",
            default_scene="Assets/Scenes/00_Bootstrap.unity")
reg.activate(e.project_id)

idx = SourceIndex.build(PROJECT)
span = idx.resolve("ChromaGame.UpdateHud")
assert span is not None
bundle = EvidenceBundleV2(
    run_id="r1", project_id=e.project_id, frames=[1],
    functions=[FunctionStat(frame_idx=1, symbol="ChromaGame.UpdateHud",
                            calls=10, total_ms=2.0, source=span)],
)
path = E.RUNS / "r1" / "evidence" / "evidence_v2.json"
bundle.write(path)

ui = WebUI(port=0, token="t").start()
threading.Thread(target=ui.serve_forever, daemon=True).start()
time.sleep(0.2)
try:
    req = urllib.request.Request(
        f"http://127.0.0.1:{ui.port}/api/tickets/candidates?token=t",
        data=b"{}", method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read().decode("utf-8"))
    ok("候选工单 API", r.status == 200 and data.get("ok") is True, data)
    it = (data.get("ticket") or {}).get("items", [{}])[0]
    ok("选中 HUD recipe", it.get("playbook_id") == "PB-OP-HUD-TEXT-CACHE", it)
    ok("auto pending", it.get("permission_tier") == "auto"
       and it.get("status") == "pending", it)
finally:
    ui.stop()

print(f"\n==== 候选工单 Web API: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
