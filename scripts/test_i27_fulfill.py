"""I27 fulfill：window_series / deep_window / paired_repeat 的接线路由。"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.evidence_fulfill import fulfill  # noqa: E402
from upi.core.evidence_request import EvidenceRequest  # noqa: E402
from upi.core.finding import Finding, WindowRef  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
RUN = TMP / "runs" / "cap1"
RUN.mkdir(parents=True)
(RUN / "action_windows.json").write_text(json.dumps([
    {"window_id": "cmd-0-start_game", "frame_start": 0, "frame_end": 10,
     "counters": {"CPU Main Thread Frame Time": {
         "median": 1000, "max": 1100, "n": 2, "series": [1000, 1100]}}},
    {"window_id": "cmd-1-wait", "frame_start": 11, "frame_end": 20,
     "counters": {"CPU Main Thread Frame Time": {
         "median": 2000, "max": 2100, "n": 2, "series": [2000, 2100]}}},
]), encoding="utf-8")

req = EvidenceRequest(kind="window_series", reason="need series",
                      finding_id="F1", window_ids=["cmd-1-wait"],
                      metric="CPU Main Thread Frame Time", request_id="r1")
d = fulfill(req, run_dir=RUN)
ok("window_series 可用", d.available and len(d.refs) == 1, d)
ok("window_series 只取请求窗口",
   list(d.rows) == ["cmd-1-wait"], d.rows)
ok("window_series 带 series",
   d.rows.get("cmd-1-wait", {}).get("series") == [2000, 2100], d.rows)

req_missing = EvidenceRequest(kind="window_series", reason="x",
                              window_ids=["cmd-9-nope"],
                              metric="CPU Main Thread Frame Time")
d2 = fulfill(req_missing, run_dir=RUN)
ok("缺窗口不可用", not d2.available, d2)

req_deep = EvidenceRequest(kind="deep_window", reason="x",
                           window_ids=["cmd-1-wait"],
                           metric="CPU Main Thread Frame Time")
d3 = fulfill(req_deep, run_dir=RUN)
ok("deep 缺 project 不可用", not d3.available, d3)

req_pair = EvidenceRequest(kind="paired_repeat", reason="x",
                           window_ids=["cmd-1-wait"],
                           metric="CPU Main Thread Frame Time")


def fake_capture(r):
    return {"refs": ["paired.json"], "rows": {"cmd-1-wait": {"median": 1}},
            "summary": "paired"}


d4 = fulfill(req_pair, run_dir=RUN, capture_fn=fake_capture)
ok("paired_repeat 注入可用", d4.available and d4.refs == ["paired.json"], d4)

# 无注入时不应假装成功
d5 = fulfill(req_pair, run_dir=RUN)
ok("paired_repeat 无注入不可用", not d5.available, d5)

# finding -> legacy Issue 映射保留精确 window
from upi.agents.nodes import _issue_from_finding  # noqa: E402
f = Finding(finding_id="F1", run_id="r1", window=WindowRef.from_id(
    "cmd-1-wait", frame_start=11, frame_end=20),
    metric="CPU Main Thread Frame Time", unit="ns",
    observed={"median": 2000, "max": 2100, "n": 2},
    reference={"median": 1000, "source": "baseline_median"},
    magnitude=1000, deviation_ratio=1.0, evidence_refs=["action_windows.json"])
issue = _issue_from_finding(f)
ok("issue window 精确", issue["window"] == "cmd-1-wait", issue.get("window"))
ok("issue evidence 指向 action_windows",
   issue["evidence"]["source"] == "action_windows", issue["evidence"])
ok("issue magnitude 保留", issue["magnitude"] == 1000, issue["magnitude"])

print(f"\n==== I27 fulfill: {len(PASS)} ok, {len(FAIL)} failed")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
