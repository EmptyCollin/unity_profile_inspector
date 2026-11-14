"""I24：压缩 replay 唯一入口检查。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "runs" / "i8_lab"


def waits(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    return sum(c.get("seconds", 0) for c in d["commands"]
               if c["type"] == "wait")


PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


main = waits(LAB / "paper_replay.json")
full = waits(LAB / "paper_replay_fullwait.json")
ok("默认 replay 是压缩版", main <= 10, main)
ok("fullwait 保留为对照", full >= 20, full)

d = json.loads((LAB / "paper_replay.json").read_text(encoding="utf-8"))
ok("默认 replay id 标 shortwait", "shortwait" in d["replay_id"], d["replay_id"])

print(f"\n==== I24 replay 唯一入口测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
