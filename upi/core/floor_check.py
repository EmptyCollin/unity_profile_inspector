"""绝对下限目标表（阶段 8 · A 方案，设计文档 knowledge/下限目标与噪声基准设计.md）。

用户的框架：真实工程没有无缺陷场景，检测与修复针对的是**下限**（帧耗时、
draw call、内存这类可量化水位）；"提高下限标准 ⇒ 瓶颈暴露 ⇒ 继续修" 是
操作循环。本模块是那"标准"的载体与判据——**绝对线**，不依赖噪声基准。

为什么不塞进 detector.Thresholds：§8.1 的阈值来自噪声标定，是"这台机器
抖动多大"的测量产物；目标线是"这台机器应该多好"的工程外生约束（同
knowledge/frame_budget.json 的定性）。两套体系混用会互相污染——m1_detect
注释里记过实测教训（"窗口标定覆盖帧预算线"）。所以：

  · 数据结构独立（FloorTarget，不是 Thresholds 的字段）；
  · 判定独立成函数（check_floors，不并入 compare）；
  · 产物分区（findings 的 basis="floor_target"，与检出列表物理分开统计）。

出处纪律（继承 frame_budget 的既有规则）：**无出处的绝对线 = 手调，拒收**。
表必须带 source；缺 source 的条目加载期拒绝，不是运行时警告。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

Op = Literal["<=", "p95<=", "p99<=", ">="]


class FloorTarget(BaseModel):
    """一条绝对下限：metric op value（value 是"至少要做到"的线）。

    op 语义：
      "<="    窗口中位数 ≤ value        —— 常态水位（均值型下限）
      "p95<=" 窗口序列 p95 分位 ≤ value —— 最差 5% 帧也要达标（体感下限）
      "p99<=" 同上取 p99
      ">="    窗口中位数 ≥ value        —— 反向指标（如缓存命中率），少用
    ⚠️ 下限盯的是**分位**不是平均：均值好看、尾部卡顿的工程很常见
      （用户讨论定调，本仓 CPU 帧时间指标同口径）。
    """

    metric: str
    op: Op
    value: float
    unit: str = ""
    subject: Optional[str] = None    # 限定机制（None = 所有被测机制都适用）
    note: str = ""

    @model_validator(mode="after")
    def _sane(self):
        if not self.metric:
            raise ValueError("FloorTarget.metric 不能为空")
        if self.value < 0:
            raise ValueError(f"FloorTarget({self.metric}).value 不能为负，收到 {self.value}")
        return self

    def evaluate(self, series: list[float]) -> dict | None:
        """对一个指标的窗口序列（每窗口一个中位值）判定。

        返回 None = 数据不足/指标不适用（**不是"通过"**——调用方必须区分，
        把"没评估"冒充"通过"是 I1/I2 反复拦的那类错误）。
        """
        vals = [float(v) for v in series if isinstance(v, (int, float))]
        if not vals:
            return None
        if self.op == "<=" or self.op == ">=":
            import statistics
            stat = statistics.median(vals)
        else:
            q = 0.95 if self.op == "p95<=" else 0.99
            stat = _percentile(vals, q)
        breached = stat > self.value if self.op != ">=" else stat < self.value
        return {"op": self.op, "stat": round(float(stat), 4), "line": self.value,
                "breached": breached, "n_windows": len(vals)}


def _percentile(vals: list[float], q: float) -> float:
    """线性插值分位（小样本 n≥2 即可算；n=1 只能返回该值本身）。

    为什么自己算而不是 statistics.quantiles：后者要 n≥2 且分位桶语义
    不是我们要的线性插值；窗口数常态只有 4~5，判 p95/p99 本质是
    "取最靠尾的那两个值"——实现必须对小样本诚实（I1），所以 docstring
    直说：n=4 时 p99 就是 max，这是插值定义而非精度承诺。
    """
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    idx = q * (len(s) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] * (1 - frac) + s[hi] * frac


class FloorTable(BaseModel):
    """一个工程的下限目标表。文件缺失 ⇒ 调用方拿到 None（不启用，不是空表通过）。"""

    source: str                        # 出处（必填，"无出处=手调"纪律的载体）
    created_at: str = ""
    targets: list[FloorTarget] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sane(self):
        if not self.source.strip():
            raise ValueError("下限表必须带 source 出处（§8.1：无出处的绝对线=手调，拒收）")
        return self

    def content_hash(self) -> str:
        """表内容摘要，进协议指纹（G7）：改下限 ⇒ 指纹变 ⇒ 禁止与改前的检出混比。

        这是"提标"动作的干净边界——提标后大面积检出不该被当成"环境突变"，
        也不该与提标前的检出率趋势连成一条线。只对内容取哈希，不含时间戳：
        重新保存同一份表不应触发不可比。
        """
        blob = json.dumps(self.model_dump(include={"source", "targets"}),
                          sort_keys=True, ensure_ascii=False).encode("utf-8")
        return "sha256:" + hashlib.sha256(blob).hexdigest()[:12]

    def applicable(self, subject: str) -> list[FloorTarget]:
        return [t for t in self.targets if t.subject in (None, subject)]


def load_floor_table(path: Path | str) -> FloorTable | None:
    """fail-soft 加载：文件不存在 ⇒ None（该工程没启用下限表）。

    为什么这里允许"缺文件=没启用"而档案本身 fail-closed：下限表是**可选增强**
    （L2/L1 的噪声分级判据不依赖它），而档案是"我在和谁协作"的身份声明——
    身份缺失必须停，增强缺失降级继续。两种缺省的方向不同，不是一个松一个紧。
    ⚠️ 但"文件存在而内容坏"必须炸（ProfileError 语义），不能静默当成没启用：
    把坏表读成空表 = 所有下限检查"通过" = 假绿，正是 §8.1 范式讨论里
    "看起来跑通了其实测了个寂寞"的同型事故。
    """
    p = Path(str(path))
    if not p.exists():
        return None
    from upi.config.project_profile import ProfileError
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ProfileError(f"下限表 {p} 读取失败：{type(e).__name__}: {str(e)[:120]}") from None
    try:
        return FloorTable(**data)
    except Exception as e:  # noqa: BLE001  pydantic 校验失败要转成档案错误语义
        raise ProfileError(f"下限表 {p} 校验失败：{str(e)[:200]}") from None


def window_series(bundle_fx_entry: dict) -> dict[str, list[float]]:
    """原始束的一个机制条目 → {指标: [逐窗口中位值]}。

    为什么不复用 m1_multiwin.stats_from_windows 的输出：它把窗口折成了
    {metric: {median, noise_band_mad, ...}}，**逐窗口序列已经丢了**，而 p95/p99
    必须拿序列算。往它的输出里补 "series" 键看着顺手，实际会打崩下游——
    矛盾规则那层是 `m = {k: v["median"] for k, v in st.items()}` 的形态，
    多一个 list 值就 TypeError（本轮实测前核出来的坑，不是假想）。
    所以这里单独从束里取，只读 A 档 metrics；A2 序列暂不纳入下限
    （下限表当前面向帧耗时/绘制计数这类 A 档水位指标）。
    """
    out: dict[str, list[float]] = {}
    for w in (bundle_fx_entry or {}).get("windows") or []:
        for k, m in (w.get("metrics") or {}).items():
            v = m.get("median") if isinstance(m, dict) else m
            if isinstance(v, (int, float)):
                out.setdefault(k, []).append(float(v))
    return out


def check_floors(subject: str, bundle_fx_entry: dict, table: FloorTable) -> dict:
    """对单个被测机制做下限判定。bundle_fx_entry = 束里该机制的原始条目。

    返回 {"evaluated": [...], "breached": [...], "unevaluated": [...]}
    ⚠️ unevaluated（数据不足的条目）必须单列，且**不能从 breached 为空反推达标**：
    表里配了 drawCalls 线而这轮 drawCalls 没采到，结论是"未评估"，
    记成"通过"就是拿没测冒充测过（I1/I2 反复拦的那类）。
    """
    series_all = window_series(bundle_fx_entry)
    evaluated, breached, unevaluated = [], [], []
    for t in table.applicable(subject):
        series = series_all.get(t.metric)
        if not series:
            unevaluated.append({"metric": t.metric, "op": t.op, "line": t.value,
                                "why": f"本轮无 {t.metric} 逐窗口数据"})
            continue
        verdict = t.evaluate(series)
        if verdict is None:
            unevaluated.append({"metric": t.metric, "op": t.op, "line": t.value,
                                "why": "序列为空"})
            continue
        row = {"subject": subject, "metric": t.metric, "unit": t.unit,
               "note": t.note, "basis": "floor_target", **verdict}
        evaluated.append(row)
        if verdict["breached"]:
            breached.append(row)
    return {"evaluated": evaluated, "breached": breached, "unevaluated": unevaluated}


def resolve_noise_basis(clean_name: str, clean_present: bool) -> str:
    """水位判据的噪声来源：L2 / L0 / incomplete。

    三态而不是两态，因为"工程没有噪声基准"和"本轮没采到它"是**完全不同的
    事实**，处置也相反：
      · clean_name 为空 ⇒ 档案有意声明"本工程无同期基准" ⇒ **L0**：相对显著性
        不可判，走绝对下限腿（合法降级，每轮都能出结论）。
      · 声明了却没采到 ⇒ **incomplete**：这是采集失败不是工程属性。B7 的实测
        教训（失焦轮五夹具全失败却被记成"零检出"）说：没采到冒充"没有异常"
        比拒判危险得多，所以调用方必须整轮拒判，不许降级成 L0 蒙混。
      · 两者皆有 ⇒ **L2**（现状最强）。

    ⚠️ 这条判断**必须单点**：patrol_waterline 与 m1_detect 各有一份水位循环
      （历史上"缺 CLEAN 怎么办"两处各写一份），两处各判迟早漂移成
      "巡检出结论、状态机拒判"互相打脸。两边都调本函数，依据同源。
    为什么没有 L1：设计里 L1=跨轮历史波动当噪声带，与 G7"跨 run 禁比"直接
      冲突（要做得先有"同协议同指纹的历史方差库"，独立基建）。缺 L1 不假装
      存在，落 L0——宁缺毋假（I1/G19 同纪律）。
    """
    if not clean_name:
        return "L0"
    return "L2" if clean_present else "incomplete"


def basis_relational(basis: str) -> bool:
    """相对显著性判据与 FP 自检都只有 L2 能跑（要有"自"才有"相对"）。
    L0/L1/缺项下调用方必须输出 not_measurable，不许留空冒充 0（G15 成对展示）。"""
    return basis == "L2"
