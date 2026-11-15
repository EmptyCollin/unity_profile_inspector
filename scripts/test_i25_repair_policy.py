"""I25：生产 blacklist 查询/提议。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.finding_blacklist import BlacklistStore  # noqa: E402
from upi.core.repair_policy import (fingerprint_of, propose_on_failure,  # noqa: E402
                                    should_skip, signature_of)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
store = BlacklistStore(TMP / "bl.db")
finding = {"mechanism": "hot_function:ChromaGame.Update", "metric": "frame_time"}

ok("signature 不含类名", "ChromaGame" not in signature_of(finding),
   signature_of(finding))
fp = fingerprint_of(finding, window="open_shop", metric="frame_time",
                    protocol_fingerprint="p1", project_scope="proj")
store.upsert(fp, status="active", reason="no gain")
ok("active 命中", should_skip(store, finding, window="open_shop",
                            metric="frame_time", protocol_fingerprint="p1",
                            project_scope="proj") is not None)
ok("window 不同不命中", should_skip(store, finding, window="resolve",
                                  metric="frame_time", protocol_fingerprint="p1",
                                  project_scope="proj") is None)

f2 = {"mechanism": "hot_function:ChromaGame.OpenShop", "metric": "gc_alloc"}
fid = propose_on_failure(store, f2, window="open_shop", metric="gc_alloc",
                         protocol_fingerprint="p1", reason="3 failures",
                         attempts=3, project_scope="proj")
ok("proposed 不命中", should_skip(store, f2, window="open_shop",
                                metric="gc_alloc", protocol_fingerprint="p1",
                                project_scope="proj") is None)
ok("proposed 已记录", store.get(fid).status == "proposed")
ok("signature 机制级", signature_of(f2) == "hot_function:gc_alloc",
   signature_of(f2))
nested = {"signature": {"mechanism": "per_frame_alloc", "metric": "gc_alloc"}}
ok("Pydantic Issue 嵌套 signature",
   signature_of(nested) == "per_frame_alloc:gc_alloc", signature_of(nested))

print(f"\n==== I25 生产 blacklist policy 测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
