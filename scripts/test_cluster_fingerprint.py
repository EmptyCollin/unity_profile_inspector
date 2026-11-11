"""I2：事件聚类 + 跨 run 复现率 + 指纹库（离线）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.cluster import cluster_events, match_across_runs  # noqa: E402
from upi.core.fingerprint import FingerprintFeatures, FingerprintStore  # noqa: E402
from upi.core.fingerprint import features_from_cluster as fp_features  # noqa: E402
from upi.core.probe_events import ProbeEvent  # noqa: E402

PASS, FAIL = [], []


def ok(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def breach(rel: int, metric="frame_time_ms", after=None, value=5.0):
    return ProbeEvent(kind="breach", metric=metric, rel_frame=rel, value=value,
                      ring_before=[1.0] * 5,
                      ring_after=after or [5.0, 4.0, 1.0, 1.0, 1.0],
                      data_tier="A4")


def sample(rel: int, metric="drawCalls", series=None, tier="A2"):
    vals = series if series is not None else [47.0] * 20
    return ProbeEvent(kind="sample", metric=metric, rel_frame=rel,
                      series=vals, value=0.0, data_tier=tier)


# ── run 内聚类 ──
evs = [breach(10), breach(15), breach(100)]
cs = cluster_events(evs, merge_gap=30)
ok("同指标相邻 breach 合成一个 cluster", len(cs) == 2 and cs[0].count == 2)
ok("远距离 breach 分开", cs[1].first_rel == 100 and cs[1].count == 1)
ok("breach 波形 spike", cs[0].shape == "spike", cs[0].shape)

ss = [sample(10), sample(40)]
cs2 = cluster_events(ss, merge_gap=40)
ok("sample 窗口合并", len(cs2) == 1 and cs2[0].count == 2)
ok("sample 波形 step", cs2[0].shape == "step", cs2[0].shape)

per = [breach(10), breach(20), breach(30)]
cs3 = cluster_events(per, merge_gap=20)
ok("等间隔 3 次 → periodic", len(cs3) == 1 and cs3[0].shape == "periodic", cs3[0].shape)

ok("空输入返回空", cluster_events([]) == [])
ok("不同指标分开", len(cluster_events([breach(10), breach(10, "gc_alloc_per_frame")])) == 2)

# ── 跨 run 复现率 ──
r1 = cluster_events([breach(10), breach(100)], merge_gap=30)
r2 = cluster_events([breach(11), breach(101)], merge_gap=30)
r3 = cluster_events([breach(200)], merge_gap=30)
m = match_across_runs([r1, r2])
ok("同相对帧 ±1 匹配", len(m) == 2 and all(x.reproduction_rate == 1.0 for x in m))
ok("稳定判定", all(x.is_stable for x in m))
m3 = match_across_runs([r1, r2, r3], stable_rate=2 / 3)
first = [x for x in m3 if x.cluster.metric == "frame_time_ms" and x.cluster.first_rel == 10][0]
ok("3 run 中 2 次命中 → 2/3", first.reproduction_rate == round(2 / 3, 3)
   and first.is_stable)
m4 = match_across_runs([r1, r2], rel_tol=0)
ok("容差 0 时 ±1 不匹配", len(m4) == 4 and all(x.reproduction_rate == 0.5 for x in m4),
   str([(x.cluster.first_rel, x.reproduction_rate) for x in m4]))

# ── 指纹库 ──
TMP = Path(tempfile.mkdtemp(prefix="upi_fp_"))
db = TMP / "fingerprints.db"
store = FingerprintStore(db)
n = store.seed_builtin()
ok("内置 pattern 入库", n == 6 and len(store.all()) == 6)
ok("ensure_seed 幂等", store.ensure_seed() == 0)

draw = cluster_events([sample(10)], merge_gap=40)[0]
f = fp_features(draw)
hit = store.route(f)
ok("drawCalls/step 命中内置", hit.hit and hit.record.pattern_id.endswith(":drawCalls:step"))
ok("命中跳过 LLM", hit.llm_required is False)
ok("命中计数 +1", store.get(hit.record.fingerprint_id).n_hits == 1)

unknown = FingerprintFeatures(metric="new_metric", shape="spike", data_tier="A2")
miss = store.route(unknown)
ok("未知指纹不命中且要求 LLM", (not miss.hit) and miss.llm_required)
ok("miss 计数进库", store.stats()["misses"] >= 1)

store.record_outcome(hit.record.fingerprint_id, True)
ok("成功计数", store.stats()["success"] == 1)

# 持久化：重开同一路径仍能读到
store2 = FingerprintStore(db)
ok("指纹库持久化", len(store2.all()) == 6)
ok("stats 有命中/未命中", store2.stats()["hits"] >= 1
   and store2.stats()["misses"] >= 1)

# 特征边界
ok("特征不含文件名/类名", "file" not in fp_features(draw).model_dump()
   and ".cs" not in str(fp_features(draw).model_dump()))

print()
if FAIL:
    print(f"==== 聚类/指纹测试: {len(PASS)} 通过, {len(FAIL)} 失败")
    for x in FAIL:
        print("  -", x)
    sys.exit(1)
print(f"==== 聚类/指纹测试: {len(PASS)} 通过, 0 失败")
