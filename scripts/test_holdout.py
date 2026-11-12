"""I8e：H 清单/泄漏扫描测试。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.holdout import (HoldoutManifest, audit_leakage,  # noqa: E402
                             freeze_manifest)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


tmp = Path(tempfile.mkdtemp(prefix="upi_holdout_"))
clean = tmp / "clean"
(clean / "Assets").mkdir(parents=True)
(clean / "Assets" / "Game.cs").write_text(
    "public class Game { void Update() { } }\n", encoding="utf-8")
ok("干净工程无泄漏", audit_leakage(clean) == [])

dirty = tmp / "dirty"
(dirty / "Assets").mkdir(parents=True)
(dirty / "Assets" / "Game.cs").write_text(
    "// legacy_ui is a case_id oracle\npublic class Game { }\n", encoding="utf-8")
hits = audit_leakage(dirty)
ok("脏工程有泄漏", len(hits) >= 3, hits)
ok("泄漏含行号", all(h.line > 0 for h in hits))

m = freeze_manifest("H1", ["c31", "c32"], clean)
ok("H 清单冻结", m.holdout_id == "H1" and len(m.case_ids) == 2)
try:
    m.assert_not_tuning(["c31"])
    ok("H 被调参时拒绝", False)
except ValueError:
    ok("H 被调参时拒绝", True)

print(f"\n==== H 清单/泄漏测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
