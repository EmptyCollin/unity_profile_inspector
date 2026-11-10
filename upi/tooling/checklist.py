"""§8.7 过拟合体检表六项计算（M6'）。

为什么单独一个模块：这六项同时被两处消费——
  · `knowledge.propose_promotion(overfit_checklist=...)` 判定升维提案 blocked/passed
  · M7 报告必须附实际数值（DESIGN §11 第 13 项、AGENTS §5）
两处必须同一口径，否则会出现"提案说达标、报告说缺项"这种无法解释的分歧。
**通过阈值一律从 `knowledge._CHECKLIST` 的判定函数反查，本模块不抄第二份数字**
（抄了就会有两套阈值，改一处漏一处）。

诚实性原则（本轮实况）：第一轮 n_effective 是个位数，六项里多数**算不出来**。
算不出必须返回 None + 原因，绝不返回 0 或估算值——把"没数据"渲染成"达标 0.0"
正是 G14/I1 要防的失效。DESIGN §11 已预判这个结果：第一轮的合法输出就是
"机制可用 + 数值未达标/未计算 ⇒ 所有变更保持 blocked"。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from upi.core.knowledge import _CHECKLIST
from upi.tooling import aggregate as AG

# 从 _CHECKLIST 的判定函数反查阈值：判据只在 knowledge.py 写一次。
# 做法是拿边界值两侧探一次——不解析 lambda 源码，避免依赖实现细节。
def _threshold(field: str) -> Optional[tuple[str, float]]:
    for rule_key, (f, pred) in _CHECKLIST.items():
        if f != field:
            continue
        # 单调方向：0 与 1 各探一次
        lo, hi = bool(pred(0.0)), bool(pred(1.0))
        if lo == hi:
            return None                      # 非单调，判据特殊，不猜
        # 边界在 (0,1) 之间，二分定位
        a, b = (0.0, 1.0) if not lo else (1.0, 0.0)
        for _ in range(52):
            m = (a + b) / 2
            if pred(m) == pred(a):
                a = m
            else:
                b = m
        # 方向：pred(0.0) 为真 ⇒ 小的值达标 ⇒ 条件是 v <= thr；
        # 反之大的值达标 ⇒ v >= thr。（此处曾把方向写反：数值对、符号反，
        # 报告里会显示"检出率差 ≥0.15 通过"这种正好说反的口径）
        return ("<=" if lo else ">="), round((a + b) / 2, 6)
    return None


from upi.config.project_profile import active_profile

ROOT = AG.ROOT


def fmt_rate(v) -> str:
    """检出率显示：None 写"无观测"，不写成 0（"没测"与"测到 0"必须区分）。"""
    return "无观测" if v is None else f"{v:.3f}"


# 阶段 7：路径来自 profile（工程侧数据的位置），但**保持模块属性形态**——
# scripts/check_fixture_split.py 靠覆盖 CL.FIXTURE_SPEC 把体检表整体隔离到
# 临时目录（那里有注释明说）。改成函数就没这个入口了，所以这里在导入时取一次值。
FIXTURE_SPEC = active_profile().fixture_spec


CLEAN_ID = "CLEAN"


def split_of() -> dict[str, str]:
    """夹具 → `tuning` | `holdout` | `clean` 三态。

    来源是 `fixture_spec.json` 的 `holdout` 字段（0/1），不是凭夹具编号猜。
    ⚠️ 本函数是**新增的读点**：该字段此前只写在夹具规格里、无任何代码消费，
    所以 §8.1 的"holdout 实现细节不进调参"此前只靠人工自觉（B6 记录的正是这点）。
    规格里缺该字段、或值非 0/1 ⇒ 返回空 dict，让调用方把体检项标为未计算，
    **不默认成 tuning**（默认会把留出夹具当成调参夹具用，正是过拟合的来源）。

    为什么 CLEAN 是**第三态**而不是 tuning：规格里 CLEAN 的 holdout=0 只是没填，
    它没有缺陷、职责是专供 FP 测量（§8.1 末条）。早期版本把它归成 tuning
    再要求每个消费方"自己记得排除"——那是把防错责任推给记性：漏一次，
    检出率分母里就多出 n 个必然 not_found 的观测，tuning 侧被压低，
    读者会误读成"调参侧反而更差"。建模上分开，才不依赖自觉。
    """
    try:
        spec = json.loads(FIXTURE_SPEC.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, str] = {}
    for f in spec.get("fixtures") or []:
        fid, hv = f.get("id"), f.get("holdout")
        if not fid or hv not in (0, 1):
            return {}                     # 任一模糊即整体不猜
        out[fid] = (CLEAN_ID if fid == CLEAN_ID else
                    "holdout" if hv == 1 else "tuning")
    return out


def defect_split() -> dict[str, str]:
    """只留缺陷夹具的 tuning/holdout 两类（负样本按 §8.1 不参与检出率分组）。"""
    return {k: v for k, v in split_of().items() if v != CLEAN_ID}


def compute(db: Path | str | None = None) -> dict:
    """返回 {field: {"value": float|None, "ok": bool|None, "n": int, "why": str}}。

    ok=None 表示"未计算"（不是不达标）；报告与提案都必须区分这两者。
    """
    # 走 aggregate._with_ro：异常路径也要释放句柄（Windows 未关句柄会锁库，
    # 让"SQL 报错"退化成"文件删不掉"，现场被第二个故障覆盖）。
    return AG._with_ro(db or AG.DB, _compute)


def _compute(c) -> dict:
    out: dict[str, dict] = {}

    # ── 1) tuning_holdout_gap：按 fixture_spec 的 holdout 标记分组实测 ──
    # 分组用 defect_split()：CLEAN 在类型上就是第三态（"clean"），不参与检出率
    # 分组——排除这件事由建模保证，不靠调用方记得过滤。
    sp_all, sp = split_of(), defect_split()
    if sp:
        rates = {}
        ns = {}
        for side in ("tuning", "holdout"):
            ids = [k for k, v in sp.items() if v == side]
            if not ids:
                rates[side] = None
                continue
            q = ",".join("?" * len(ids))
            rows = c.execute(
                f"SELECT COUNT(*) total, "
                f"SUM(CASE WHEN verdict='detected' THEN 1 ELSE 0 END) det "
                f"FROM detections WHERE fixture IN ({q}) AND detector IS NOT NULL",
                ids).fetchone()
            total = (rows or (0, 0))[0] or 0
            det = (rows or (0, 0))[1] or 0
            rates[side] = (det / total) if total else None
            ns[side] = total
        if rates["tuning"] is None or rates["holdout"] is None:
            gap, ok_ = None, None
        else:
            gap = abs(rates["tuning"] - rates["holdout"])
            ok_ = gap <= 0.15
        out["tuning_holdout_gap"] = {
            "value": round(gap, 4) if gap is not None else None,
            "ok": ok_, "n": min(ns.values()) if ns else 0,
            "why": (f"按 fixture_spec.holdout 分组："
                    f"tuning={sorted(k for k, v in sp.items() if v == 'tuning')} "
                    f"检出率 {fmt_rate(rates['tuning'])}（n={ns.get('tuning')}）、"
                    f"holdout={sorted(k for k, v in sp.items() if v == 'holdout')} "
                    f"检出率 {fmt_rate(rates['holdout'])}（n={ns.get('holdout')}）"
                    + ("；CLEAN 已排除（无缺陷，专供 FP 测量）"
                       if "CLEAN" in sp_all else ""))
        }
    else:
        out["tuning_holdout_gap"] = {
            "value": None, "ok": None, "n": 0,
            "why": f"夹具规格 {FIXTURE_SPEC.name} 缺 holdout 字段或值非 0/1 ⇒ 不猜测"
                   "分组（默认成 tuning 会把留出夹具喂进调参，正是 §8.1 要防的）"}

    # ── 2) clean_fp_rate：CLEAN 自比自，任何 detected 即误报（D6 口径）──
    alerts = total = 0
    n_runs_with = 0
    for p in sorted(AG.RUNS.glob("*/detection.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        fp = d.get("fp_selfcheck") or {}
        if not fp:
            continue
        n_runs_with += 1
        for verdict in fp.values():
            total += 1
            alerts += 1 if verdict == "detected" else 0
    out["clean_fp_rate"] = {
        "value": (alerts / total) if total else None,
        "ok": None if not total else bool(alerts == 0),
        "n": total,
        "why": (f"CLEAN 自比自：阈值来自 CLEAN 自身，任何 detected 都是误报"
                f"（{n_runs_with} 轮的 detection.json 带 fp_selfcheck 字段）"
                if total else "无任何 detection.json 带 fp_selfcheck 字段")}

    # ── 3) ece_diff：需 tuning/holdout 两侧分箱，双侧都不够 ──
    out["ece_diff"] = {
        "value": None, "ok": None, "n": 0,
        "why": "需两侧各自的置信度×结局分箱；outcome 总数 "
               f"{c.execute('SELECT COUNT(*) FROM runs WHERE verdict IS NOT NULL').fetchone()[0]}"
               " 条，单侧 n<5 无法分箱（§7.3 小样本禁算）"}

    # ── 4) n_eff_ratio：n_effective / n_trials（§8.2 独立上下文口径）──
    kb = AG.KB_DB
    n_eff = n_trials = 0
    if Path(kb).exists():
        k = sqlite3.connect(str(kb))
        try:
            n_trials = k.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
            # 上下文键三处同源（KnowledgeStore.n_effective / aggregate / 这里），
            # 阶段 7 批 2 起含工程档案维度。
            from upi.core.knowledge import context_key_expr
            n_eff = k.execute(
                f"SELECT COUNT(DISTINCT {context_key_expr(k)}) FROM outcomes"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            pass
        finally:
            k.close()
    ratio = (n_eff / n_trials) if n_trials else None
    out["n_eff_ratio"] = {
        "value": ratio,
        "ok": None if ratio is None else bool(ratio >= 0.5),
        "n": n_trials,
        "why": f"n_effective={n_eff}（独立夹具×档位上下文）/ n_trials={n_trials}；"
               "同一上下文重复运行按 §8.2 只计 1 次"}

    # ── 5) sig_transfer：需变体夹具与命中测试，M6' 未建 ──
    out["sig_transferability"] = {
        "value": None, "ok": None, "n": 0,
        "why": "签名可迁移性需为同机制造变体夹具并做命中测试（§8.3），"
               "当前每机制仅 1 个缺陷夹具，无变体可比"}

    # ── 6) verdict_consistency：同工程×同夹具×同协议的完整判定轮次 ──
    # G7 要求"协议指纹一致才可对比"。实测：巡检轮次不产 detection.json
    # ⇒ protocol_fingerprint 为 NULL。此时**不能**只看夹具同名就当可比：
    # 两轮的档位/窗口数/环境若不同，判定一致率就是把不可比数据算成一致。
    # profile 进分组（阶段 7 批 2）：不同工程可以有同名机制编号，协议与环境
    # 字段也可能恰好相同，但那不是同一处境下的复现——并到一组算"判定一致"
    # 等于拿 A 工程的稳定冒充 B 工程的稳定。
    # 一致率的定义（§8.7）：可比的每一组内，判定结论是否全部相同；
    # 全同的组记 1，否则记 0，再对组数取比例——不是把"resolved"当正确。
    # ⚠️ SELECT 加了 profile 一列，下面取 verdicts 的下标从 g[3] 变 g[4]。
    #    取错列不会报错，只会拿环境指纹那串去比"是否全同"，一致率恒为 0。
    # 分组键来自 aggregate.runs_group_cols（唯一来源），两个理由：
    #  · 旧库兼容——checklist 走 connect_ro，只读连接不跑迁移，写死 ru.profile
    #    会让整份体检表崩在 "no such column: ru.profile"。拿旧 stats.db 出报告
    #    是真实用户路径，必须降级而不是抛错；
    #  · SELECT 前几列与 GROUP BY 永远同序，下面取 verdicts 的下标按 len(grp)
    #    现算。本轮已栽过一次：加列后下标写死，把环境指纹当成判定结果比，
    #    一致率静默变成 0，看起来像"结论不一致"其实是取错了列。
    grp = AG.runs_group_cols(c, "ru")
    ng = len(grp)                       # 分组列数 == verdicts 的下标
    groups = c.execute(f"""
      SELECT {", ".join(grp)},
             GROUP_CONCAT(DISTINCT ru.verdict) verdicts, COUNT(*) n
      FROM runs ru
      WHERE ru.verdict IS NOT NULL AND ru.protocol_fingerprint IS NOT NULL
      GROUP BY {", ".join(grp)}""").fetchall()
    cmp_groups = [g for g in groups if g[ng + 1] >= 2]   # 单轮组无"一致"可言
    if cmp_groups:
        agree = sum(1 for g in cmp_groups
                    if len(set((g[ng] or "").split(","))) == 1)
        val = agree / len(cmp_groups)
        out["cross_run_verdict_consistency"] = {
            "value": round(val, 3),
            "ok": val >= 0.9,
            "n": len(cmp_groups),
            "why": (f"可比组（{AG.runs_group_label(c, 'ru')}且 ≥2 轮判定）"
                    f"{len(cmp_groups)} 组，其中判定全一致的 {agree} 组")}
    else:
        n_v = c.execute("SELECT COUNT(*) FROM runs WHERE verdict IS NOT NULL").fetchone()[0]
        out["cross_run_verdict_consistency"] = {
            "value": None, "ok": None, "n": n_v,
            "why": f"有判定的轮次 {n_v} 条，但没有一条带 protocol_fingerprint"
                   "（巡检链路不产 detection.json）⇒ 按 G7 无法确认同协议可比，"
                   "不拿'夹具同名'冒充可比。解锁路径：巡检把协议指纹写进 runreport"}

    # §7.3 硬要求"hover 显示公式，不允许只给数不给口径"：每项挂上阈值与
    # 阈值来源，且阈值是从 _CHECKLIST 反查的，不是这里抄的第二份数字。
    for field, v in out.items():
        t = _threshold(field)
        v["threshold"] = f"{t[0]}{t[1]}" if t else "见 knowledge._CHECKLIST"

    # 汇总：任何未计算项都使整体 blocked（G14 的"缺项即 blocked"字面实现）
    n_missing = sum(1 for v in out.values() if v["ok"] is None)
    n_bad = sum(1 for v in out.values() if v["ok"] is False)
    n_verdict = c.execute(
        "SELECT COUNT(*) FROM runs WHERE verdict IS NOT NULL").fetchone()[0]
    return {"items": out,
            "n_items": len(out), "n_missing": n_missing, "n_fail": n_bad,
            "status": ("incomplete" if n_missing else
                       "blocked" if n_bad else "passed"),
            "thresholds_source": "upi.core.knowledge._CHECKLIST",
            "n_runs_with_verdict": n_verdict}


def checklist_for_proposal(db: Path | str | None = None) -> Optional[dict]:
    """给 propose_promotion 用的扁平字典。

    语义要精确区分三态，不能一律塌成 None：
      · 六项全有值      ⇒ 返回全部（提案按达标/不达标判定）
      · 部分有值        ⇒ **只返回有值的项**——propose_promotion 会分别报
                          "缺项 X"与"不达标 Y=值"，两类原因不混为一谈。
                          曾写成"任一项缺就整体返回 None"，那会把已经算出来的
                          达标数值也丢掉，blocked 理由退化成"缺体检表"一句
                          笼统话，读者无法知道到底缺哪几项。
      · 一项都算不出    ⇒ 返回 None（此时"缺 §8.7 体检表"才是准确表述）
    """
    items = compute(db)["items"]
    vals = {k: v["value"] for k, v in items.items() if v["value"] is not None}
    return vals or None


def gate_check(reason: str, checklist: Optional[dict] = None) -> dict:
    """变更上线门禁的判定入口（G14）：把变更说明与体检表数值绑在一起。"""
    cl = checklist if checklist is not None else compute()["items"]
    missing = [k for k, v in cl.items() if isinstance(v, dict) and v.get("ok") is None]
    fail = [k for k, v in cl.items()
            if isinstance(v, dict) and v.get("ok") is False]
    return {"change": reason,
            "decision": "blocked" if (missing or fail) else "allowed",
            "missing_items": missing, "failed_items": fail,
            "note": "缺项或不达标一律 blocked（AGENTS G14 / D9）"}


if __name__ == "__main__":
    print(json.dumps(compute(), ensure_ascii=False, indent=1))
