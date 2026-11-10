"""UI 动作层：状态派生 + 动作白名单 + 确认强度（阶段 4）。

为什么要与渲染层分开：按计划要求，交互规则必须能被**按键序列驱动测试**
（test_ui_actions.py 喂一串键，断言每步该做什么/该拒什么）。渲染和动作
混在一个 while 里，就只能在真终端上手点，而这些恰恰是最容易出错的部分
（状态误判 ⇒ 在错误的时刻发出破坏性命令）。

三条纪律：
1. **状态只从文件派生，UI 不自造**：running/phase/busy/open_tickets/alerts
   全来自 daemon_status.json 与工单目录。UI 自己的内存态不可信（I9 同型）。
2. **禁用动作显示并附原因，不隐藏**：藏起来会让人以为功能缺失；灰掉并
   写出"为什么现在不行"才能教会人这套规则。
3. **确认强度按可逆性分档**：只读=按了就做；可逆=回车再确认；
   不可逆（批准/退出/注册工程）=必须手输 token。肌肉记忆是审批的头号敌人。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from upi.core import events as E

# ─────────────── 状态 ───────────────
NO_DAEMON = "no_daemon"
IDLE = "idle"
ROUND_RUNNING = "round_running"
AWAITING = "awaiting_decision"
PAUSED = "paused"
DEGRADED = "degraded"
UNCALIBRATED = "uncalibrated"

ALL_STATES = (NO_DAEMON, IDLE, ROUND_RUNNING, AWAITING, PAUSED,
              DEGRADED, UNCALIBRATED)

# 确认强度
TIER_NONE = "none"        # 只读：按了就走
TIER_SOFT = "confirm"     # 可逆：回车确认
TIER_HARD = "token"       # 不可逆：手输 token


def is_calibrated(runs_root: Optional[Path] = None) -> bool:
    """有无可用标定。无标定 ⇒ 检出不等于可信，整个链路只能看不能修。

    判据取阈值文件存在性，与 detect 链"无标定禁止判定"同一条约束，
    不在这里另立标准。⚠️ 目录必须跟**当前工程档案**走（真工程实测撞出）：
    阶段 8 起阈值按工程分目录（baselines_dir_rel），这里若还写死仓库根的
    "baselines"，新工程明明标定好了，UI 仍永远显示"未标定"并把 [r] 置灰——
    向导跑完看不到效果，等于向导白做（算了不往上抛的产物层变体）。
    """
    from upi.config.project_profile import active_profile
    rel = active_profile().baselines_dir_rel
    p = Path(rel)
    root = p if p.is_absolute() else (runs_root or E.RUNS).parent / rel
    return bool(list(root.glob("thresholds_*.json")))


def derive_state(st: dict | None, *, calibrated: bool = True,
                 open_tickets: list | None = None) -> str:
    """从状态快照派生当前态。顺序即优先级，注释说明为什么这样排。"""
    running = bool(st) and st.get("updated_at") and not _hb_stale(st)
    if not running:
        # 守护没在跑：一切动作都从"先把守护起来"开始
        return NO_DAEMON
    # 告警优先于业务态显示：坏文件/丢盘这类事被"正在跑"盖住就没人处理了
    if st.get("alerts") or st.get("degraded"):
        return DEGRADED
    if not calibrated:
        return UNCALIBRATED
    if st.get("busy"):
        return ROUND_RUNNING
    ot = open_tickets if open_tickets is not None else (st.get("open_tickets") or [])
    if ot:
        return AWAITING
    if st.get("paused"):
        return PAUSED
    return IDLE


def _hb_stale(st: dict, max_age_s: int = 15) -> bool:
    ts = st.get("updated_at")
    if not ts:
        return True
    try:
        t = time.mktime(time.strptime(str(ts), "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return True
    return (time.time() - t) > max_age_s


# ─────────────── 动作表 ───────────────
# 每项：键 / 名称 / 发给守护的命令 / 确认档 / 该态下可用否 / 不可用的原因
# 命令名与 daemon._dispatch_cmd 的 known 必须一致（test 会做交叉断言）。
ACT_START_DAEMON = "start_daemon"
ACT_STOP_DAEMON = "stop_daemon"
ACT_TOGGLE_SAMPLING = "toggle_sampling"
ACT_PAUSE = "pause"
ACT_RESUME = "resume"
ACT_RUN_NOW = "run_now"
ACT_CANCEL = "cancel"
ACT_VIEW_TICKET = "view_ticket"
ACT_DECIDE = "decide"
ACT_CONFIG = "config"
ACT_REGISTER = "register_project"
ACT_CALIBRATE = "calibrate"
ACT_FLOOR = "edit_floor_table"
ACT_SNAPSHOT = "snapshot"
ACT_QUIT_UI = "quit_ui"

# 态 → 动作可用性。缺省=不可用（fail-closed：新增动作默认在所有态禁用，
# 逼着加它的人显式想清楚"什么时候能做"，而不是默认哪儿都能点）
_MATRIX: dict[str, set[str]] = {
    NO_DAEMON: {ACT_START_DAEMON, ACT_SNAPSHOT, ACT_QUIT_UI, ACT_REGISTER,
                ACT_FLOOR, ACT_VIEW_TICKET},
    IDLE: {ACT_TOGGLE_SAMPLING, ACT_PAUSE, ACT_RUN_NOW, ACT_VIEW_TICKET,
           ACT_CONFIG, ACT_REGISTER, ACT_CALIBRATE, ACT_FLOOR,
           ACT_SNAPSHOT, ACT_QUIT_UI, ACT_STOP_DAEMON},
    ROUND_RUNNING: {ACT_CANCEL, ACT_SNAPSHOT, ACT_QUIT_UI, ACT_VIEW_TICKET,
                    ACT_CONFIG},
    AWAITING: {ACT_VIEW_TICKET, ACT_DECIDE, ACT_SNAPSHOT, ACT_QUIT_UI,
               ACT_STOP_DAEMON},
    PAUSED: {ACT_RESUME, ACT_SNAPSHOT, ACT_QUIT_UI, ACT_VIEW_TICKET,
             ACT_CONFIG, ACT_STOP_DAEMON},
    DEGRADED: {ACT_SNAPSHOT, ACT_QUIT_UI, ACT_VIEW_TICKET, ACT_CONFIG,
               ACT_RESUME, ACT_TOGGLE_SAMPLING},
    UNCALIBRATED: {ACT_CALIBRATE, ACT_SNAPSHOT, ACT_QUIT_UI, ACT_VIEW_TICKET,
                   ACT_CONFIG, ACT_REGISTER, ACT_FLOOR, ACT_STOP_DAEMON},
}

_REASONS = {
    ACT_RUN_NOW: {
        ROUND_RUNNING: "本轮正在跑，叠一轮会抢同一个编辑器（G7 不可比）",
        AWAITING: "有工单在等你决定；此时开新轮会出重复工单",
        PAUSED: "已暂停。先按 p 继续",
        DEGRADED: "存在未处理的告警，先看清楚再采集",
        NO_DAEMON: "守护没在跑，先起守护",
        UNCALIBRATED: "无标定基线，检出结果不可信（§8.1）"},
    ACT_CANCEL: {
        IDLE: "当前没有进行中的轮次",
        AWAITING: "守护此刻在等你的决定，没在采集",
        PAUSED: "已暂停，无需取消",
        NO_DAEMON: "守护没在跑"},
    ACT_PAUSE: {ROUND_RUNNING: "轮次进行中不接暂停（等本轮结束或取消）",
                AWAITING: "有工单待决，先处理工单",
                PAUSED: "已经是暂停态", NO_DAEMON: "守护没在跑"},
    ACT_RESUME: {PAUSED: None, NO_DAEMON: "守护没在跑"},
    ACT_DECIDE: {
        IDLE: "没有待批工单", ROUND_RUNNING: "本轮在跑，批单会打断判定链",
        NO_DAEMON: "守护没在跑，批准也没人执行",
        UNCALIBRATED: "无标定，不该产出工单"},
    ACT_REGISTER: {ROUND_RUNNING: "轮次进行中不切换工程上下文",
                   AWAITING: "有工单待决，先决定它"},
    ACT_FLOOR: {ROUND_RUNNING: "本轮正在采样，中途改下限会让这轮一半旧一半新",
                AWAITING: "有工单在等决定，先别改判据",
                DEGRADED: "存在未处理告警，先看清再改判据",
                PAUSED: "已暂停：先继续或改完再采"},
    ACT_CALIBRATE: {ROUND_RUNNING: "轮次进行中不能并发标定",
                    AWAITING: "有工单待决", NO_DAEMON: "守护没在跑"},
    ACT_STOP_DAEMON: {
        ROUND_RUNNING: "本轮在跑：请先取消（c），等子进程收尾后再停守护",
        NO_DAEMON: "守护本来就没在跑"},
    ACT_START_DAEMON: {IDLE: "守护已在运行"},
    ACT_TOGGLE_SAMPLING: {NO_DAEMON: "守护没在跑",
                          ROUND_RUNNING: "轮次进行中改开关会让本轮结论难解释"},
}


_ALL_ACTIONS = [
    {"key": "b", "name": ACT_START_DAEMON, "label": "启动守护",
     "tier": TIER_SOFT, "cmd": None},
    {"key": "s", "name": ACT_TOGGLE_SAMPLING, "label": "开/关采样",
     "tier": TIER_SOFT, "cmd": None},              # enable/disable 按实况定
    {"key": "p", "name": ACT_PAUSE, "label": "暂停排期",
     "tier": TIER_SOFT, "cmd": "pause"},
    {"key": "p", "name": ACT_RESUME, "label": "继续排期",
     "tier": TIER_NONE, "cmd": "resume"},
    {"key": "r", "name": ACT_RUN_NOW, "label": "立即巡检一轮",
     "tier": TIER_SOFT, "cmd": "run_now"},
    {"key": "c", "name": ACT_CANCEL, "label": "取消本轮",
     "tier": TIER_SOFT, "cmd": "cancel"},
    {"key": "a", "name": ACT_VIEW_TICKET, "label": "看待批工单",
     "tier": TIER_NONE, "cmd": None},
    {"key": "d", "name": ACT_DECIDE, "label": "批准/驳回工单",
     "tier": TIER_HARD, "cmd": None},
    {"key": "k", "name": ACT_CONFIG, "label": "改运行配置",
     "tier": TIER_SOFT, "cmd": "set"},
    {"key": "n", "name": ACT_REGISTER, "label": "注册新工程",
     "tier": TIER_HARD, "cmd": None},              # 本地流程：落盘走 onboarding
    {"key": "l", "name": ACT_CALIBRATE, "label": "跑标定向导",
     "tier": TIER_HARD, "cmd": "calibrate"},        # 走守护命令通道（抢编辑器）
    {"key": "f", "name": ACT_FLOOR, "label": "改下限目标表",
     "tier": TIER_HARD, "cmd": None},              # 本地流程：写工程侧判据文件
    {"key": "x", "name": ACT_SNAPSHOT, "label": "打快照(只读)",
     "tier": TIER_NONE, "cmd": None},
    {"key": "q", "name": ACT_QUIT_UI, "label": "退出界面(不停守护)",
     "tier": TIER_NONE, "cmd": None},
    {"key": "Q", "name": ACT_STOP_DAEMON, "label": "停止守护并退出",
     "tier": TIER_HARD, "cmd": "quit"},
]

def actions_for(state: str) -> list[dict]:
    """返回该态下**全部已知动作**及其可用性（可用的排前面）。

    为什么返回全部而不是只返回可用的：计划要求"禁用项显示并附原因"。
    隐藏禁用项会让人以为功能不存在，下次在错误的态里按它就摸不着头脑。
    """
    allowed = _MATRIX.get(state, set())
    out = []
    for act in _ALL_ACTIONS:
        en = act["name"] in allowed
        reason = ""
        if not act.get("wired", True):
            # 未接线的动作**不能显示为可用**：菜单里给一个按下去必然失败
            # 的项，比不给更糟——用户会以为已经注册/标定过了。
            # 等阶段 7/8 实现后把 wired 改成 True 即可自动解禁。
            en, reason = False, f"该能力尚未接线（{act.get('stage')}）"
        elif not en:
            reason = _REASONS.get(act["name"], {}).get(state) or \
                f"当前状态 {state} 不允许该动作"
        out.append({**act, "enabled": en, "disabled_reason": reason})
    out.sort(key=lambda a: (not a["enabled"], a["key"]))
    # ⚠️ 同键去重：p 在 IDLE 下是"暂停"、在 PAUSED 下是"继续"，两个动作
    #   共用一个键。若照原样列进菜单，会出现 "[p]暂停排期"（可用）与
    #   "[p]继续排期"（置灰）并排显示——看着像键冲突或功能坏了。
    #   规则：某键只要有可用动作，就不显示同键的禁用项（resolve() 本来就
    #   优先返回可用者，两者口径一致）。全被禁的键才显示，用于给出原因。
    seen = {a["key"] for a in out if a["enabled"]}
    return [a for a in out if a["enabled"] or a["key"] not in seen]


def daemon_commands() -> set[str]:
    """守护实际认识的命令集（从源码 known 元组取，不凭记忆写第二份）。

    为什么动态取：动作表与守护命令表是两张必须对齐的清单，硬编码一份
    就会出现"菜单里有、守护按 command_ignored 拒掉"的错位（本轮实测
    抓到 4 个）。取源码而不是 import，是为了让校验在守护未被导入时也能跑。
    """
    import ast
    src = (Path(__file__).resolve().parents[2] / "upi" / "agents"
           / "daemon.py").read_text(encoding="utf-8")
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Assign):
            for tg in n.targets:
                if isinstance(tg, ast.Name) and tg.id == "known":
                    return {e.value for e in n.value.elts}
    return set()


def consistency_report() -> dict:
    """动作表 ↔ 守护命令表 的交叉校验（测试与自检都用它）。"""
    known = daemon_commands()
    names = {a["name"] for a in _ALL_ACTIONS}
    sent = {a["cmd"] for a in _ALL_ACTIONS
            if a.get("cmd") and a.get("wired", True)}
    return {
        "unknown_cmds": sorted(sent - known),
        "matrix_orphans": {s: sorted(v - names)
                           for s, v in _MATRIX.items() if v - names},
        "no_quit": [s for s in ALL_STATES
                    if ACT_QUIT_UI not in _MATRIX.get(s, set())],
        "wired": sorted(a["name"] for a in _ALL_ACTIONS
                        if not a.get("wired", True)),
    }


# ⚠️ cmd=None 的动作各有原因，不是漏填：
#   · view_ticket / decide 走文件与意向通道（E.stage_decide），不发命令——
#     批准凭据必须由守护经 ApprovalStore 单点落盘（阶段 2 的单一写入者约定）；
#   · snapshot 是本地只读渲染，根本不涉及守护；
#   · start_daemon 由 UI 直接拉起分离进程（阶段 6），也不走命令通道；
#   · register_project / edit_floor_table（阶段 8 接入向导）是**本地落盘**：
#     写 projects/<id>.json 与工程侧 floor 文件都经 onboarding 的写出口，
#     不碰编辑器也不占命令槽。注册改的是"下次进程默认用哪个工程"的指针，
#     当前守护仍跑老工程——所以它天然是 UI 侧动作，不该发给守护（守护没有
#     "切换工程"这种命令，发过去只会 command_ignored）。
# 把它们塞进 cmd 列只会得到 daemon 的 command_ignored，看起来像功能坏了。

# p 键在两态下含义不同（暂停 / 继续），显式声明避免动作表里靠顺序猜
_KEY_ALIAS = {PAUSED: {"p": ACT_RESUME}}


def resolve(state: str, key: str) -> Optional[dict]:
    """把一次按键解析成动作。返回 None=该键在此态无定义。"""
    name = _KEY_ALIAS.get(state, {}).get(key)
    acts = actions_for(state)
    for a in acts:
        if (name and a["name"] == name) or (not name and a["key"] == key):
            return a
    return None


def check_guard(state: str, act: dict, st: dict | None) -> tuple[bool, str]:
    """动作放行判定。不可用 ⇒ 返回原因，绝不"看起来做了其实没做"。"""
    if act["enabled"]:
        return True, ""
    return False, act["disabled_reason"]
