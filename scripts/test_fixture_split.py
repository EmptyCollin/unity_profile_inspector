"""§8.1 夹具切分隔离闸的测试。

这个闸自己就是"防假闸"的东西，所以测试的第一要务不是"正例通过"，
而是**证明它在违规时真的会报错**。本轮实测教训：我第一版写的束解析器
与真实结构不匹配，返回空 dict，于是 S2 的比较分支永不执行——
一个"永远通过"的假闸，比没有闸更危险，因为后面的人会以为这条防住了。
下面的反向用例（R 系列）就是专门用来让那种实现当场失败的。

构造全部走临时目录 + 模块属性重定向，不读也不改真实仓库状态。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_fixture_split as G          # noqa: E402
from upi.tooling import checklist as CL   # noqa: E402

PASS, FAIL = [], []


def ok(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name + ("" if cond else f"  ← {detail[:170]}"))


def has(code: str, kw: str = "") -> bool:
    return any(code in f and (not kw or kw in f) for f in G.FAIL)


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp())


SPEC0 = {
    "fixtures": [
        {"id": "F-CPU-01", "enabled": True, "scale": 100, "dynamic": True, "holdout": 0},
        {"id": "F-GPU-01", "enabled": True, "scale": 40, "dynamic": True, "holdout": 0},
        {"id": "F-ANIM-01", "enabled": True, "scale": 128, "dynamic": True, "holdout": 1},
        {"id": "F-LOAD-01", "enabled": True, "scale": 400, "dynamic": True, "holdout": 1},
        {"id": "CLEAN", "enabled": False, "scale": 0, "dynamic": True, "holdout": 0},
    ]
}


def with_spec(mutate=None) -> Path:
    """写一份规格副本并把 checklist 的路径指过去（闸经 CL 取数，一处即生效）。"""
    d = json.loads(json.dumps(SPEC0))
    if mutate:
        mutate(d)
    p = tmpdir() / "fixture_spec.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    CL.FIXTURE_SPEC = p
    return p


def bundle_with(medians: dict[str, dict[str, float]]) -> Path:
    """造标定束：结构必须与真实束一致（windows[i].metrics[m].median），
    否则测的是我的解析器而不是闸。"""
    wins = []
    for fx, m in medians.items():
        # 三个窗口才能过 robust 的 n>=3 门槛；每窗内 median 相同 ⇒ 窗口间中位=该值
        wins.append({fx: {k: {"median": v, "mad": 0.0, "n": 20, "unique": 1}
                          for k, v in m.items()}})
    d = {"fixtures": {fx: {"windows": [
        {"metrics": {k: {"median": v, "mad": 0.0, "n": 20, "unique": 1}
                     for k, v in m.items()}}
        for _ in range(4)]} for fx, m in medians.items()}}
    p = tmpdir() / "multiwin_full.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    G.CAL = p
    return p


def thresholds_with(vals: dict[str, float]) -> Path:
    d = tmpdir()
    body = {"source": "t", "thresholds": {
        k: {"metric": k, "clean_median": v, "clean_band": 1.0, "k_sigma": 3.0,
            "min_magnitude": 0.0, "band_basis": "between_windows(n=4)"}
        for k, v in vals.items()}}
    (d / "thresholds_test.json").write_text(json.dumps(body), encoding="utf-8")
    G.BASELINES = d
    return d


def refs_with(names: list[str], bodies: dict[str, str] | None = None) -> Path:
    d = tmpdir()
    for n in names:
        (d / n).write_text((bodies or {}).get(n, "// x"), encoding="utf-8")
    G.REFS = d
    return d


CLEAN_M = {"cpuFrameTimeMs": 1.0, "gc_alloc_per_frame": 8000.0, "triangles": 2000.0}
ANIM_M = {"cpuFrameTimeMs": 1.7, "gc_alloc_per_frame": 8000.0, "triangles": 2000.0}


# ─────────── ① 三态建模（本轮从"靠记性"改为"靠类型"的成果） ───────────
with_spec()
sp = CL.split_of()
ok("①负样本自成一类，不落入 tuning",
   sp["CLEAN"] == CL.CLEAN_ID, json.dumps(sp, ensure_ascii=False))
ok("①缺陷侧分组不含负样本",
   "CLEAN" not in CL.defect_split() and len(CL.defect_split()) == 4,
   str(CL.defect_split()))
ok("①切分为 2 tuning + 2 holdout",
   sorted(CL.defect_split().values()) == ["holdout", "holdout", "tuning", "tuning"])

# ─────────── ② S1 正例与三类违规 ───────────
G.FAIL.clear(); G.check_split(); ok("②S1 合规规格不误报", not G.FAIL, str(G.FAIL))

G.FAIL.clear()
with_spec(lambda d: [f.update(holdout=0) for f in d["fixtures"] if f["id"] == "F-LOAD-01"])
G.check_split()
ok("②S1 拦住失衡切分（3+1）", has("S1", "2 tuning + 2 holdout"), str(G.FAIL))

G.FAIL.clear()
with_spec(lambda d: [f.pop("holdout", None) for f in d["fixtures"]])
r = G.check_split()
ok("②S1 缺字段即拒绝且不默认成 tuning",
   r == {} and has("S1", "不做默认猜测"), str(G.FAIL))

G.FAIL.clear()
with_spec(lambda d: [f.update(enabled=True) for f in d["fixtures"] if f["id"] == "CLEAN"])
G.check_split()
ok("②S1 拦住负样本被启用", has("S1", "enabled=true"), str(G.FAIL))

G.FAIL.clear()
with_spec(lambda d: d["fixtures"].remove(
    next(f for f in d["fixtures"] if f["id"] == "CLEAN")))
G.check_split()
ok("②S1 拦住缺负样本（FP 率无从测量）", has("S1", "负样本"), str(G.FAIL))

G.FAIL.clear()
CL.FIXTURE_SPEC = tmpdir() / "nope.json"
G.check_split()
ok("②S1 规格文件缺失也报错（不静默通过）", has("S1", "不存在"), str(G.FAIL))

# ─────────── ③ S2：正向可追溯 + 反向泄漏升级 ───────────
with_spec()
sp = CL.split_of()
bundle_with({"CLEAN": CLEAN_M, "F-ANIM-01": ANIM_M,
             "F-LOAD-01": ANIM_M, "F-CPU-01": CLEAN_M, "F-GPU-01": CLEAN_M})

# (a) 阈值就是 CLEAN 的重算值 ⇒ 合规
G.FAIL.clear()
thresholds_with({"cpuFrameTimeMs": 1.0})
G.check_thresholds(sp)
ok("③S2 可回溯至 CLEAN 不误报", not G.FAIL, str(G.FAIL))

# (b) 阈值 = 留出夹具中位且 ≠ CLEAN ⇒ 必须抓（R 系列，假闸试金石）
G.FAIL.clear()
thresholds_with({"cpuFrameTimeMs": 1.7})
G.check_thresholds(sp)
ok("R ③S2 拦住『阈值取自留出夹具』",
   has("S2", "无法由 CLEAN 重算") and has("S2", "留出夹具"), str(G.FAIL))

# (c) 阈值 = 凭空数字 ⇒ 至少报"来源不明"
G.FAIL.clear()
thresholds_with({"cpuFrameTimeMs": 3.33})
G.check_thresholds(sp)
ok("③S2 拦住来源不明（手工回调）", has("S2", "无法由 CLEAN 重算"), str(G.FAIL))

# (d) 标定束缺失 ⇒ 报"不可追溯"，绝不静默通过（假闸的另一条路径）
G.FAIL.clear()
keep = G.CAL
G.CAL = tmpdir() / "nope.json"
thresholds_with({"cpuFrameTimeMs": 1.0})
G.check_thresholds(sp)
ok("R ③S2 束缺失时报不可追溯而非通过",
   has("S2", "不可追溯") or has("S2", "缺 CLEAN"), str(G.FAIL))
G.CAL = keep

# (e) CLEAN 与留出同值的指标：不做无据的泄漏指控（防误报）
#     gc_alloc_per_frame 在 CLEAN 与 F-ANIM-01 都是 8000 —— 等于留出中位
#     是巧合，因为该值可由 CLEAN 解释，必须判合规。
G.FAIL.clear()
thresholds_with({"gc_alloc_per_frame": 8000.0})
G.check_thresholds(sp)
ok("③S2 不把跨夹具同值当泄漏（防假阳性逼人绕闸）", not G.FAIL, str(G.FAIL))

# ─────────── ④ S3：留出实现不外泄 ───────────
G.FAIL.clear()
refs_with(["F_CPU_01_defect.cs"])
G.check_refs()
ok("④S3 调参侧切片不误报", not G.FAIL, str(G.FAIL))

G.FAIL.clear()
refs_with(["F_CPU_01_defect.cs", "F_ANIM_01_defect.cs"])
G.check_refs()
ok("R ④S3 拦住留出切片入码（下划线形态也要认出）",
   has("S3", "留出夹具的实现"), str(G.FAIL))

G.FAIL.clear()
refs_with(["F_CPU_01_defect.cs"],
          {"F_CPU_01_defect.cs": "// 引用 Scene_F-LOAD-01.unity"})
G.check_refs()
ok("④S3 拦住切片内引用留出场景", has("S3", "场景引用"), str(G.FAIL))

G.FAIL.clear()
G.REFS = tmpdir() / "nope"
G.check_refs()
ok("④S3 参照目录缺失时报错", has("S3", "不存在"), str(G.FAIL))

# ─────────── ⑤ 口径同源：必须复用生成侧函数 ───────────
ok("⑤S2 复用阈值生成侧的 between_windows（非另写实现）",
   getattr(G, "_mw_between_windows", None) is not None
   and G._mw_between_windows.__module__ == "m1_multiwin",
   getattr(G, "_MW_IMPORT_ERR", "未导入"))
ok("⑤S2 复用生成侧 robust",
   getattr(G, "_mw_robust", None) is not None
   and G._mw_robust.__module__ == "m1_multiwin")
# 束解析必须真取到数：取空 = 比较分支永不执行 = 假闸（本轮真实踩过）
b = G._bundle_stats()
ok("R ⑤束解析返回非空且含 CLEAN 指标",
   bool(b) and "CLEAN" in b and len(b["CLEAN"]) > 0,
   json.dumps({k: len(v) for k, v in b.items()}))

print(f"\n夹具切分隔离闸测试：{len(PASS)} 通过 / {len(FAIL)} 失败")
for f in FAIL:
    print("  ✗", f)
sys.exit(1 if FAIL else 0)
