"""Orchestrator（LangGraph 状态机，AGENTS §1.1 / DESIGN §4 闭环）。

职责边界（§1.1）：只做调度、状态持久化、失败重试、终止——
**不判断技术方案，不直接修改工程文件**（写入全部经 fixer，
回滚全部经 snapshot 的 git 通道）。

七节点循环：detect → diagnose → plan → fix → verify → review → record
外加 escalate（needs_human 终态出口）。每个节点一个 trace span（I10），
预算护栏在节点入口统一检查（G11：任一超限 → needs_human，不许烧积分打转）。

信息隔离（§1.6）：review 节点的输入是白名单构造的——baseline 证据 +
Hypothesis + EditPlan diff + 改后全量数据 + Verdict；Fix 的自我辩护、
自评置信度、自然语言解释一律不进。当前 review 裁定为确定性规则
（Verdict 语义 + 配额），LLM 化是可选增强，隔离约束对两种实现同样生效。

运行态持久化：runs/<run_id>/state.json 每节点退出时落盘（断点可续）。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from upi.core import events as E
from upi.tooling.trace import Tracer, environment_fingerprint

ROOT = Path(__file__).resolve().parents[2]


class RunState(TypedDict, total=False):
    run_id: str
    fixture: str
    quality_tier: str
    budget: dict                  # G11: max_issues/max_review_rounds/
                                  # max_attempts_per_issue/wallclock_s/max_tokens
    spent: dict                   # 实测消耗：tokens/attempts/review_rounds/wallclock_s
    findings: list                # Finding dicts（issue ∪ potential）
    issue: Optional[dict]         # 当前进入循环的 Issue
    hypotheses: list              # Hypothesis dicts，按 C_attr 降序
    edit_plan: Optional[dict]
    snapshot: Optional[dict]
    pre_attempt: Optional[dict]   # fix 节点写入前锁定：before 束路径+改前态 fresh
    approval_request: Optional[dict]  # 先报后批闸的工单位置（awaiting/rejected 停轮）
    verdict: Optional[dict]
    review_decision: Optional[str]
    review_reason: Optional[str]
    attempts: int
    route: str                    # proceed / needs_human / budget_exceeded / done
    records: list                 # AttemptRecord 级轨迹
    error: Optional[str]
    degraded: list                # 本轮"做不了但必须说出去"的降级项：
                                  # state_write_failed（每节点落盘失败）等。
                                  # 必须是正式字段——早期只写在 ctx 上，
                                  # trace 与 UI 都不消费它，等于把告警丢进黑洞。


def _plain(v):
    """pydantic 模型 → dict（state.json 落盘用），其余原样。"""
    if hasattr(v, "model_dump"):
        return v.model_dump()
    return v


def budget_check(st: RunState, node: str = "") -> Optional[str]:
    """G11 预算护栏。必须**节点感知**（批准后链路实测撞出的语义错）：

    - wallclock / tokens：全局消耗，任何节点入口都拦（record/escalate 除外）；
    - attempts：**只在 fix 入口拦**。它是"要不要开始新一次 attempt"的闸。
      旧版不分节点，attempts 满后 verify 入口也被拦——于是已落地的修复
      拿不到判定：双侧采样成本里 before 侧已花掉、after 侧没跑，
      整轮 attempt 白烧，还得靠出口回滚把脏写擦掉。拦在写入之前才是正解：
      既不烧采样，也不产生需要回滚的脏写。
    - review_rounds：同理只在 review 入口拦（G6 配额）。
    """
    b, sp = st.get("budget", {}), st.get("spent", {})
    if sp.get("wallclock_s", 0) > b.get("wallclock_s", 2400):
        return "budget_exceeded(wallclock)"
    if sp.get("tokens", 0) > b.get("max_tokens", 60_000):
        return "budget_exceeded(tokens)"
    if node == "fix" and st.get("attempts", 0) >= b.get("max_attempts_per_issue", 3):
        return "budget_exceeded(attempts)"
    if node == "review":
        rr = [r for r in st.get("records", []) if r.get("kind") == "review"]
        if len(rr) >= b.get("max_review_rounds", 3):
            return "budget_exceeded(review_rounds)"
    return None


def _wrap(name: str, fn, tracer: Tracer):
    """节点包装：预算前置检查 + trace span + 异常成状态不炸循环。"""
    def inner(st: RunState) -> RunState:
        over = budget_check(st, name)
        if over and name not in ("escalate", "record"):
            out = dict(st)
            out.update(route="needs_human", error=over)
            return out
        t0 = time.time()
        issue_id = (st.get("issue") or {}).get("id") if isinstance(
            st.get("issue"), dict) else None
        with tracer.stage(name, issue_id=issue_id) as ctx:
            try:
                delta = fn(dict(st))
                ctx["exit_status"] = 0
            except Exception as e:  # noqa: BLE001 — 失败进状态，由路由处置
                delta = {"route": "needs_human",
                         "error": f"{type(e).__name__}: {str(e)[:300]}"}
                ctx["exit_status"] = 1
        sp = dict(st.get("spent", {}))
        sp["wallclock_s"] = round(sp.get("wallclock_s", 0.0) + (time.time() - t0), 1)
        # 节点用 delta["tokens_used"] 上报真实消耗（只有 LLM 节点有）；
        # 上一版写成三元表达式塞在一行里，读起来像 bug 且 pop 时机含混，拆开。
        used = delta.pop("tokens_used", None)
        if isinstance(used, int):
            sp["tokens"] = sp.get("tokens", 0) + used
        delta.setdefault("spent", sp)
        # 每节点退出即落盘（docstring 承诺的"断点可续"要真做到）：
        # 近似合并态 {**st, **delta}——LangGraph 稍后还会写权威终态
        merged = dict(st)
        merged.update(delta)
        # 原子写 + 失败留痕。为什么两件事都要：
        #   · 半写更危险——非原子的 write_text 崩在中途会留下截断的 state.json，
        #     而守护的续跑判据 `_tickets()` 正读这个文件；文件损坏时它既不算
        #     "已批准待续跑"也不算"待批"，那张工单就永久隐形（实测缺口）。
        #   · 静默失败同样危险——旧写法 `except OSError: pass` 丢盘零留痕。
        # 失败后仍继续跑本节点：中断会把已花的采样成本一起作废，而终态还有
        # 一次 persist 机会；但必须让 UI 看得见（写进 state 的 degraded 字段）。
        # 通道语义已实测（Python3.14 + 本机 langgraph）：节点**不回传**某键时
        # 旧值保留，**回传**才是覆盖。所以只在真要追加时才回传，且必须先合并
        # 旧值——否则前序节点的降级告警会被静默冲掉，正好复现"告警进黑洞"。
        if not E.write_json(tracer.dir / "state.json",
                            {k: _plain(v) for k, v in merged.items()},
                            what="state_per_node", strict=False):
            # 专用事件名：让"哪一阶段把状态写丢了"可直接被检索到
            # （泛化的 write_failed 只知道文件名，不知道跑到哪一步）
            E.emit("state_write_failed", stage=name, run_id=tracer.run_id,
                   seq=getattr(tracer, "_seq", None))
            prev_deg = list(st.get("degraded") or [])
            if "state_write_failed" not in prev_deg:
                prev_deg.append("state_write_failed")
            delta["degraded"] = prev_deg
        return delta
    return inner


def _route_or(next_name: str):
    """条件路由：needs_human 走 escalate，否则走既定节点。"""
    def r(st: RunState) -> str:
        return "escalate" if st.get("route") == "needs_human" else next_name
    return r


def _after_verify(st: RunState) -> str:
    if st.get("route") == "needs_human":
        return "escalate"
    v = st.get("verdict") or {}
    status = v.get("status") if isinstance(v, dict) else getattr(v, "status", None)
    if status == "resolved":
        return "record"
    return "review"


def _after_review(st: RunState) -> str:
    d = st.get("review_decision")
    if d == "continue_with_direction":
        return "plan"        # 带 reason 重选方案（§1.6 输出回传 Fix）
    if d == "rollback_and_replan":
        return "diagnose"    # 回滚已在 review 节点经 git 完成
    return "escalate"


def build_orchestrator(nodes: dict, tracer: Tracer, entry: str = "detect"):
    """nodes: 节点名→业务函数 (RunState)->增量dict。图拓扑固定（§1.1）。

    entry 可改：人工批准回路的"两阶段跑"需要在批准后从 fix 续跑——
    前序节点（detect/diagnose/plan）的产物已由 state.json 落盘，重跑等于
    白烧一遍采样与归因积分。入口换掉即可复用同一张图，不改判定逻辑。"""
    g = StateGraph(RunState)
    for name, fn in nodes.items():
        g.add_node(name, _wrap(name, fn, tracer))
    g.set_entry_point(entry)
    g.add_edge("detect", "diagnose")
    g.add_conditional_edges("diagnose", _route_or("plan"),
                            {"plan": "plan", "escalate": "escalate"})
    g.add_conditional_edges("plan", _route_or("fix"),
                            {"fix": "fix", "escalate": "escalate"})
    # fix→verify 走条件路由（patrol-once 实测撞出的缺口）：fix 早退
    # （awaiting_approval / rejected / diff_rejected / compile_failed_rolled_back）
    # 返回 route=needs_human 时，无条件边照样进 verify → 读不到 pre_attempt
    # 崩 KeyError，把干净的"待批"终态污染成假错误。
    g.add_conditional_edges("fix", _route_or("verify"),
                            {"verify": "verify", "escalate": "escalate"})
    g.add_conditional_edges("verify", _after_verify,
                            {"record": "record", "review": "review",
                             "escalate": "escalate"})
    g.add_conditional_edges("review", _after_review,
                            {"plan": "plan", "diagnose": "diagnose",
                             "escalate": "escalate"})
    g.add_edge("record", "escalate")   # 统一出口：RunReport 落盘
    g.add_edge("escalate", END)
    return g.compile()


class Runner:
    """一次 run 的生命周期：run 目录、state.json 落盘、最终 RunReport。"""

    def __init__(self, run_id: str, fixture: str, budget: Optional[dict] = None):
        self.dir = ROOT / "runs" / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tracer = Tracer(run_id, root=str(ROOT / "runs"), channel="dev",
                             env_fingerprint=environment_fingerprint(
                                 str(ROOT / "fixtures" / "upi-fixture")))
        self.budget = {"max_issues": 1, "max_review_rounds": 3,
                       "max_attempts_per_issue": 3, "wallclock_s": 2400,
                       "max_tokens": 60_000, **(budget or {})}
        self.fixture = fixture

    def initial_state(self) -> RunState:
        return {"run_id": self.dir.name, "fixture": self.fixture,
                "quality_tier": "urp-balanced", "budget": self.budget,
                "spent": {"wallclock_s": 0.0, "tokens": 0}, "findings": [],
                "issue": None, "hypotheses": [], "edit_plan": None,
                "snapshot": None, "verdict": None, "review_decision": None,
                "review_reason": None, "attempts": 0, "route": "proceed",
                "records": [], "error": None, "degraded": []}

    def persist(self, st: RunState):
        # 终态落盘失败必须上抛（strict=True）：这是整轮唯一的结果文件，
        # 静默失败会让上层以为"跑完了"，而磁盘上根本没有这一轮的判定记录。
        # 已花的采样成本与积分换不来留痕，是最坏的一种失败。
        E.write_json(self.dir / "state.json",
                     {k: _plain(v) for k, v in st.items()},
                     what="state_final")

    def run(self, nodes: dict, initial: Optional[RunState] = None,
            entry: str = "detect"):
        app = build_orchestrator(nodes, self.tracer, entry=entry)
        st = initial or self.initial_state()
        final = app.invoke(st)
        self.persist(final)
        return final

    def resume(self, nodes: dict, entry: str = "fix") -> RunState:
        """从 state.json 续跑（人工批准两阶段用）。

        不重跑前序节点：detect 的采样、diagnose 的归因积分已经花过，
        重跑等于白烧（I13）。预算计数（attempts/spent/records）本来就在
        state.json 里，续跑接着算，G11 不会因为"换了进程"而重置。"""
        fp = self.dir / "state.json"
        st, err = E.load_json(fp)
        if err == "missing":
            raise RuntimeError(f"无 state.json，不能续跑：{fp}")
        if err:
            # 损坏与缺失要分开报：损坏意味着"这一轮的进度已经不可恢复"，
            # 需要人去查为什么写坏了，不是简单重跑就能解决的
            raise RuntimeError(f"state.json 无法解析，拒绝续跑：{err}；"
                               f"文件 {fp} 的进度已不可信，请勿硬跑（会重复扣预算）")
        return self.run(nodes, initial=st, entry=entry)
