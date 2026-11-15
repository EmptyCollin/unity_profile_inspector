"""I25：考试路径不得读取黑名单。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


FILES = [ROOT / "scripts" / "exam_batch.py", ROOT / "scripts" / "exam_live.py",
         ROOT / "bench" / "replay_reliability.py",
         ROOT / "bench" / "c2_per_target.py",
         ROOT / "bench" / "counterfactual.py"]
BAD = ("finding_blacklist", "BlacklistStore", "BlacklistFingerprint")
for f in FILES:
    if not f.is_file():
        continue
    text = f.read_text(encoding="utf-8", errors="replace")
    hits = [b for b in BAD if b in text]
    ok(f"考试隔离 {f.name}", not hits, hits)

print(f"\n==== I25 考试黑名单隔离: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
