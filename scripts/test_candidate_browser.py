"""浏览器验收：工单页“从证据候选生成工单”。"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _compose import compose  # noqa: E402

from upi.agents.webui import WebUI  # noqa: E402
from upi.config.project_profile import reset_profile  # noqa: E402
from upi.config.project_registry import ProjectRegistry  # noqa: E402
from upi.core import events as E  # noqa: E402
from upi.core.evidence_v2 import EvidenceBundleV2, FunctionStat  # noqa: E402
from upi.core.source_index import SourceIndex  # noqa: E402

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_cand_browser_"))
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
EvidenceBundleV2(
    run_id="r1", project_id=e.project_id, frames=[1],
    functions=[FunctionStat(frame_idx=1, symbol="ChromaGame.UpdateHud",
                            calls=10, total_ms=2.0, source=span)],
).write(E.RUNS / "r1" / "evidence" / "evidence_v2.json")

ui = WebUI(port=0, token="t").start()
threading.Thread(target=ui.serve_forever, daemon=True).start()
time.sleep(0.2)


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        print("SKIP: playwright 不可用：", exc)
        return 0
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not Path(chrome).exists():
        print("SKIP: 本机没有 Chrome")
        return 0
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, executable_path=chrome,
                              args=["--no-sandbox"])
        page = b.new_page()
        page.goto(f"http://127.0.0.1:{ui.port}/?token=t", wait_until="networkidle")
        page.click('a[data-page="tickets"]')
        page.wait_for_selector("#candTicketBtn", timeout=5000)
        page.click("#candTicketBtn")
        page.wait_for_timeout(900)
        html = page.content()
        ok("候选建单按钮存在", 'id="candTicketBtn"' in html)
        ok("页面出现 HUD recipe", "PB-OP-HUD-TEXT-CACHE" in html, html[-500:])
        ok("页面出现 auto/pending", "auto" in html and "pending" in html)
        b.close()
    print(f"\n==== 候选工单浏览器验收: {len(PASS)} 通过, {len(FAIL)} 失败")
    return 1 if FAIL else 0


try:
    code = main()
finally:
    ui.stop()
raise SystemExit(code)
