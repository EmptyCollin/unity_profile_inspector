"""I23：批量 ConfigEditPlan。"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.apply_config_plans import from_proposals  # noqa: E402
from upi.core.config_edit import apply, rollback, validate  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
ps = TMP / "ProjectSettings"
ps.mkdir()
f = ps / "QualitySettings.asset"
original = ("QualitySettings:\n  m_CurrentQuality: 1\n  m_QualitySettings:\n"
            "  - name: Mobile\n    shadowDistance: 80\n    antiAliasing: 2\n"
            "  - name: High\n    shadowDistance: 100\n    antiAliasing: 4\n")
f.write_text(original, encoding="utf-8")
doc = {"proposals": [
    {"path": "ProjectSettings/QualitySettings.asset",
     "section": "m_QualitySettings", "index": 0,
     "field": "shadowDistance", "before": 80, "after": 40},
    {"path": "ProjectSettings/QualitySettings.asset",
     "section": "m_QualitySettings", "index": 1,
     "field": "antiAliasing", "before": 4, "after": 0},
]}
edits = from_proposals(doc["proposals"])
ok("提案转 edit", len(edits) == 2 and edits[0].section == "m_QualitySettings")
ok("批量 validate 通过", validate(TMP, edits) == [], validate(TMP, edits))
done = apply(TMP, edits)
text = f.read_text(encoding="utf-8")
ok("Mobile shadow 已改", "name: Mobile\n    shadowDistance: 40" in text, text)
ok("High AA 已改", "name: High\n    shadowDistance: 100\n    antiAliasing: 0" in text)
rollback(TMP, done)
ok("批量 rollback", f.read_text(encoding="utf-8") == original)

print(f"\n==== I23 配置批量测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
