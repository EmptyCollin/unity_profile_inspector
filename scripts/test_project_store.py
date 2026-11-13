"""I10b：学习资产按项目隔离（无全局库）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import events as E  # noqa: E402
from upi.core.fingerprint import FingerprintStore  # noqa: E402
from upi.core.knowledge import KnowledgeStore, default_path  # noqa: E402
from upi.core.project_store import (fingerprint_db, knowledge_db,  # noqa: E402
                                    project_knowledge_dir)

PASS, FAIL = [], []


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp(prefix="upi_pstore_"))
E.isolate(TMP / "runs")

pa, pb = knowledge_db("proj-a"), knowledge_db("proj-b")
ok("两项目知识库路径不同", pa != pb, f"{pa} vs {pb}")
ok("路径在项目目录下", "projects/proj-a/knowledge" in str(pa).replace("\\", "/"),
   str(pa))
ok("指纹库也在项目目录", "proj-a" in str(fingerprint_db("proj-a")))
ok("默认无全局 knowledge/patterns.db",
   "projects/proj-a/knowledge" in str(default_path("proj-a")).replace("\\", "/"))

ka = KnowledgeStore(pa)
ka.record_outcome("pat-1", "run-a", "scene", "tier", "resolved", profile="proj-a")
ka.close()
kb = KnowledgeStore(pb)
ok("B 项目看不到 A 的历史", kb.rates("pat-1")["n_success"] == 0, kb.rates("pat-1"))
kb.close()
ka = KnowledgeStore(pa)
ok("A 项目能读回自己的历史", ka.rates("pat-1")["n_success"] == 1, ka.rates("pat-1"))
ka.close()

fa = FingerprintStore(fingerprint_db("proj-a"))
fb = FingerprintStore(fingerprint_db("proj-b"))
fa.upsert(__import__("upi.core.fingerprint", fromlist=["FingerprintRecord"]).FingerprintRecord(
    fingerprint_id="fp-a", features={"metric": "m", "shape": "step",
                                     "kind": "sample", "data_tier": "A2",
                                     "rel_bucket": -1},
    pattern_id="learned:a", root_cause="a", playbook_id="", confidence=0.5,
    source="learned"))
ok("指纹 A 写入", len(fa.all()) == 1)
ok("指纹 B 独立（空）", len(fb.all()) == 0)
ok("项目目录函数稳定", project_knowledge_dir("proj-a") == pa.parent)

print(f"\n==== 项目隔离学习库测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
