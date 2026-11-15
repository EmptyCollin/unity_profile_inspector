"""I25：黑名单指纹库。"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.finding_blacklist import (BlacklistFingerprint,  # noqa: E402
                                        BlacklistStore)

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


TMP = Path(tempfile.mkdtemp())
store = BlacklistStore(TMP / "bl.db")
fp = BlacklistFingerprint("per_frame_texture_alloc", "open_shop",
                          "gc_alloc", "proto1", "test_project")

store.upsert(fp, status="proposed", reason="2 attempts no gain")
ok("proposed 不命中", store.lookup(fp) is None)
ok("proposed 可查", store.get(fp.fingerprint_id()).status == "proposed")

store.set_status(fp.fingerprint_id(), "active", actor="human", reason="approve")
ok("active 命中", store.lookup(fp) is not None)
ok("协议不同不命中",
   store.lookup(BlacklistFingerprint("per_frame_texture_alloc", "open_shop",
                                     "gc_alloc", "proto2", "test_project")) is None)

store.set_status(fp.fingerprint_id(), "retired", actor="human", reason="cleanup")
ok("retired 不命中", store.lookup(fp) is None)

fp2 = BlacklistFingerprint("other", "load", "frame_time", "proto1", "p")
store.upsert(fp2, status="active", ttl=1)
time.sleep(0.05)
ok("TTL 到期不命中", store.lookup(fp2, now=time.time() + 5) is None)
ok("TTL 状态 expired", store.get(fp2.fingerprint_id()).status == "expired")

print(f"\n==== I25 黑名单测试: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
raise SystemExit(0 if not FAIL else 1)
