"""I0 Web 控制台骨架测试（离线、零编辑器）。

覆盖：首页壳、状态 API、token 门禁、SSE 首帧、隔离目录不写真实 runs/。
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

from upi.agents.webui import WebUI                     # noqa: E402
from upi.core import events as E                       # noqa: E402
from upi.core.probe_events import EventLog, ProbeEvent  # noqa: E402
from upi.tooling import probe_control as PC           # noqa: E402

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_webui_"))
E.isolate(TMP / "runs")
os.environ["UPI_FINGERPRINT_DB"] = str(TMP / "fingerprints.db")
os.environ["UPI_TICKET_DIR"] = str(TMP / "tickets")
(E.RUNS / "_probe").mkdir(parents=True, exist_ok=True)
(E.RUNS / "_probe" / "pb_hb.txt").write_text("1", encoding="utf-8")
(E.RUNS / "daemon_status.json").write_text(json.dumps({
    "pid": 4242, "phase": "idle", "sampling_enabled": True,
    "sampling_mode": "steady", "updated_at": "2026-11-11T22:00:00",
}), encoding="utf-8")
EventLog().append(ProbeEvent(
    kind="sample", metric="Render/DrawCalls", series=[47, 47, 47],
    frame_idx=100, rel_frame=100, data_tier="A2",
))


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def post_json(url: str, body: dict):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


ui = WebUI(port=0, token="test-token").start()
th = threading.Thread(target=ui.serve_forever, daemon=True)
th.start()
time.sleep(0.15)          # 让 serve_forever 就绪（端口已 bind，连接本可立即成功）
base = f"http://127.0.0.1:{ui.port}"

try:
    code, hdr, body = get(base + "/?token=test-token")
    html = body.decode("utf-8", "replace")
    ok("首页 200", code == 200, f"code={code}")
    ok("首页含品牌与导航", "UPI 性能 Agent" in html and "首页" in html and "I3b 执行" in html)
    ok("首页注入 token（前端 API/SSE 用）", "test-token" in html)
    ok("首页 UTF-8 声明", "charset=utf-8" in (hdr.get("Content-Type") or "").lower())
    ok("首页设置 HttpOnly cookie", "HttpOnly" in (hdr.get("Set-Cookie") or ""))

    code, hdr, body = get(base + "/api/status?token=test-token")
    data = json.loads(body.decode("utf-8"))
    ok("状态 API 200 + JSON", code == 200 and isinstance(data, dict))
    ok("状态含单通道标记", data.get("ui", {}).get("single_channel") is True)
    ok("状态含两形态字段", data.get("mode") in ("resident", "regression"))
    for k in ("daemon", "project", "probe", "tickets", "events"):
        ok(f"状态含 {k}", k in data)
    ok("探针心跳读到（隔离目录）", data.get("probe", {}).get("heartbeat_age_s") is not None)
    ok("守护状态读到隔离 status", data.get("daemon", {}).get("sampling") == "steady")
    ok("状态含统一探针事件", data.get("probe", {}).get("events_total") == 1
       and len(data.get("probe_events") or []) == 1)
    ok("事件载荷裁剪（不把整段 series 发前端）",
       "series" not in (data.get("probe_events") or [{}])[0]
       and (data["probe_events"][0].get("series_n") == 3))
    ok("状态含 cluster 摘要", data.get("cluster_total") == 1
       and len(data.get("clusters") or []) == 1)
    ok("cluster 摘要含机制级字段",
       {"metric", "shape", "count", "data_tier"} <= set((data.get("clusters") or [{}])[0]))
    ok("状态含指纹库", data.get("fingerprints", {}).get("total") == 6
       and len(data.get("fingerprint_records") or []) == 6)

    code, _hdr, _body = get(base + "/api/status")
    ok("无 token 访问 API 被拒", code == 403, f"code={code}")

    req = urllib.request.Request(base + "/api/status",
                                 headers={"Cookie": "upi_token=test-token"})
    with urllib.request.urlopen(req, timeout=5) as r:
        cookie_ok = r.status == 200
    ok("正确 cookie 可访问 API", cookie_ok)

    req = urllib.request.Request(base + "/api/status",
                                 headers={"Cookie": "upi_token=test"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            prefix_code = r.status
    except urllib.error.HTTPError as e:
        prefix_code = e.code
    ok("cookie 前缀不算通过（精确比较）", prefix_code == 403, f"code={prefix_code}")

    # SSE 是长连接，不能 read() 到底；只读首帧即关。
    with urllib.request.urlopen(base + "/api/events?token=test-token", timeout=5) as r:
        sse_code, sse_hdr = r.status, r.headers
        first = r.readline().decode("utf-8", "replace")
    ok("SSE 200 + 事件流 content-type",
       sse_code == 200 and "text/event-stream" in (sse_hdr.get("Content-Type") or ""))
    ok("SSE 首帧是 data: JSON", first.startswith("data: ") and
       isinstance(json.loads(first[6:]), dict))

    code, _hdr, _body = get(base + "/nope?token=test-token")
    ok("未知路径 404", code == 404, f"code={code}")

    code, built = post_json(base + "/api/tickets/build?token=test-token", {})
    t = built.get("ticket") or {}
    ok("POST 建单 200", code == 200 and t.get("status") == "awaiting_approval")
    ok("工单含候选与校验码", bool(t.get("items")) and len(t.get("approve_token") or "") == 4)
    tid = t.get("ticket_id")
    code, appr = post_json(base + f"/api/tickets/{tid}/approve?token=test-token",
                           {"approver": "测试", "note": "批准", "token": t.get("approve_token")})
    ok("POST 批准 200", code == 200 and appr.get("ticket", {}).get("status") == "approved")
    code, ex = post_json(base + f"/api/tickets/{tid}/execute?token=test-token", {})
    ok("无可执行 plan 返回 409（诚实边界）", code == 409 and ex.get("ok") is False)
    code, _hdr, body = get(base + "/api/status?token=test-token")
    data2 = json.loads(body.decode("utf-8"))
    ok("状态含批量工单", any(x.get("ticket_id") == tid
                             for x in (data2.get("batch_tickets") or [])))

    # 探针控制：用假实现，不碰真 Unity
    PC.install = lambda project=None, timeout_s=180: {"ok": True, "detail": "fake install"}
    PC.unload = lambda: {"ok": True, "detail": "fake unload"}
    PC.sample = lambda on=True, window=300: {"ok": True,
                                             "detail": f"sample={'on' if on else 'off'}"}
    code, r = post_json(base + "/api/probe/install?token=test-token", {})
    ok("POST 探针安装", code == 200 and r.get("ok") is True)
    code, r = post_json(base + "/api/probe/sample?token=test-token",
                        {"on": True, "window": 60})
    ok("POST 采样开", code == 200 and "sample=on" in r.get("detail", ""))
    code, r = post_json(base + "/api/probe/unload?token=test-token", {})
    ok("POST 探针卸载", code == 200 and r.get("ok") is True)
finally:
    ui.stop()
    th.join(timeout=2)

print()
if FAIL:
    print(f"==== Web 控制台骨架测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print(f"==== Web 控制台骨架测试: {len(PASS)} 通过, 0 失败")
