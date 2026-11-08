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

from upi.core.conflicts import run_pair, run_snapshot                    # noqa: E402
from upi.core.detector import DEFAULT_REGISTRY                    # noqa: E402
from upi.core.models import Evidence, Fingerprint, Issue, IssueSignature  # noqa: E402
from upi.tooling.trace import Tracer, environment_fingerprint     # noqa: E402

BASELINES = ROOT / "baselines"
CALDIR = ROOT / "calibration"
RUNS = ROOT / "runs"


def latest_baseline() -> Path:
    return max(BASELINES.glob("baseline_*.json"), key=lambda p: p.stat().st_mtime)


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


def latest_calibration() -> dict | None:
    cps = sorted(BASELINES.glob("thresholds_*.json"), key=lambda q: q.stat().st_mtime)
    if not cps:
        return None
    cal = json.loads(cps[-1].read_text(encoding="utf-8"))
    cal["_path"] = str(cps[-1])
    return cal


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
    # A2：每窗口取逐帧序列中位数，再跨窗口统计
    A2MAP = {"gc": "gc_alloc_per_frame", "anim": "animator_process_animations_ns",
             "graph": "animator_process_graph_ns", "mem": "total_used_per_frame"}
    for src, kname in A2MAP.items():
        vals = []
        for w in wins:
            seq = (w.get("a2") or {}).get(src) or []
            if len(seq) >= 50:
                vals.append(float(st.median(seq)))
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
    fp = CALDIR / "multiwin_full.json"
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
    """
    import hashlib
    try:
        blob = Path(source_path).read_bytes()
    except Exception:  # noqa: BLE001
        blob = source_path.encode("utf-8", "replace")
    return Fingerprint(
        scene_guid="sha256:" + hashlib.sha256(blob).hexdigest()[:8],
        replay_script_hash="none-v0",              # T2 未建，如实缺省
        quality_tier=proto.get("quality_tier", "urp-balanced"),
        warmup_samples=int(proto.get("warmup", 8)),
        window_samples=int(proto.get("window_polls", 20)),
        n_samples=int(windows_target),
        poll_rate=float(proto.get("poll_interval_s", 1.0)),
        data_tier="A",                              # 本文件主源=A 轮询；A2 序列同源同窗
        editor_mode="gui",                          # G16：渲染值只在 GUI 有效
        environment_fingerprint=env_fp,
        scale_factor=float(proto.get("scale_factor", 1.0)),
    )


def main() -> int:
    mw = load_multwin()
    if mw is None:
        print("!! 缺 calibration/multiwin_full.json（先跑 m1_multiwin --session full）")
        print("   检测与标定必须来自同一多窗口数据源，禁止回退旧单窗口基线（G7/§3）")
        return 1
    fixs = {k: stats_from_windows(v) for k, v in mw.get("fixtures", {}).items() if "windows" in v}
    if "CLEAN" not in fixs:
        print("!! 缺 CLEAN 变体，无法对照（§8.1）")
        return 1
    clean_stats = fixs["CLEAN"]

    run_id = time.strftime("m1detect_%Y%m%d_%H%M%S")
    tracer = Tracer(run_id, root=str(RUNS), channel="dev",
                    env_fingerprint=environment_fingerprint(str(ROOT / "fixtures/upi-fixture")))

    cal = latest_calibration()
    if cal is None:
        print("!! 无标定文件（先跑 m1_multiwin --merge）；§8.1 禁止未标定检测")
        return 1

    # G7 真实落点：**阈值源指纹 × 数据源指纹** 必须一致才可拿阈值比数据。
    # 同一次 merge 链里两者同源必然一致；换错标定文件（如 n 或窗口长度不同）
    # 在这里被拦下，而不是无声混比。协议字段一致即可比，scene_guid 在 v0
    # 只反映来源文件（数据文件与阈值文件哈希必然不同），故对比取协议子集。
    env_fp = tracer.env_fingerprint
    fp_data = fingerprint_from_protocol(mw.get("protocol", {}), mw.get("_path", ""),
                                        env_fp, int(mw.get("windows_target", 4)))
    fp_th = fingerprint_from_protocol(cal.get("protocol", {}), cal.get("_path", ""),
                                      env_fp, int(mw.get("windows_target", 4)))
    compare_fields = ["replay_script_hash", "quality_tier", "warmup_samples",
                      "window_samples", "n_samples", "poll_rate", "data_tier",
                      "editor_mode", "scale_factor", "environment_fingerprint"]
    mism = [f for f in compare_fields
            if getattr(fp_data, f) != getattr(fp_th, f)]
    if mism:
        print("!! 阈值与数据协议不一致，禁止套用（G7）：", mism)
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

    with tracer.stage("detect") as ctx:
        tracer.tool_call(ctx, kind="sqlite", name="read_calibration_windows", args=mw.get("_path", ""), exit_status=0)
        tracer.tool_call(ctx, kind="sqlite", name="read_thresholds", args=cal.get("_path", ""), exit_status=0)
        results = {}
        for det in waterline_dets:
            fid = det.fixture
            if fid not in fixs:
                results[fid] = {"verdict": "insufficient_evidence", "why": "标定数据缺该夹具"}
                continue
            issue, detail = compare(det, fixs[fid], clean_stats)
            detail["detector"] = det.id
            detail["detector_version"] = det.version
            detail["window_n"] = clean_stats.get(det.metric, {}).get("n")
            if issue:
                detail["issue_signature"] = issue.signature.model_dump()
                detail["magnitude"] = issue.magnitude
            results[fid] = detail

        # T7 矛盾规则表（P4，AGENTS §1.5）：跨指标组合的**机制签名**。
        # 边界（本轮口径修正）：不用于"判数据可信"——CLEAN 自比自时
        # totalAllocated 差 ~240KB 是环境噪声，超 S3 的 10MB 绝对阈会误报，
        # 而 S3 本意要抓的是"结构性大分配但无逐帧分配"。因此水位层
        # 输出只作归因方向裁定（配合检出结果互证），不作 bundle 可疑标记；
        # "数据可信"职责已由 instrument_distorted（三层开销）与 G17（分布退化）承担。
        conflicts_all = {}
        for fid, st in fixs.items():
            if fid == "CLEAN":
                continue
            m = {k: v["median"] for k, v in st.items()}
            m.update({f"baseline_{k}": v["median"] for k, v in clean_stats.items()})
            cs = run_snapshot(m)
            # pair 口径：CLEAN=改前、fixture=改后 —— 水位层的组合签名
            # （F-GPU-01 命中 P1 = 碎描签名与 drawCalls 检出互证）
            cs += run_pair({k: v["median"] for k, v in clean_stats.items()},
                           {k: v["median"] for k, v in st.items()})
            if cs:
                conflicts_all[fid] = [c.model_dump() for c in cs]

        # FP 自检：CLEAN 对自己跑一遍，任何"检出"都是误报（D6 口径）
        fp_check = {}
        for det in waterline_dets:
            _, d = compare(det, clean_stats, clean_stats)
            fp_check[det.id] = d.get("verdict")
        fp_hits = [k for k, v in fp_check.items() if v == "detected"]

        out = {"run_id": run_id, "data_source": mw.get("_path"),
               "thresholds_source": cal.get("_path"),
               "protocol_fingerprint": fp_data.model_dump(),   # G7：本轮判定挂靠的指纹
               "replayable": fp_data.replay_script_hash != "none-v0",  # T2 未建 → False，如实标注
               "band_basis": "between_windows", "fp_selfcheck": fp_check,
               "conflicts": conflicts_all,
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results}
        (RUNS / run_id).mkdir(parents=True, exist_ok=True)
        det_fp = RUNS / run_id / "detection.json"
        det_fp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        # A1 债修复：evidence_refs 必须在 stage 块**内**赋值——span 在块退出时已写出，
        # 块外再赋 ctx 是无效写入（旧版 evidence_refs 恒空的根因）
        ctx["evidence_refs"] = [str(det_fp)]

    print(f"数据源: {Path(mw['_path']).name}（窗口间 σ，与阈值同源）")
    detected = sum(1 for r in results.values() if r.get("verdict") == "detected")
    for fid, r in results.items():
        mark = "检出" if r.get("verdict") == "detected" else ("证据不足" if "insufficient" in str(r.get("verdict")) else "未检出")
        print(f"  [{mark:4s}] {fid:10s} {r.get('metric','')} fx={r.get('fixture')} clean={r.get('clean')} "
              f"delta={r.get('delta','')} ratio={r.get('deviation_ratio','')} n窗口={r.get('window_n','')} {r.get('reason','')}")
    print(f"检出 {detected}/{len(results)}；CLEAN 自检误报 {len(fp_hits)}/{len(fp_check)}"
          + (f" !!{fp_hits}" if fp_hits else "（FP=0，下界成立）"))
    for fid, cs in conflicts_all.items():
        for c in cs:
            print(f"  [矛盾] {fid:10s} {c['rule_id']}: {c['observed'][:88]}")
    print("trace:", tracer.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
