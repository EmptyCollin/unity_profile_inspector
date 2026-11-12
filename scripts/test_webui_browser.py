"""浏览器级 Web 验收：工程页/工单页/机会建单按钮。"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents.webui import WebUI  # noqa: E402
from upi.config.project_profile import reset_profile  # noqa: E402
from upi.config.project_registry import ProjectRegistry  # noqa: E402
from upi.core import events as E  # noqa: E402

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_browser_"))
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
time.sleep(0.2)


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        print("SKIP: playwright 不可用：", e)
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
        ok("首页加载", "CHROMA" not in page.title() and "UPI" in page.content())
        page.click('a[data-page="projects"]')
        ok("工程页可见", page.locator("#page-projects").is_visible())
        ok("工程列表含 Chroma Rogue", "Chroma Rogue" in page.content())
        page.click('a[data-page="tickets"]')
        ok("工单页可见", page.locator("#page-tickets").is_visible())
        page.click("#oppTicketBtn")
        page.wait_for_timeout(700)
        html = page.content()
        ok("机会建单后出现工单", "PB-OP" in html or "opportunity" in html
           or "cpuMainThreadFrameTimeMs" in html)
        b.close()
    print(f"\n==== 浏览器 Web 验收: {len(PASS)} 通过, {len(FAIL)} 失败")
    return 1 if FAIL else 0


try:
    code = main()
finally:
    ui.stop()
raise SystemExit(code)
