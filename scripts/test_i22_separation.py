"""I22：oracle 与产品路径隔离。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


exam = (ROOT / "scripts" / "exam_batch.py").read_text(encoding="utf-8")
scorer = (ROOT / "bench" / "scorer.py").read_text(encoding="utf-8")

ok("exam_batch 不读 paper.json", "paper.json" not in exam)
ok("exam_batch 不调用 score_run", "score_run" not in exam)
ok("scorer 才读 oracle", "paper.json" in scorer and "score_run" in scorer)

# 产品模块不得 import bench
for p in (ROOT / "upi").rglob("*.py"):
    text = p.read_text(encoding="utf-8", errors="replace")
    ok(f"{p.name} 不 import bench", "import bench" not in text, str(p))

print(f"\n==== I22 隔离测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
