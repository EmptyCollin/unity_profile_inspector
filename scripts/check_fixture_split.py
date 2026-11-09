"""§8.1 夹具切分强制隔离闸（B6 的落地件）。

§8.1 的原文约束是"**holdout 夹具的缺陷实现细节不进入任何阈值调参过程**"。
在此之前，这条只写在 `fixture_spec.json` 的 `holdout` 字段里，**没有任何代码
读它**（grep 实况：`holdout` 只出现在阈值判据名与本文档中）——所以约束的真实
执行方式是"我记得别用"。人记性会坏，闸不会。

三条可证伪的检查（任一不过即退出非零，供上线流程与报告引用）：

S1 切分合法：规格里 4 个缺陷夹具必须是 2 tuning + 2 holdout，且 CLEAN 存在
   并标 enabled=false（它是负样本，不该被当缺陷夹具跑）。
   —— 比例失衡（比如 3+1）会让 holdout 侧样本过少，gap 数值本身就无意义。

S2 阈值来源纯净（两个判据，一正一反）：
   正向——`baselines/thresholds_*.json` 每项的 `clean_median` 必须**能由标定束
   的 CLEAN 条目按生成侧同一算法重算得到**（容差取生成侧的 round(…,4)）。
   重算不出来 = 来源不明（手工回调过，或标定束换了而阈值没重生成），即违规。
   反向——在此之上，若该值又**恰好等于某个留出缺陷夹具的中位**，报错升级为
   "阈值取自留出实现"：那就是把答案写进了配置（§8.1 禁止）。
   ⚠️ 判据顺序不能反：单看"等于留出中位"会大量误报（见下方能力上限）。

S3 留出实现不外泄：Fix 侧的切片参照（`_refs/*.cs`）与 playbook 模板
   **只能引用 tuning 夹具的缺陷实现**；holdout 夹具的代码不得作为模板入码。
   —— holdout 的全部价值在于"模型没见过它的写法"；把它当模板，
      holdout 就变成了 tuning，体检表第 1 项失去意义。

为什么做成独立脚本而不是单测：它检查的是**仓库当前状态**（阈值文件、夹具规格、
参照切片），这些会随标定重跑而变；写进单测就等于把某一次的具体数值钉死。

S2 的能力上限（**必须知道，否则会把这个闸当成万能**，反向实测得出）：
"等于留出中位"能否被抓，取决于该指标在 CLEAN 与留出夹具上**是否可区分**：
  · 可区分（实测 animator_process_animations_ns CLEAN=0 / F-ANIM-01=361950，
    cpuFrameTimeMs、monoUsedBytes、totalAllocatedBytes 同类）⇒ 篡改必被抓；
  · **同值不可区分**（实测 gc_alloc_per_frame、triangles、vertices、
    setPassCalls、drawCalls、batches、totalReservedBytes、available 在两侧
    完全相等——注入的缺陷没改变这些量）⇒ 该处阈值"来自 CLEAN"与"来自留出"
    在数值上不可分辨，闸抓不到，S3（切片/场景引用不泄漏）才是这类指标的
    实际防线。
所以 S2 的准确表述是"阈值必须可回溯至 CLEAN 或为跨夹具同值"，
不是"任何留出泄漏都能检出"。报告与文档均按此措辞，不夸口。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from upi.tooling import checklist as CL  # noqa: E402

# 路径与判据都从 checklist 模块属性取（不是本地副本名），
# 这样测试只要改 CL.FIXTURE_SPEC 就能整体隔离到临时目录。
FIXTURE_SPEC = None  # 见 _spec_path()，不在此固化


def _spec_path() -> Path:
    return CL.FIXTURE_SPEC


def _split() -> dict[str, str]:
    return CL.split_of()


def _defect_split() -> dict[str, str]:
    return CL.defect_split()

# 复用阈值**生成侧**的同一对函数（不是另写等价实现）：
# 校验与生成只要口径不同，S2 就退化成假闸——假闸比没闸更危险。
try:
    from m1_multiwin import between_windows as _mw_between_windows
    from m1_multiwin import robust as _mw_robust
except Exception as _e:  # noqa: BLE001
    _mw_between_windows = _mw_robust = None
    _MW_IMPORT_ERR = str(_e)[:120]
else:
    _MW_IMPORT_ERR = ""

CAL = ROOT / "calibration" / "multiwin_full.json"
BASELINES = ROOT / "baselines"
REFS = ROOT / "upi" / "agents" / "_refs"

FAIL: list[str] = []
INFO: list[str] = []


def fail(code: str, msg: str) -> None:
    FAIL.append(f"{code} {msg}")


# ─────────────── S1 切分合法性 ───────────────
def check_split() -> dict:
    spec_p = _spec_path()
    if not spec_p.exists():
        fail("S1", f"夹具规格文件不存在：{spec_p.name}")
        return {}
    spec = json.loads(spec_p.read_text(encoding="utf-8"))
    fl = spec.get("fixtures") or []
    ids = {f.get("id"): f for f in fl}
    sp = _split()
    if not sp:
        fail("S1", "holdout 字段缺失或值非 0/1 ⇒ 切分不可用，不做默认猜测")
        return {}
    # 三态建模：CLEAN 的类型就是 "clean"，与 tuning/holdout 平级（见
    # checklist.split_of）。所以这里按**类型**取缺陷夹具，不按 id 字符串排除——
    # 按名字排除等于把防错责任交给记性，加一个负样本就要记得改一处。
    defects = _defect_split()
    n_tuning = sum(1 for v in defects.values() if v == "tuning")
    n_hold = sum(1 for v in defects.values() if v == "holdout")
    if (n_tuning, n_hold) != (2, 2):
        fail("S1", f"缺陷夹具切分应为 2 tuning + 2 holdout，实测 {n_tuning} + {n_hold}"
                   "（失衡会让 gap 数值本身无统计意义）")
    n_clean = sum(1 for v in sp.values() if v == CL.CLEAN_ID)
    if n_clean != 1:
        fail("S1", f"规格应有且仅有 1 个负样本（{CL.CLEAN_ID}），实测 {n_clean} 个"
                   "——缺它则 FP 率无从测量（§8.1 末条）")
    for cid, f in ((k, v) for k, v in ids.items() if k == CL.CLEAN_ID):
        if f.get("enabled"):
            fail("S1", f"{cid} 被标 enabled=true——它是负样本，不应作为缺陷夹具参与回放")
    INFO.append(f"S1 切分：tuning={sorted(k for k, v in defects.items() if v=='tuning')} "
                f"holdout={sorted(k for k, v in defects.items() if v=='holdout')}")
    return sp


# ─────────────── S2 阈值来源纯净 ───────────────
# 阈值由 scripts/m1_multiwin.py 的 do_merge() 生成，口径是
#   A 档：between_windows(windows)[metric] → robust(各窗口 median 的序列)["median"]
#   A2 档：各窗口 a2[短键] 序列（≥50 个）的中位 → 窗口间 robust
# 校验**必须复用同两个函数**，不能在这里另写一套聚合。
# 教训（本轮实测）：我先前自己写了个"把所有数字挑出来取中位"的解析器，
# 与真实束结构（windows[i].metrics[m].median）不匹配，返回空 dict
# ⇒ 比较分支永不执行 ⇒ S2 变成一个"永远通过"的假闸——而它正是本闸唯一
# 能拦住"阈值被调到刚好框住留出样本"的一条，假闸比没有闸更危险，
# 因为它会让后面的人以为这条已经防住了。
_A2_KEYS = (("gc", "gc_alloc_per_frame"), ("anim", "animator_process_animations_ns"),
            ("graph", "animator_process_graph_ns"), ("mem", "total_used_per_frame"))


def _bundle_stats() -> dict[str, dict[str, float]]:
    """标定束 → {夹具: {指标: 窗口间中位数}}，与阈值生成完全同口径。"""
    if not CAL.exists():
        return {}
    d = json.loads(CAL.read_text(encoding="utf-8"))
    out: dict[str, dict[str, float]] = {}
    for fx, blk in (d.get("fixtures") or {}).items():
        wins = blk.get("windows") or []
        if not wins:
            continue
        m: dict[str, float] = {}
        # A 档：复用生成侧 between_windows（内含 robust 与 n≥3 门槛）
        for key, st in _mw_between_windows(wins).items():
            m[key] = float(st["median"])
        # A2 档：逐帧序列先取窗口内中位，再做窗口间 robust
        import statistics
        for short, name in _A2_KEYS:
            vals = []
            for w in wins:
                seq = (w.get("a2") or {}).get(short, [])
                if len(seq) >= 50:
                    vals.append(float(statistics.median(seq)))
            if len(vals) >= 3:
                m[name] = float(_mw_robust(vals)["median"])
        out[fx] = m
    return out





def check_thresholds(sp: dict) -> None:
    files = sorted(BASELINES.glob("thresholds_*.json"))
    if not files:
        fail("S2", "baselines/ 下无阈值文件，无法校验阈值来源")
        return
    med = _bundle_stats()
    if not med:
        fail("S2", f"标定束 {CAL.name} 不存在或解析为空 ⇒ 阈值来源不可追溯")
        return
    if "CLEAN" not in med:
        fail("S2", "标定束缺 CLEAN 条目，无法建立阈值的正向来源")
        return
    clean_stats = med["CLEAN"]
    hold = {k for k, v in CL.defect_split().items() if v == "holdout"}
    traced = leak = untraceable = 0
    for fp in files:
        d = json.loads(fp.read_text(encoding="utf-8"))
        for metric, t in (d.get("thresholds") or {}).items():
            cm = t.get("clean_median")
            if not isinstance(cm, (int, float)):
                continue
            # ── 正向判据：阈值本就只能来自 CLEAN 噪声标定，所以"能由 CLEAN
            #   重算出来"才是合规证据。容差按生成侧口径 round(median, 4)
            #   （实测 cpuFrameTimeMs 存 1.3376、重算 1.33755 即此差）。
            from_rounded = (metric in clean_stats
                            and abs(round(clean_stats[metric], 4) - float(cm)) < 1e-9)
            if from_rounded:
                traced += 1
                continue
            untraceable += 1
            fail("S2", f"{fp.name}:{metric} clean_median={cm} **无法由 CLEAN 重算得到**"
                       f"（CLEAN 同项={clean_stats.get(metric, '缺失')}）"
                       "⇒ 来源不明：可能是手工回调过，或标定束已换而阈值未重生成"
                       "（§8.1 禁止为检出某缺陷而回调阈值）")
            # ── 反向判据：只有"等于某夹具中位、且无法由 CLEAN 解释"才算泄漏。
            #   教训（本轮实测）：最初写成"等于留出夹具中位即违规"，撞上
            #   triangles/vertices/setPassCalls 三项**假阳性**——注入的缺陷
            #   没有改变场景几何与 SetPass 结构，这些量在 CLEAN 与留出夹具上
            #   **本来就相等**。数值相等 ≠ 数值来自该夹具；拿巧合当因果，
            #   会把合规的阈值文件判成违规，逼人绕过这个闸。
            for fx in sorted(hold):
                hv = med.get(fx, {}).get(metric)
                if hv is not None and abs(float(hv) - float(cm)) < 1e-9:
                    leak += 1
                    fail("S2", f"{fp.name}:{metric} clean_median={cm} 等于留出夹具 {fx} "
                               "的中位，且无法由 CLEAN 解释 ⇒ 阈值取自留出实现（§8.1 违规）")
    INFO.append(f"S2 阈值来源：{len(files)} 份文件，{traced} 项可回溯至 CLEAN，"
                f"{untraceable} 项来源不明，{leak} 项疑似取自留出夹具")


# ─────────────── S3 留出实现不外泄 ───────────────
def check_refs() -> None:
    """参照切片只能来自 tuning 夹具。

    判据用夹具 id 的**编号形态**（F-XXX-NN 前缀），因为 `_refs/` 的文件名
    就是 `<夹具id>_defect.cs` 这一约定；同时扫 playbook 模板里是否内联了
    holdout 夹具的代码特征串。
    """
    if not REFS.exists():
        fail("S3", f"参照目录不存在：{REFS}")
        return
    hold_ids = {k for k, v in CL.defect_split().items() if v == "holdout"}

    # ⚠️ 形态陷阱：夹具 id 是连字符（F-ANIM-01），而 `_refs/` 的文件名约定用
    #   下划线（F_CPU_01_defect.cs）——直接拿 id 去 match 文件名**永不命中**，
    #   S3 会退化成一个"永远通过"的假闸（本轮实测：旧写法对唯一文件返回 []，
    #   看似正常，实则放任何留出切片进来都不会报）。
    #   归一化成同一形态再比，是唯一安全的做法。
    def norm(s: str) -> str:
        return s.replace("_", "-").upper()

    hold_norm = {norm(h) for h in hold_ids}

    names = sorted(p.name for p in REFS.iterdir() if p.suffix == ".cs")
    leaked = []
    for n in names:
        stem = norm(Path(n).stem)               # F_CPU_01_defect → F-CPU-01-DEFECT
        if any(stem.startswith(h + "-") or stem == h for h in hold_norm):
            leaked.append(n)
    if leaked:
        fail("S3", f"留出夹具的实现被作为修复模板入码：{leaked}"
                   "（holdout 沦为 tuning，体检表第 1 项失去意义）")
    else:
        INFO.append(f"S3 参照切片：{names}，均属调参侧夹具"
                    f"（留出侧 {sorted(hold_ids)} 无切片入码）")

    # playbook 表里也不得内联留出夹具的代码。判据同样归一化，并**只认带夹具
    # 前缀的标识符**（nodes.py 里出现 "F-ANIM-01" 是数据不是模板，不该算违规——
    # 所以检查的是切片内容而非源码文本，见下）。
    slices: list[tuple[str, str]] = []
    for p in sorted(REFS.iterdir()):
        if p.suffix in (".cs", ".txt", ".json"):
            try:
                slices.append((p.name, p.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError):
                fail("S3", f"参照切片 {p.name} 无法读取，来源不可追溯")
    for name, body in slices:
        for code, scene in (("F-ANIM-01", "Scene_F-ANIM-01"), ("F-LOAD-01", "Scene_F-LOAD-01")):
            if code.replace("-", "_") in body or code in body or scene in body:
                fail("S3", f"参照切片 {name} 含留出夹具 {code} 的实现/场景引用"
                           "（留出实现细节不得进入任何调参过程，§8.1）")


if __name__ == "__main__":
    sp = check_split()
    if sp:
        check_thresholds(sp)
        check_refs()
    for line in INFO:
        print("  ✓", line)
    if FAIL:
        print(f"\n夹具切分隔离校验**未通过**（{len(FAIL)} 项）：")
        for f in FAIL:
            print("  ✗", f)
        sys.exit(1)
    print("\n夹具切分隔离校验通过（S1 切分 / S2 阈值来源 / S3 实现不外泄）")
    sys.exit(0)
