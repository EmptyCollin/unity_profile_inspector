"""M1 检测：读基线 → 对照 clean 变体 → 产出 Issue[]（带证据链）→ 写 trace。

判据（DESIGN §3 / AGENTS §1.2）：
  检出 = |median(fixture) − median(clean)| > k × noise_band(clean)      # 统计显著
       AND delta > min_magnitude                                        # 业务显著
       AND deviation_ratio 达标
  G17: 任一参与方 degenerate_distribution ⇒ 禁用相对判据，只走绝对门限并标注
  误报口径：CLEAN 自身对照自身应零检出（FP 直接可见）
  I10/G13：每个阶段写 TraceSpan，无 trace 的运行不进统计
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.config.project_profile import active_profile, load_profile, owner_profile_id  # noqa: E402
from upi.core.conflicts import run_pair, run_snapshot                    # noqa: E402
from upi.core.detector import DEFAULT_REGISTRY                    # noqa: E402
from upi.core.models import Evidence, Fingerprint, Issue, IssueSignature  # noqa: E402
from upi.tooling.trace import Tracer, environment_fingerprint     # noqa: E402

BASELINES = ROOT / "baselines"
CALDIR = ROOT / "calibration"
RUNS = ROOT / "runs"

# G7 可比性判据的字段清单（阈值源指纹 × 数据源指纹，逐字段相等才允许拿
# 阈值比数据）。提到模块级是为了让它**可被断言**：写在 main 里就是局部变量，
# test_profile_portability 只能去 grep 源码文本，那等于测字符串而不是测行为——
# 清单一旦改名或漏项，grep 版本照样"通过"。
# 不含 scene_guid：v0 它只反映来源文件内容，数据文件与阈值文件的哈希必然不同。
# 含 profile_id（阶段 7 批 2）：别的协议字段全对得上、但数据来自 A 工程而标定
# 来自 B 工程时，噪声带量的是另一个工程的抖动分布，拿它判本工程 = 无意义比较。
# environment_fingerprint 的哈希里虽然也混了工程标签，但那里不一致只能报
# "环境指纹不同"，看不出是驱动变了还是换了工程——两者处置完全不同（前者重采
# 即可，后者是拿错标定文件，重采一百次也不会变好），所以归属单列一个字段。
G7_COMPARE_FIELDS = ["replay_script_hash", "quality_tier", "warmup_samples",
                     "window_samples", "n_samples", "poll_rate", "data_tier",
                     "editor_mode", "scale_factor", "environment_fingerprint",
                     "profile_id"]


def g7_mismatches(a: Fingerprint, b: Fingerprint) -> list[str]:
    """两份协议指纹在 G7 判据上不一致的字段清单（空=可比）。

    单独成函数是为了让**判定本身**可被断言：测试给出"数据来自 A 工程、
    标定来自 B 工程"的两个指纹，直接问它拦不拦，而不是复刻一遍推导。
    """
    return [f for f in G7_COMPARE_FIELDS if getattr(a, f) != getattr(b, f)]


def latest_baseline() -> Path:
    return max(BASELINES.glob("baseline_*.json"), key=lambda p: p.stat().st_mtime)


def waterline_matrix(eval_subjects: list[str], fixs: dict, clean_stats: dict,
                     wln: list) -> tuple[dict, dict]:
    """选法 1 的面板：每个水位检测器的指标跑在**每个被测机制**上。

    为什么存在：注册表检测器硬绑夹具机制（subject="F-CPU-01"…），真工程的
    机制名不在其中 ⇒ 相对检出永远为空——"跨机制相对检出只服务夹具"正是
    这条耦合的产物。而判据本身（中位 vs 噪声基准 k·σ + 绝对阈 + 偏离倍数）
    是**指标级**的：gc_alloc_per_frame 的标定阈值评任何机制的 GC 都成立。
    所以面板 = 机制 × 指标，home（检测器原绑机制）与非 home 用同一 compare，
    不做特例开关（特例开关会随工程数增殖）。

    键形与分区（G18 等级归属的同型问题，实测后定）：
      · results 只放 **home 行**、按机制名键控——与历史 detection.json
        **逐字节兼容**（统计层 detections 表、状态机 find 循环都不动）；
      · panel_results 放**跨检行**、按 "机制::指标" 键控——筛查事实，
        不进 detector 检出率分母（混进去会把"检测器声称的能力"和
        "指标面板的顺带覆盖"算成一个数，正是 G18 拦的那种失真），
        也不进自动修复唤起（anchor_patterns 是 home 机制的代码模式，
        拿它归因别的机制会误导）。
    实测背书：夹具上开面板后 F-ANIM-01×totalAllocatedBytes 被跨检抓到
    （Δ3.76MB 过 2MB 标定地板）——这是面板的**价值**（瓶颈自然暴露），
    但它属筛查不属 DET-LOAD-SYNCPEAK 的战绩，分区后两不失真。
    """
    home_results: dict[str, dict] = {}
    panel: dict[str, dict] = {}
    for det in wln:
        for fid in eval_subjects:
            home = (fid == det.subject)
            st = fixs.get(fid)
            if st is None:
                row = {"subject": fid, "metric": det.metric, "detector": det.id,
                       "detector_version": det.version, "home": home,
                       "verdict": "insufficient_evidence",
                       "why": "本轮未采到该机制（缺窗口数据）"}
            else:
                issue, detail = compare(det, st, clean_stats)
                detail["detector"] = det.id
                detail["detector_version"] = det.version
                detail["subject"] = fid
                detail["home"] = home
                detail["window_n"] = (clean_stats.get(det.metric) or {}).get("n")
                if issue:
                    detail["issue_signature"] = issue.signature.model_dump()
                    detail["magnitude"] = issue.magnitude
                row = detail
            if home:
                home_results[fid] = row        # 历史契约：机制名键
            else:
                panel[f"{fid}::{det.metric}"] = row
    return home_results, panel


def compare(det, fix_stats: dict, clean_stats: dict) -> tuple[Issue | None, dict]:
    """返回 (Issue|None, 判定详情)。指标缺失 ⇒ insufficient_evidence（I2）。"""
    m = det.metric
    fx = fix_stats.get(m)
    cl = clean_stats.get(m)
    detail = {"metric": m, "fixture": fx and fx["median"], "clean": cl and cl["median"]}
    if not fx or not cl:
        detail["verdict"] = "insufficient_evidence"
        return None, detail

    delta = fx["median"] - cl["median"]
    signed = delta if det.direction == "higher_worse" else -delta
    degenerate = fx["degenerate_distribution"] or cl["degenerate_distribution"]

    if degenerate:
        stat_ok = None                      # 相对判据不可用（G17）
        biz_ok = signed > det.thresholds.min_magnitude
        verdict = "detected" if biz_ok else "not_found"
        detail.update(reason="degenerate_distribution ⇒ 仅绝对门限" if degenerate else "",
                      stat_ok=stat_ok, biz_ok=biz_ok)
    else:
        band = cl["noise_band_mad"]
        stat_ok = signed > det.thresholds.k_sigma * band
        biz_ok = signed > det.thresholds.min_magnitude
        ratio_ok = (fx["median"] / cl["median"]) > det.thresholds.deviation_ratio if cl["median"] > 1 else True
        verdict = "detected" if (stat_ok and biz_ok and ratio_ok) else "not_found"
        detail.update(stat_ok=stat_ok, biz_ok=biz_ok, ratio_ok=ratio_ok)

    ratio = (fx["median"] / cl["median"]) if cl["median"] not in (0, None) else float("inf")
    detail.update(delta=round(delta, 4), deviation_ratio=round(ratio, 3), verdict=verdict)

    if verdict != "detected":
        return None, detail

    sig = IssueSignature(dim=m, metric=m,
                         mechanism=det.anchor_patterns[0],           # 机制级，无标识符
                         threshold=det.thresholds.min_magnitude)
    ev = Evidence(tool="unity diagnostics get_performance + a2 sampler",
                  raw_ref=str(BASELINES), value=float(fx["median"]), unit=det.unit,
                  noise_band=float(cl["noise_band_mad"]),
                  data_tier=det.source_tier)      # type: ignore[arg-type]
    issue = Issue(signature=sig, link=det.link, evidence=[ev],
                  magnitude=round(delta, 4), deviation_ratio=round(ratio, 3),
                  explains_share_of_regression=1.0, state="confirmed",
                  low_confidence=False)
    return issue, detail


def apply_calibration(dets: list, cal: dict) -> int:
    """§8.1 落地：检测端阈值只接受标定文件覆盖，注册表里的初值一律作废。"""
    applied = 0
    th = cal.get("thresholds", {})
    for det in dets:
        t = th.get(det.metric)
        if not t:
            continue
        det.thresholds.min_magnitude = float(t["min_magnitude"])
        det.thresholds.deviation_ratio = float(t["deviation_ratio"])
        det.thresholds.k_sigma = float(t["k_sigma"])
        det.thresholds.threshold_source = "noise_calibration"
        det.calibration_file = Path(cal_path).name if (cal_path := cal.get("_path")) else "inline"
        det.calibration_degenerate = bool(t.get("degenerate"))
        applied += 1
    return applied


def latest_calibration(profile_id: str | None = None) -> dict | None:
    """取**本工程归属**的最新标定文件。

    阶段 8 选法 1 的前置修复：旧版按 mtime 全局取最新——接入第二个工程后，
    谁最后标定谁就是"最新"，另一个工程的检出会拿错阈值。G7 确实会拦下
    （profile_id 已在指纹里），但"每次换工程都撞墙再被拦"不是隔离，是串扰。
    归属判据与全仓同源（owner_profile_id）：新标定文件的 protocol 里有
    profile 键（protocol_block 写出）；历史文件没有 ⇒ 归内置档案——与
    "此前所有产物只可能来自内置工程"的事实判定一致（I1，不是猜默认值）。
    """
    pid = profile_id or active_profile().profile_id
    cps = sorted(BASELINES.glob("thresholds_*.json"),
                 key=lambda q: q.stat().st_mtime, reverse=True)
    for p in cps:
        try:
            cal = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if owner_profile_id(cal.get("protocol") or {}) == pid:
            cal["_path"] = str(p)
            return cal
    return None


def stats_from_windows(fx: dict) -> dict:
    """多窗口数据 → compare() 所需的统计形状。

    clean 与缺陷夹具**必须走同一个函数**，否则两相对照就是跨协议比较。
    窗口间 σ 才是 §3 定义的噪声带（旧单窗口 MAD 度量的是窗口内抖动）。
    """
    import statistics as st
    wins = fx.get("windows", [])
    out: dict[str, list[float]] = {}
    for w in wins:
        for k, m in (w.get("metrics") or {}).items():
            out.setdefault(k, []).append(float(m["median"]))
    # A2：每窗口取逐帧序列中位数，再跨窗口统计。
    # 双形态兼容（2026-11-08 摘要入库改造）：原始束给 a2[src]=逐帧列表，
    # 摘要束给 a2[src+"_median"]=已折好的窗口中位数。等价性实测逐字段一致
    # ——median 是等价输入。改造教训：等价性必须对**实际消费的函数**验证，
    # 上一轮我对着自写的兼容函数测通过就上，结果共享函数没改，两项指标
    # 直接退化成"证据不足"（A 级自欺，被回归当场抓住）。
    A2MAP = {"gc": "gc_alloc_per_frame", "anim": "animator_process_animations_ns",
             "graph": "animator_process_graph_ns", "mem": "total_used_per_frame"}
    for src, kname in A2MAP.items():
        vals = []
        for w in wins:
            a2 = w.get("a2") or {}
            seq = a2.get(src) or []
            if len(seq) >= 50:
                vals.append(float(st.median(seq)))
            elif isinstance(a2.get(src + "_median"), (int, float)):
                vals.append(float(a2[src + "_median"]))
        if vals:
            out[kname] = vals

    stats = {}
    for k, vals in out.items():
        if len(vals) < 3:
            continue
        med = st.median(vals)
        mad = st.median([abs(x - med) for x in vals]) * 1.4826
        uniq = len(set(vals))
        stats[k] = {"n": len(vals), "median": med, "noise_band_mad": mad, "unique": uniq,
                    "degenerate_distribution": uniq / len(vals) < 0.1 or mad == 0.0}
    return stats


def load_multwin() -> dict | None:
    """数据源优先级：摘要束（入库、31KB）→ 原始束（本地留档、23.9MB）。

    两者对 stats_from_windows 逐字段等价（median 是等价输入，实测验证）；
    摘要入库、原始可重生成，本机留着只是缓存。判据与阈值不受影响。"""
    fp = CALDIR / "multiwin_full_summary.json"
    if not fp.exists():
        fp = CALDIR / "multiwin_full.json"     # 兼容旧工作区
    if not fp.exists():
        return None
    d = json.loads(fp.read_text(encoding="utf-8"))
    d["_path"] = str(fp)
    return d


def fingerprint_from_protocol(proto: dict, source_path: str, env_fp: str,
                              windows_target: int = 4) -> Fingerprint:
    """从数据/阈值文件自带的 protocol 块构造 G7 指纹（B5 债：模型早已有、流程一直没接）。

    scene_guid v0 用来源文件内容 sha256 前 8 位（稳定可跨 run 复现；不用
    Python __hash__——那会随进程盐漂移，指纹本身就不可比了）。M2a 接上
    Unity 场景查询后换成真 guid，届时旧指纹自然失配、按 G7 禁止新旧混比，
    这正是指纹字段该有的行为。replay_script_hash v0="none"：回放基建（T2）
    未建，如实标注，不假装有回放。

    profile_id 取 protocol 块里声明的归属（没有则 owner_profile_id 判为
    内置档案）。这里**不取当前运行档案的值**：指纹要描述"这份数据是怎么
    采出来的"，不是"现在是谁在跑"——取当前值的话，换档案后重跑检测会让
    同一份旧数据的指纹跟着变，历史数据被改写成本源，G7 就失去了可比依据。
    quality_tier 的缺省回落同理走这条归属链（见下面 _qt），理由与上面一致。

    ⚠️ quality_tier 曾经写成 `proto.get("quality_tier", "urp-balanced")`：
      而采样端从来没把这个键写进 protocol（实测历史束只有 warmup /
      window_polls / poll_interval_s），所以数据侧与阈值侧**都落到同一个
      硬编码缺省**，G7_COMPARE_FIELDS 里的 quality_tier 于是变成恒等比较
      ——换了档位也不会被拦，看着有保护其实是假闸。现在采样端按档案写出
      真值，缺键（历史数据）回落到归属档案的档位；归属档案也解析不出来
      时记 "unknown"：两份都不知 ⇒ 相等放行（历史数据不作废），
      一知一不知 ⇒ 不等 ⇒ 拦下，方向严格是"更严"而不是放松。
    """
    import hashlib
    try:
        blob = Path(source_path).read_bytes()
    except Exception:  # noqa: BLE001
        blob = source_path.encode("utf-8", "replace")
    if "quality_tier" in proto:
        _qt = proto["quality_tier"]
    else:
        try:
            _qt = load_profile(owner_profile_id(proto)).quality_tier
        except Exception:  # noqa: BLE001  档案解析不出来 ⇒ 不猜一个看着正常的值
            _qt = "unknown"
    return Fingerprint(
        scene_guid="sha256:" + hashlib.sha256(blob).hexdigest()[:8],
        replay_script_hash="none-v0",              # T2 未建，如实缺省
        quality_tier=_qt,
        warmup_samples=int(proto.get("warmup", 8)),
        window_samples=int(proto.get("window_polls", 20)),
        n_samples=int(windows_target),
        poll_rate=float(proto.get("poll_interval_s", 1.0)),
        data_tier="A",                              # 本文件主源=A 轮询；A2 序列同源同窗
        editor_mode="gui",                          # G16：渲染值只在 GUI 有效
        environment_fingerprint=env_fp,
        scale_factor=float(proto.get("scale_factor", 1.0)),
        profile_id=owner_profile_id(proto),
    )


def main() -> int:
    mw = load_multwin()
    if mw is None:
        print("!! 缺 calibration/multiwin_full_summary.json / multiwin_full.json（先跑 m1_multiwin --session full）")
        print("   检测与标定必须来自同一多窗口数据源，禁止回退旧单窗口基线（G7/§3）")
        return 1
    fixs = {k: stats_from_windows(v) for k, v in mw.get("fixtures", {}).items() if "windows" in v}
    _clean = active_profile().clean_subject   # 噪声基准机制名来自档案，不写死
    # ── 阶段 8 A：噪声基准缺失分三种处置（设计 §1，判据单点 resolve_noise_basis）。
    #    patrol 与本文件都调它——历史上"缺 CLEAN 怎么办"两处各写一份，迟早漂移成
    #    "巡检出结论、状态机拒判"互相打脸。三态：
    #      incomplete（声明了却没采到）⇒ 采集失败，整轮拒判（B7：没采到≠没有异常）；
    #      L0（档案有意无同期基准）⇒ 相对显著性不可判，clean_stats={} 让 compare()
    #        自然落 insufficient_evidence，结论改由绝对下限表（第二腿）承担；
    #      L2（基准在场）⇒ 现状最强路径，内置档案即此态（行为逐字不变）。
    from upi.core.floor_check import (basis_relational, load_floor_table,
                                      resolve_noise_basis)
    basis = resolve_noise_basis(_clean, _clean in fixs)
    if basis == "incomplete":
        print(f"!! 档案声明了噪声基准 {_clean} 但本轮束里没有它 = 采集失败，"
              "禁止判定（§8.1/B7，与'工程本就无基准'是两回事）")
        return 1
    clean_stats = fixs.get(_clean) or {}
    if basis == "L0":
        print("!! 本工程无同期噪声基准（noise_basis=L0）：相对显著性不可判"
              "（各指标落 insufficient_evidence），仅绝对下限可评、FP 率 not_measurable")

    run_id = time.strftime("m1detect_%Y%m%d_%H%M%S")
    # 工程上下文来自档案（阶段 7）。标签位显式传 profile_id：
    # 内置档案的 id 与工程目录名同值（已实测指纹摘要逐字节相同），
    # 所以这一步不会让历史轮次的 env_fingerprint 失配。
    _pf = active_profile()
    tracer = Tracer(run_id, root=str(RUNS), channel="dev",
                    env_fingerprint=environment_fingerprint(
                        str(_pf.abs_project), profile_id=_pf.profile_id))

    cal = latest_calibration()
    # 标定闸只在**要跑相对判据**时生效（L2）。L0 下 compare 全落
    # insufficient_evidence、用不到任何阈值，绝对下限腿又不依赖标定——
    # 若照旧"无标定即整轮中止"，新工程没标定就直接失去了下限监测能力，
    # 与阶段 8 设计 §1"L0 只走绝对门槛"矛盾（test_patrol ⑥ 实测撞出）。
    if cal is None and basis_relational(basis):
        print("!! 无本工程标定文件（先跑 m1_multiwin --merge）；§8.1 禁止未标定检测")
        return 1

    # G7 真实落点：**阈值源指纹 × 数据源指纹** 必须一致才可拿阈值比数据。
    # 同一次 merge 链里两者同源必然一致；换错标定文件（如 n 或窗口长度不同）
    # 在这里被拦下，而不是无声混比。协议字段一致即可比，scene_guid 在 v0
    # 只反映来源文件（数据文件与阈值文件哈希必然不同），故对比取协议子集。
    # ⚠️ 整段只在**有标定**时跑：cal=None 只可能出现在 L0（无标定的 L2 上面
    #    已 return）。L0 没有阈值可比，相对通路下面整体落 insufficient_evidence
    #    （compare 缺 clean_stats 短路，连阈值都不读），只留下限腿——
    #    但代码不能拿着 None 去算 fp_th/apply_calibration，那会 AttributeError。
    env_fp = tracer.env_fingerprint
    fp_data = fingerprint_from_protocol(mw.get("protocol", {}), mw.get("_path", ""),
                                        env_fp, int(mw.get("windows_target", 4)))
    waterline_dets = []
    if cal is not None:
        fp_th = fingerprint_from_protocol(cal.get("protocol", {}), cal.get("_path", ""),
                                          env_fp, int(mw.get("windows_target", 4)))
        # 判据与比对见模块级 G7_COMPARE_FIELDS / g7_mismatches（提到模块级的唯一
        # 原因是可被断言：留在 main 里就是局部表达式，测试只能复刻一遍或 grep
        # 源码文本——复刻的那份不是产品，grep 测的是字符串不是行为。）
        mism = g7_mismatches(fp_data, fp_th)
        if mism:
            print("!! 阈值与数据协议不一致，禁止套用（G7）：", mism)
            if "profile_id" in mism:
                print(f"   数据侧工程={fp_data.profile_id}  标定侧工程={fp_th.profile_id}"
                      f"  当前运行档案={active_profile().profile_id}")
            return 1
        print(f"G7 校验: 阈值源×数据源协议一致（{'|'.join(str(getattr(fp_data,f)) for f in ('warmup_samples','window_samples','poll_rate','scale_factor'))}）")

        # §3A 通路分流：本脚本=水位通路（窗口分布 × noise_calibration 阈值）。
        # episode 检测器（frame_budget 阈值 × BreachSample 输入）的证据形态不同，
        # 由 m2a_detect_breach.py 独立跑——混进本循环会发生两件坏事：
        #   a) 窗口标定覆盖帧预算线（两套阈值体系互相污染，§8.1 违例）；
        #   b) "全量覆盖"检查被帧预算指标撞停（T2w 收口实测发生）。
        waterline_dets = [d for d in DEFAULT_REGISTRY
                          if d.thresholds.threshold_source == "noise_calibration"]
        other_dets = [d for d in DEFAULT_REGISTRY if d not in waterline_dets]
        if other_dets:
            print(f"通路分流: {len(other_dets)} 个非水位检测器移交对应通路 "
                  f"{[d.id for d in other_dets]}")
        n_applied = apply_calibration(waterline_dets, cal)
        print(f"标定应用: {n_applied}/{len(waterline_dets)} 项阈值来自 {Path(cal['_path']).name}")
        if n_applied != len(waterline_dets):
            print("!! 有水位检测器指标不在标定表内（§8.1 要求全量覆盖），中止")
            return 1
    else:
        print("无本工程标定：相对判据通路整体跳过（L0），仅评绝对下限")

    with tracer.stage("detect") as ctx:
        tracer.tool_call(ctx, kind="sqlite", name="read_calibration_windows", args=mw.get("_path", ""), exit_status=0)
        if cal is not None:
            tracer.tool_call(ctx, kind="sqlite", name="read_thresholds", args=cal.get("_path", ""), exit_status=0)
        # 选法 1 面板：机制×指标。评测集 = 档案 subjects ∪ 束里出现的被测机制
        # （并集而非只取 subjects：标定/采样清单可能比档案更宽，比如 dev 手动
        #  --fixtures 采的组合；漏评等于"没测"，多评只是多一行事实）。
        # 分区语义见 waterline_matrix：home 行落 results（历史契约、进统计），
        # 跨检行落 panel_results（筛查事实，不进 detector 检出率分母）。
        # 内置档案上 home 行 == 旧 results 逐字节兼容（零行为变化验收线）。
        _pf2 = active_profile()
        eval_subjects = sorted(set(_pf2.subjects)
                               | {k for k in fixs if k != _pf2.clean_subject})
        results, panel_results = waterline_matrix(
            eval_subjects, fixs, clean_stats, waterline_dets)

        # T7 矛盾规则表（P4，AGENTS §1.5）：跨指标组合的**机制签名**。
        # 边界（本轮口径修正）：不用于"判数据可信"——CLEAN 自比自时
        # totalAllocated 差 ~240KB 是环境噪声，超 S3 的 10MB 绝对阈会误报，
        # 而 S3 本意要抓的是"结构性大分配但无逐帧分配"。因此水位层
        # 输出只作归因方向裁定（配合检出结果互证），不作 bundle 可疑标记；
        # "数据可信"职责已由 instrument_distorted（三层开销）与 G17（分布退化）承担。
        conflicts_all = {}
        for fid, st in fixs.items():
            if fid == _clean:
                continue
            m = {k: v["median"] for k, v in st.items()}
            m.update({f"baseline_{k}": v["median"] for k, v in clean_stats.items()})
            cs = run_snapshot(m)
            # pair 口径：噪声基准=改前、fixture=改后 —— 水位层的组合签名
            # （F-GPU-01 命中 P1 = 碎描签名与 drawCalls 检出互证）。
            # ⚠️ pair 规则只在 L2（同期基准在场）跑：L0 下 clean_stats 为空，
            #    run_pair({}, st) 会把"没有改前值"喂进成对判据，产出的签名
            #    无意义。snapshot 规则不吃 clean_stats，L0 下照常跑。
            if basis_relational(basis):
                cs += run_pair({k: v["median"] for k, v in clean_stats.items()},
                               {k: v["median"] for k, v in st.items()})
            if cs:
                conflicts_all[fid] = [c.model_dump() for c in cs]

        # FP 自检：噪声基准自比自，任何"检出"都是误报（D6 口径）。
        # ⚠️ 只有 L2 可测：自比自要有"自"。L0 下若照跑 compare(det,{}, {})
        #    会得到一串 insufficient_evidence，fp_hits 为空 → 打成"FP=0 下界
        #    成立"——那是把"没测"冒充"测了且没问题"，正是设计 §1 明令禁止的
        #    （G15 成对展示）。故 L0 直接落空 dict，报告侧显示 not_measurable。
        fp_check = {}
        if basis_relational(basis):
            for det in waterline_dets:
                _, d = compare(det, clean_stats, clean_stats)
                fp_check[det.id] = d.get("verdict")
        fp_hits = [k for k, v in fp_check.items() if v == "detected"]

        # 绝对下限腿（第二判据，与相对检出物理分开）：档案配了表才评。
        # 表哈希进产物：提标（改表）⇒ 哈希变 ⇒ 与改前的检出不可混比（G7 同源纪律）。
        floors = {"enabled": False, "breached": [], "evaluated": [],
                  "unevaluated": [], "floor_hash": None}
        _fp = active_profile().floor_targets_path
        if _fp is not None:
            _tbl = load_floor_table(_fp)
            if _tbl is None:
                # 配了路径但文件不在 ⇒ 如实记未启用，不猜一张空表（空表=全通过=假绿）
                floors["why"] = f"floor_targets_path={_fp} 指向的文件不存在"
            else:
                from upi.core.floor_check import check_floors
                raw_fx_all = mw.get("fixtures", {})
                for fid, entry in raw_fx_all.items():
                    if fid == _clean or "windows" not in entry:
                        continue
                    r = check_floors(fid, entry, _tbl)
                    floors["evaluated"] += r["evaluated"]
                    floors["breached"] += r["breached"]
                    floors["unevaluated"] += r["unevaluated"]
                floors.update({"enabled": True, "floor_hash": _tbl.content_hash(),
                               "source": _tbl.source})

        out = {"run_id": run_id, "data_source": mw.get("_path"),
               "thresholds_source": cal.get("_path") if cal else None,
               "protocol_fingerprint": fp_data.model_dump(),   # G7：本轮判定挂靠的指纹
               "replayable": fp_data.replay_script_hash != "none-v0",  # T2 未建 → False，如实标注
               "band_basis": "between_windows", "fp_selfcheck": fp_check,
               "noise_basis": basis,               # 阶段 8 A：本轮噪声带来源等级
               "floors": floors,                   # 绝对下限腿（与 results 分区）
               "panel_results": panel_results,     # 跨检筛查行（不进 detector 分母）
               "conflicts": conflicts_all,
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results}
        (RUNS / run_id).mkdir(parents=True, exist_ok=True)
        det_fp = RUNS / run_id / "detection.json"
        det_fp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        # A1 债修复：evidence_refs 必须在 stage 块**内**赋值——span 在块退出时已写出，
        # 块外再赋 ctx 是无效写入（旧版 evidence_refs 恒空的根因）
        ctx["evidence_refs"] = [str(det_fp)]

    print(f"数据源: {Path(mw['_path']).name}（窗口间 σ，与阈值同源）  噪声带来源: {basis}")
    detected = sum(1 for r in results.values() if r.get("verdict") == "detected")
    for sub, r in results.items():
        mark = "检出" if r.get("verdict") == "detected" else ("证据不足" if "insufficient" in str(r.get("verdict")) else "未检出")
        print(f"  [{mark:4s}] {sub:10s} {r.get('metric','')} "
              f"fx={r.get('fixture')} clean={r.get('clean')} "
              f"delta={r.get('delta','')} ratio={r.get('deviation_ratio','')} n窗口={r.get('window_n','')} {r.get('reason','')}")
    # 跨检筛查行：单列计数，与主位检出分开（G18 等级归属——它们不进检出率分母）
    _pdet = [k for k, v in panel_results.items() if v.get("verdict") == "detected"]
    if _pdet:
        print(f"跨检筛查（指标面板，不计入 detector 检出率）命中 {len(_pdet)} 项:")
        for k in _pdet:
            v = panel_results[k]
            print(f"  [跨检] {k} Δ={v.get('delta')} ratio={v.get('deviation_ratio')}")
    # L0 下 fp_check 为空是"没测"不是"零误报"，打印必须区分（G15 成对展示：
    # 不许只报检出率、把 FP 谎报成 0）。措辞里机制名用档案值，不写死 CLEAN。
    if basis_relational(basis):
        print(f"检出 {detected}/{len(results)}；{_clean} 自检误报 {len(fp_hits)}/{len(fp_check)}"
              + (f" !!{fp_hits}" if fp_hits else "（FP=0，下界成立）"))
    else:
        print(f"检出 {detected}/{len(results)}（相对判据，本轮 {basis}）；"
              "误报率 not_measurable（无同期噪声基准，自检未跑）")
    if floors.get("enabled"):
        _br = floors["breached"]
        print(f"绝对下限: 破线 {len(_br)}/{len(floors['evaluated'])}"
              f"（未评估 {len(floors['unevaluated'])} 项，表 {floors['floor_hash']}）")
        for b in _br:
            print(f"  [越下限] {b['subject']:10s} {b['metric']} {b['op']}{b['line']} "
                  f"实测={b['stat']}（n窗口={b['n_windows']}）")
    elif "why" in floors:
        print(f"绝对下限: 未启用（{floors['why']}）")
    for fid, cs in conflicts_all.items():
        for c in cs:
            print(f"  [矛盾] {fid:10s} {c['rule_id']}: {c['observed'][:88]}")
    print("trace:", tracer.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
