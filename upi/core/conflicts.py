"""跨指标矛盾规则表（P4，DESIGN §3A / §2.4 T7，AGENTS §1.5）。

定位：矛盾 = 指标组合**物理上说不通**，或组合与归因方向互相拆台。
它不预设"正常值"，因此不受基线口径的自证批评（DESIGN §13 v0.12 ④）。

分工边界（避免与既有机制重复）：
- 单指标涨跌 → 守护指标（G2 guard），不归这里管；
- 无收益改动 → G1 not_significant，不归这里管；
- 本表只管**跨指标组合**：出现即说明"数字在说谎或机制理解错了"，
  视同守护劣化记 conflicts[] 进 Review，Verify 不得自行解释成因（AGENTS §1.5）。

规则两类：
- snapshot：同一采样内多指标的组合（物理不可能类，误报面最小）
- pair：改前/改后两组指标的组合（修复未生效但数字动了类）

每条规则的正/负例都标注数据来源（实测者优先，I1/G13 同口径）：
- S3 mem_jump_no_gc 的正例就是 F-LOAD-01 实测（Δ28.6MB 而 gc 中位不动）
  ——它直接命中"同步加载、非脚本每帧分配"的机制归因；
- P1 density_collapse 的正例是 F-GPU-01 实测（drawCalls +170% 而 triangles 仅
  +44%，单绘制密度 46→24.6 tri/draw 腰斩）——碎描/合批失效的签名。
暂缓条目（依赖未接入的数据源，登记在此防遗忘、不注册防虚报）：
- FD 事件数 == setPassCalls 交叉校验（依赖程序侧 FD，DESIGN §12 ①-⑫ 已判不可用）
- anim_ns==0 但场景含 Animator（依赖 C+ 场景组件清点，M3.5）
"""
from __future__ import annotations

from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

from upi.core.models import Conflict


class Rule(BaseModel):
    id: str
    kind: Literal["snapshot", "pair"]
    # snapshot: check(metrics)；pair: check(before, after=None 不适用)
    predicate: Callable[..., bool] = Field(exclude=True)
    expected: str
    observed_tpl: str
    note: str = ""

    def check_snapshot(self, m: dict) -> Optional[Conflict]:
        if self.kind != "snapshot":
            return None
        try:
            if self.predicate(m):
                return Conflict(rule_id=self.id, metrics=_numify(m),
                                expected=self.expected,
                                observed=self.observed_tpl.format(**m))
        except (KeyError, TypeError, ValueError):
            return None            # 指标缺席 = 证据不足，不判（I2）
        return None

    def check_pair(self, before: dict, after: dict) -> Optional[Conflict]:
        if self.kind != "pair":
            return None
        merged = {f"a_{k}": v for k, v in after.items()}
        merged.update({f"b_{k}": v for k, v in before.items()})
        try:
            if self.predicate(before, after):
                return Conflict(rule_id=self.id, metrics=_numify(merged),
                                expected=self.expected,
                                observed=self.observed_tpl.format(
                                    **{f"b_{k}": v for k, v in before.items()},
                                    **{f"a_{k}": v for k, v in after.items()}))
        except (KeyError, TypeError, ValueError):
            return None
        return None


def _numify(d: dict) -> dict:
    return {k: (round(float(v), 4) if isinstance(v, (int, float)) else str(v))
            for k, v in d.items()}


# 相对变化率工具：b 为基准
def _rise(a: float, b: float) -> float:
    return (a - b) / max(abs(b), 1e-9)


RULES: list[Rule] = [
    # ---- snapshot：物理不可能组合 ----
    Rule(
        id="S1_PASS_GT_DRAW",
        kind="snapshot",
        predicate=lambda m: m["setPassCalls"] > m["drawCalls"],
        expected="每次 draw 必属于某个 SetPass 组 ⇒ setPassCalls ≤ drawCalls",
        observed_tpl="setPass={setPassCalls} > drawCalls={drawCalls}：渲染计数来自不同帧或读数不可信",
        note="触发即 A 档 bundle 标数据可疑，禁用于判定（同 instrument_distorted 通道）",
    ),
    Rule(
        id="S2_TRI_WITH_VERT_ZERO",
        kind="snapshot",
        predicate=lambda m: m["triangles"] > 0 and m["vertices"] == 0,
        expected="三角形必由顶点构成 ⇒ triangles>0 时 vertices>0",
        observed_tpl="triangles={triangles} 但 vertices=0：渲染计数组合物理不可能",
    ),
    Rule(
        id="S3_MEM_JUMP_NO_GC",
        kind="snapshot",
        predicate=lambda m: "baseline_totalAllocatedBytes" in m
        and "baseline_gc_alloc_per_frame" in m
        and (m["totalAllocatedBytes"] - m["baseline_totalAllocatedBytes"]) > 10e6
        and abs(_rise(m["gc_alloc_per_frame"], m["baseline_gc_alloc_per_frame"])) < 0.2,
        expected="内存台阶跳涨 >10MB 而每帧脚本分配不动 ⇒ 源头不是热路径分配",
        observed_tpl="totalAllocated 高出 {baseline_totalAllocatedBytes} 基线超 10MB，"
                 "gc_alloc_per_frame={gc_alloc_per_frame} 与基线差 <20%",
        note="归因方向裁定：排除 PB-REUSE-BUFFER 类修法；指向同步加载/纹理上传/常驻缓存。"
             "实测正例=F-LOAD-01 vs CLEAN（Δ28.6MB、gc 双方 8371 中位）",
    ),
    # ---- pair：修复未生效但数字动了 ----
    Rule(
        id="P1_DENSITY_COLLAPSE",
        kind="pair",
        predicate=lambda b, a: _rise(a["drawCalls"], b["drawCalls"]) > 0.2
        and _rise(a["triangles"], b["triangles"]) < 0.3 * _rise(a["drawCalls"], b["drawCalls"]),
        expected="drawCalls 上升 ≥20% 时 triangles 应大致同比；塌缩=碎描（每笔越画越空）",
        observed_tpl="drawCalls {b_drawCalls}→{a_drawCalls} 而 triangles 仅 {b_triangles}→{a_triangles}："
                 "涨幅严重不成比例（碎描签名）",
        note="实测签名例=F-GPU-01 vs CLEAN：+170% draw vs +44% tri，密度 46→24.6 tri/draw。"
             "水位场景命中=检出合批失效；验证窗命中=改动引入碎描（视同劣化）",
    ),
    Rule(
        id="P2_DRAW_DOWN_NO_STRUCT",
        kind="pair",
        predicate=lambda b, a: _rise(a["drawCalls"], b["drawCalls"]) < -0.05
        and a["batches"] == b["batches"] and a["setPassCalls"] == b["setPassCalls"]
        and a["triangles"] == b["triangles"],
        expected="drawCalls 降 ≥5% 必有结构量跟进（batches/setPass/triangles 至少一动）",
        observed_tpl="drawCalls {b_drawCalls}→{a_drawCalls} 但 batches/setPass/triangles 全部原封不动："
                 "场景未重载或读数来自改动前",
        note="Verify 防「改了个寂寞还报成功」：命中则改后 bundle 整体作废重采",
    ),
]


def run_snapshot(metrics: dict) -> list[Conflict]:
    """对单次采样（dict）跑全部 snapshot 规则。缺键不判（I2）。"""
    out = []
    for r in RULES:
        c = r.check_snapshot(metrics)
        if c:
            out.append(c)
    return out


def run_pair(before: dict, after: dict) -> list[Conflict]:
    """验证窗：改前/改后两组指标跑全部 pair 规则。"""
    out = []
    for r in RULES:
        c = r.check_pair(before, after)
        if c:
            out.append(c)
    return out
