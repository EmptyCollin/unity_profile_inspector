"""能力产物契约 + 能力判分（离线，无 Unity、无正则）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import findings as F  # noqa: E402

PASS, FAIL = [], []


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp(prefix="upi_findings_"))
F.RUNS = TMP


def mkrun(run_id="r1", anchors=None, mechanism="hot_path", fp="fp-a",
          findings_n=None, repairs=None):
    anchors = anchors or [("Assets/A.cs", "UpdateHud")]
    fs = [F.ToolFinding(finding_id=f"f{i}", mechanism=mechanism,
                        anchors=[F.Anchor(file=a, method=m) for a, m in anchors])
          for i in range(findings_n if findings_n is not None else 1)]
    return F.RunFindings(run_id=run_id, project_id="test_project",
                         protocol_fingerprint=fp, created_at="2026-01-01T00:00:01",
                         findings=fs, repairs=repairs or [])


# ── 契约 ──────────────────────────────────────────────
try:
    F.ToolFinding(finding_id="x", anchors=[])
    ok("无锚点 finding 被拒", False)
except Exception:
    ok("无锚点 finding 被拒", True)
try:
    F.Anchor(file=" ")
    ok("空文件锚点被拒", False)
except Exception:
    ok("空文件锚点被拒", True)
try:
    F.ToolFinding(finding_id="x", anchors=[F.Anchor(file="A.cs")], bogus=1)
    ok("未知字段被拒（extra=forbid）", False)
except Exception:
    ok("未知字段被拒（extra=forbid）", True)

r = mkrun(repairs=[F.RepairRecord(finding_id="f0", compile_ok=True,
                                  gameplay_ok=True, verdict="resolved")])
p = F.save_run(r)
ok("产物落盘", p.exists() and p.name == "findings.json")
loaded = F.load_run("r1")
ok("产物可回读", loaded is not None and loaded.findings[0].anchors[0].method == "UpdateHud")
ok("项目索引可读", len(F.load_runs("test_project")) == 1)

# ── 判分：发现 ────────────────────────────────────────
paper = {"expectations": [
    {"mechanism": "ui_text_rebuild_per_frame",
     "anchor": "Assets/A.cs::UpdateHud", "metric": "cpu"}]}
s = F.score_run(paper, r)
d = s["dimensions"]["discovery"]
ok("锚点匹配 → TP=1 / P=R=1", d["tp"] == 1 and d["fp"] == 0 and d["fn"] == 0
   and d["precision"] == 1.0 and d["recall"] == 1.0, str(d))

wrong_file = mkrun("r2", anchors=[("Assets/B.cs", "UpdateHud")])
d2 = F.score_run(paper, wrong_file)["dimensions"]["discovery"]
ok("文件不符 → FN+FP", d2["tp"] == 0 and d2["fn"] == 1 and d2["fp"] == 1, str(d2))

wrong_method = mkrun("r3", anchors=[("Assets/A.cs", "Other")])
d3 = F.score_run(paper, wrong_method)["dimensions"]["discovery"]
ok("方法不符 → FN+FP", d3["tp"] == 0 and d3["fp"] == 1, str(d3))

extra = mkrun("r4", anchors=[("Assets/A.cs", "UpdateHud")], findings_n=2)
d4 = F.score_run(paper, extra)["dimensions"]["discovery"]
ok("同锚点重复不计 FP（冗余非误报）", d4["tp"] == 1 and d4["fp"] == 0, str(d4))

# ── 判分：负对照 / 修复 / 可复现 ──────────────────────
neg_paper = {"expectations": []}
neg_ok_run = mkrun("r5", findings_n=0)
sneg = F.score_run(neg_paper, neg_ok_run)
ok("干净卷无 finding → 负对照通过",
   sneg["negative_control"] is True and sneg["negative_ok"] is True)

neg_bad = mkrun("r6", findings_n=1)
sneg2 = F.score_run(neg_paper, neg_bad)
ok("干净卷有 finding → 负对照失败",
   sneg2["negative_ok"] is False)

rep = F.score_run(paper, r)["dimensions"]["repair"]
ok("修复：编译通过 + resolved 记有效",
   rep["matched"] == 1 and rep["compiled"] == 1 and rep["effective"] == 1, str(rep))

same = mkrun("r7", anchors=[("Assets/A.cs", "UpdateHud")], fp="fp-a")
F.save_run(same)
srep = F.score_run(paper, same)["dimensions"]["reproducible"]
ok("同协议同位置 → 可复现", srep["consistent"] is True, str(srep))

diff = mkrun("r8", anchors=[("Assets/C.cs", "X")], fp="fp-a")
F.save_run(diff)
srep2 = F.score_run(paper, diff)["dimensions"]["reproducible"]
ok("同协议不同位置 → 不可复现", srep2["consistent"] is False, str(srep2))

# I11f：最终不可用 → 整卷 0（硬门禁）
bad = mkrun("r9", anchors=[("Assets/A.cs", "UpdateHud")], fp="fp-a")
bad.usability_ok = False
bad.usability_note = "编译失败且回滚失败"
gate = F.score_run(paper, bad)
ok("最终不可用 → 整卷 0", gate.get("usability_failed") is True
   and gate["dimensions"]["discovery"]["tp"] == 0
   and gate["dimensions"]["repair"]["compiled"] == 0, str(gate)[:160])

print(f"\n==== 能力产物/判分测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
