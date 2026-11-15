"""I26-ext E1：生产/考试共用 site card。"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.observation import site_card_for  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
base = TMP / "cap"
base.mkdir()
(base / "pass1_timeline.json").write_text(json.dumps([
    {"cmd_index": 0, "type": "update", "frame": 1},
]), encoding="utf-8")
d = TMP / "cap_a0" / "evidence"
d.mkdir(parents=True)
(d / "evidence_v2.json").write_text(json.dumps({"functions": [
    {"symbol": "ChromaGame.Update", "calls": 2, "self_ms": 4.0,
     "total_ms": 5.0, "source": {"file": "A.cs", "start_line": 10}},
]}), encoding="utf-8")

card = site_card_for("cap", root=TMP)
ok("非空 card", "ChromaGame.Update" in card, card)
ok("含 source line", "A.cs:10" in card, card)
ok("空 capture 空 card", site_card_for("", root=TMP) == "")
ok("不存在 capture 空 card", site_card_for("none", root=TMP) == "")

print(f"\n==== I26-ext observation 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
