"""I6b2a：Web/API 全链路 E2E 冒烟（不碰 Unity）。

流程：状态 → 建工单 → 批准 → 执行 409 → 回归建套件/3 次基线 → 验证 →
报告 → 设置。结果写 runs/e2e_smoke_<ts>.json。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import events as E  # noqa: E402
from upi.core.probe_events import EventLog, ProbeEvent  # noqa: E402
from upi.tooling.console import force_utf8_streams  # noqa: E402

force_utf8_streams()

TMP = Path(tempfile.mkdtemp(prefix="upi_e2e_"))
E.isolate(TMP / "runs")
os.environ["UPI_FINGERPRINT_DB"] = str(TMP / "fingerprints.db")
os.environ["UPI_TICKET_DIR"] = str(TMP / "tickets")
os.environ["UPI_REGRESSION_DIR"] = str(TMP / "regression")

from upi.agents.webui import WebUI  # noqa: E402

checks: list[dict] = []


def ok(name: str, cond: bool, detail: str = ""):
    checks.append({"name": name, "ok": bool(cond), "detail": str(detail)[:300]})
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {str(detail)[:160]}"))


def get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post(url: str, body: dict):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def main() -> int:
    EventLog().append(ProbeEvent(
        kind="breach", metric="drawCalls", value=47.0, threshold=30.0,
        ring_before=[30.0] * 5, ring_after=[47.0] * 5,
        rel_frame=10, data_tier="A2"))

    ui = WebUI(host="127.0.0.1", port=0, token="e2e-token").start()
    th = threading.Thread(target=ui.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{ui.port}"
    time.sleep(0.3)
    try:
        code, body = get(base + "/?token=e2e-token")
        ok("GET / 200", code == 200 and b"UPI" in body, code)

        code, body = get(base + "/api/status?token=e2e-token")
        st = json.loads(body.decode("utf-8"))
        ok("status ok", code == 200 and st.get("ok") and st.get("settings"),
           st.get("ui"))

        code, r = post(base + "/api/tickets/build?token=e2e-token", {})
        t = r.get("ticket") or {}
        ok("build ticket", code == 200 and t.get("ticket_id"), code)
        code, r = post(base + f"/api/tickets/{t.get('ticket_id')}/approve?token=e2e-token",
                       {"approver": "e2e", "note": "ok", "token": t.get("approve_token")})
        ok("approve ticket", code == 200 and r.get("ticket", {}).get("status") == "approved")
        code, r = post(base + f"/api/tickets/{t.get('ticket_id')}/execute?token=e2e-token", {})
        ok("execute 409 (no plan)", code == 409, code)

        code, r = post(base + "/api/regression/suites?token=e2e-token",
                       {"name": "e2e", "protocol_fingerprint": "fp-e2e",
                        "command": f"{sys.executable} -c pass"})
        sid = (r.get("summary") or {}).get("suite_id")
        ok("create regression suite", code == 200 and sid, code)
        for _ in range(3):
            code, r = post(base + f"/api/regression/suites/{sid}/record?token=e2e-token", {})
        ok("record 3 baselines", code == 200 and r.get("summary", {}).get("runs") == 3)
        post(base + f"/api/regression/suites/{sid}/record?token=e2e-token", {})
        code, r = post(base + f"/api/regression/suites/{sid}/verify?token=e2e-token", {})
        ok("verify returns verdict", code == 200 and r.get("record", {}).get("verdict"))
        code, r = post(base + f"/api/regression/suites/{sid}/report?token=e2e-token", {})
        ok("generate report", code == 200 and r.get("ok"))
        code, body = get(base + f"/api/regression/suites/{sid}/report?token=e2e-token")
        ok("report HTML", code == 200 and "回归报告" in body.decode("utf-8", "replace"))
    finally:
        ui.stop()
        th.join(timeout=2)

    ok_all = all(c["ok"] for c in checks)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = ROOT / "runs" / f"e2e_smoke_{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"ok": ok_all, "checks": checks, "at": ts},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n==== E2E {'通过' if ok_all else '失败'}: "
          f"{sum(c['ok'] for c in checks)}/{len(checks)}；证据 {out}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
