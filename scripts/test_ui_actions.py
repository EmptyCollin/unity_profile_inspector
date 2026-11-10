"""UI 动作层与按键流程测试（阶段 4 完成判据）。

被测的是"交互规则的电路"，不是画面：状态派生、动作白名单、确认三档、
配置表单的接受/拒绝路径。全部用注入依赖驱动 step()，不开终端、不起守护、
不发真命令——拒绝路径（禁用、取消、越界、token 不符）恰恰是最需要测、
而真终端手点最测不到的部分。

总线经 E.isolate 指向临时区：不隔离就会把测试噪音写进真实事件留档，
而这类污染不会让测试变红（本轮已栽过三次，故把判据写进断言里）。
"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.agents import ui as U              # noqa: E402
from upi.agents import ui_actions as UA      # noqa: E402
from upi.core import events as E             # noqa: E402

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_uiact_"))
E.isolate(TMP / "runs")        # 全程隔离：任何留痕/命令/状态都不落真实 runs/
E.atomic_write(E.PID, str(__import__("os").getpid()))   # 让 daemon_running 为真


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'ok' if cond else 'FAIL'}] {name}"
          + ("" if cond else f"  ← {str(detail)[:130]}"))


def fresh_minute(offset_s: int = 0) -> str:
    return (dt.datetime.now() + dt.timedelta(seconds=offset_s)).strftime(
        "%Y-%m-%dT%H:%M:%S")


def st_of(**kw) -> dict:
    # conf 用守护真实导出的**扁平**结构（{key: 值}，键名=SPEC.key）。
    # 夹具形状必须跟真实生产者一致，否则 UI 测的是想象中的契约。
    base = {"updated_at": fresh_minute(), "pid": 4321, "busy": False,
            "paused": False, "open_tickets": [], "alerts": [],
            "degraded": False,
            "conf": {"interval_s": 1800, "windows": 4, "cooldown_s": 0,
                     "waterline_timeout_s": 900,
                     "max_consecutive_failures": 3,
                     "budget_max_review_rounds": 1,
                     "budget_max_attempts_per_issue": 1,
                     "budget_wallclock_s": 3600, "budget_max_tokens": 60000,
                     "auto_fix_enabled": False, "live_llm": False},
            "fixtures": ["CLEAN"]}
    base.update(kw)
    return base


# ───────── ① 状态派生 ─────────
print("== ① 状态只从文件派生 ==")
ok("无状态快照 ⇒ no_daemon", UA.derive_state(None) == UA.NO_DAEMON)
ok("心跳过期 ⇒ no_daemon（守护死了不能显示成空闲）",
   UA.derive_state(st_of(updated_at="2020-01-01T00:00:00")) == UA.NO_DAEMON)
ok("busy ⇒ round_running", UA.derive_state(st_of(busy=True)) == UA.ROUND_RUNNING)
ok("有告警 ⇒ degraded（告警优先于业务态）",
   UA.derive_state(st_of(alerts=[{"kind": "write_failed"}])) == UA.DEGRADED)
ok("无标定 ⇒ uncalibrated",
   UA.derive_state(st_of(), calibrated=False) == UA.UNCALIBRATED)
ok("告警优先于无标定（坏文件比没基线更要先处理）",
   UA.derive_state(st_of(alerts=[{"kind": "x"}]), calibrated=False)
   == UA.DEGRADED)
ok("有工单 ⇒ awaiting_decision",
   UA.derive_state(st_of(open_tickets=["a/request.md"])) == UA.AWAITING)
ok("paused ⇒ paused", UA.derive_state(st_of(paused=True)) == UA.PAUSED)
ok("其余 ⇒ idle", UA.derive_state(st_of()) == UA.IDLE)
ok("轮次进行中优先于待批工单",
   UA.derive_state(st_of(busy=True, open_tickets=["x"])) == UA.ROUND_RUNNING)


# ───────── ② 动作白名单逐格 ─────────
print("== ② 每个态的动作可用性 ==")
cases = [
    (UA.NO_DAEMON, {UA.ACT_START_DAEMON, UA.ACT_QUIT_UI, UA.ACT_SNAPSHOT},
     {UA.ACT_RUN_NOW, UA.ACT_CANCEL, UA.ACT_PAUSE, UA.ACT_STOP_DAEMON}),
    (UA.IDLE, {UA.ACT_RUN_NOW, UA.ACT_PAUSE, UA.ACT_TOGGLE_SAMPLING,
               UA.ACT_STOP_DAEMON, UA.ACT_CONFIG},
     {UA.ACT_CANCEL, UA.ACT_RESUME}),
    (UA.ROUND_RUNNING, {UA.ACT_CANCEL, UA.ACT_SNAPSHOT},
     {UA.ACT_RUN_NOW, UA.ACT_START_DAEMON, UA.ACT_PAUSE,
      UA.ACT_STOP_DAEMON, UA.ACT_CALIBRATE}),
    (UA.AWAITING, {UA.ACT_VIEW_TICKET, UA.ACT_STOP_DAEMON, UA.ACT_DECIDE},
     {UA.ACT_RUN_NOW, UA.ACT_CANCEL, UA.ACT_PAUSE}),
    (UA.PAUSED, {UA.ACT_RESUME}, {UA.ACT_RUN_NOW, UA.ACT_PAUSE}),
    (UA.UNCALIBRATED, {UA.ACT_SNAPSHOT, UA.ACT_REGISTER, UA.ACT_FLOOR},
     {UA.ACT_RUN_NOW, UA.ACT_CALIBRATE}),
]
# ⚠️ 两个期望必须避开"未接线"与"同键去重"这两条实现规则，否则测试与
#   实现互相矛盾：
#     · 未接线动作（calibrate）在所有态都置灰，不能期待可用；
#       register/floor 已在阶段 8 接线（本地流程），故 UNCALIBRATED 里
#       它们进 must_on —— 未标定态恰恰是"注册完还没标定"的状态，注册和
#       配下限表都该能点，只有标定本身因走守护命令通道仍置灰。
#     · 同键去重会把已可用键的禁用同态项从菜单里摘掉——"没列出"等于
#       "没被开启"，must_off 因此要按 .get(n, False) 判。
for state, must_on, must_off in cases:
    acts = {a["name"]: a["enabled"] for a in UA.actions_for(state)}
    bad_on = [n for n in must_on if not acts.get(n)]
    bad_off = [n for n in must_off if acts.get(n)]
    ok(f"{state}：{len(must_on)} 项应可用 / {len(must_off)} 项应禁用",
       not bad_on and not bad_off, f"缺可用={bad_on} 误开={bad_off}")

# 禁用项必须有原因（"显示但置灰"只有一半是显示）
for state, _on, must_off in cases:
    listed = {a["name"]: a for a in UA.actions_for(state)}
    checked = 0
    for n in must_off:
        a = listed.get(n)
        if a is None:
            continue                   # 被同键去重摘掉，不列即不误导，无需原因
        if not a["enabled"]:
            ok(f"{state}/{n} 禁用时带原因", bool(a["disabled_reason"]),
               "原因为空")
            checked += 1
            break
    if checked == 0 and must_off:
        ok(f"{state} 至少有一条带原因的禁用项或全被去重", True, "")

# 未接线动作在任何态都不得可用。
# ⚠️ register_project / edit_floor_table 已从这份名单移除——阶段 8 接成了本地
#   流程（cmd=None，落盘走 onboarding），不再是"按下去必然失败"的占位。
#   calibrate 仍在列：标定要占编辑器跑采样，必须走守护命令通道，那是下一批。
#   这条断言的作用就是"还没接的能力不许偷偷变成可点"。
print("== ②b 未接线能力一律置灰 ==")
for name in ("calibrate",):
    bad = [s for s in UA.ALL_STATES
           if any(a["name"] == name and a["enabled"]
                  for a in UA.actions_for(s))]
    ok(f"{name} 在所有态均不可用", not bad, str(bad))

# 同键去重：一个键只要有可用动作，就不该再列同键的禁用项
print("== ②c 同键不并排显示 ==")
for s in UA.ALL_STATES:
    acts = UA.actions_for(s)
    enabled_keys = {a["key"] for a in acts if a["enabled"]}
    dup = sorted({a["key"] for a in acts
                  if not a["enabled"] and a["key"] in enabled_keys})
    ok(f"{s}：无同键并排（可用+置灰同键）", not dup, str(dup))


# ───────── ③ 交叉一致性（动作表 ↔ 守护） ─────────
print("== ③ 动作表与守护命令对齐 ==")
r = UA.consistency_report()
ok("UI 不会发出守护不认识的命令", not r["unknown_cmds"], str(r["unknown_cmds"]))
ok("矩阵不引用不存在的动作", not r["matrix_orphans"], str(r["matrix_orphans"]))
ok("每个态都有退出键", not r["no_quit"], str(r["no_quit"]))
# 配置项必须真被守护受理。⚠️ 上一版靠"读 daemon.py 源码、看字段名有没有
# 出现在 set 分支里"来断言——那既测不到行为（名字写在注释里也算过），又把
# 实现细节钉成了测试前提：把 set 改成由 runtime_spec 派生后源码不再写字段
# 字面量，它会假失败得像"产品坏了"。改成直接喂守护的 set 分支看 conf 变不变。
print("== ③b 守护 set 分支按 SPEC 逐项受理 ==")
from upi.agents.daemon import Daemon
from upi.config.runtime_spec import SPEC as _SPEC


def _daemon_with(conf):
    d = Daemon.__new__(Daemon)                # 绕开 __init__（不心跳、不排期）
    d.conf = conf
    return d


for item in _SPEC:
    E._p_conf().unlink(missing_ok=True)             # 每项从干净覆盖层起步
    probe = _daemon_with({"fixtures": ["CLEAN"]})   # 带一个绝不该被 set 动的键
    if item.kind is bool:
        good = True
    else:
        good = item.lo if item.lo else (item.hi // 2 if item.hi else 1)
    probe._dispatch_cmd({"cmd": "set", "args": {item.key: good}})
    ok(f"守护受理 {item.key}", item.get(probe.conf) == good,
       f"得到 {item.get(probe.conf)!r}")
    # 覆盖层只能有改动项：整份写盘会把从没改的键也固化，yaml 打底从此失效
    saved = E.load_conf()
    ok(f"{item.key} 未把无关键固化进覆盖层", "fixtures" not in saved,
       str(list(saved)))

# 嵌套项落盘位置正确 + 重启能读回来（这是 UI 化配置的真实收益：改了要活过重启）
E._p_conf().unlink(missing_ok=True)
_nb = _daemon_with({})
_nb._dispatch_cmd({"cmd": "set", "args": {"budget_max_review_rounds": 2,
                                          "live_llm": True}})
ok("嵌套 budget 写进 budget 子字典（不拍平成顶层怪键）",
   (_nb.conf.get("budget") or {}).get("max_review_rounds") == 2
   and "budget_max_review_rounds" not in _nb.conf, str(_nb.conf))
ok("嵌套 auto_fix 写进 auto_fix 子字典",
   (_nb.conf.get("auto_fix") or {}).get("live_llm") is True, str(_nb.conf))
_saved = E.load_conf()
ok("改动已落盘（活过重启）",
   (_saved.get("budget") or {}).get("max_review_rounds") == 2
   and (_saved.get("auto_fix") or {}).get("live_llm") is True, str(_saved))
_rt = _daemon_with({**E.load_conf()})   # 模拟重启：覆盖层打底再合并
ok("重启后读回同一值（覆盖层未被整份写盘污染）",
   (_rt.conf.get("budget") or {}).get("max_review_rounds") == 2, str(_rt.conf))
E._p_conf().unlink(missing_ok=True)

# 未知字段必须被拒且不留痕改内存
d2 = _daemon_with({"interval_s": 900})
d2._dispatch_cmd({"cmd": "set", "args": {"bogus_key": 1}})
ok("未知配置项被拒（不静默接受）", d2.conf.get("bogus_key") is None,
   str(d2.conf))
# 越界值必须被拒：间隔 1 秒会把积分当场烧穿
d3 = _daemon_with({"interval_s": 900})
d3._dispatch_cmd({"cmd": "set", "args": {"interval_s": 1}})
ok("越界配置被拒（interval<60 下限）", d3.conf["interval_s"] == 900,
   str(d3.conf["interval_s"]))


# ───────── ④ step()：确认三档与拒绝路径 ─────────
print("== ④ 按键驱动的确认与拒绝 ==")
sent = []


def spy_send(cmd, **args):
    """命令发送替身。

    ⚠️ 返回形状必须与真实 send 一致：(bool, str)。上一版第二元素返回 dict
    （照抄了总线 send_cmd_and_wait 的形状），于是任何"对返回文本做拼接/断言"
    的实现都只能在真机上才炸——实测就是这样在取消粒度提示这行抛 TypeError。
    替身失真 = 这套测试白跑。
    """
    sent.append((cmd, args))
    return True, f"已受理：{cmd}（seq={len(sent)}）"


def spy_line(prompt):
    lines.append(prompt)
    return answers.pop(0) if answers else ""


lines, answers = [], []

# 只读动作：按了就做，不要确认
msg, pend, quit_ = U.step(UA.IDLE, st_of(), "x", sender=spy_send)
ok("只读动作无需确认", pend is None and not quit_, f"{msg}|{pend}")
ok("只读动作不发命令（本地渲染）", not sent, str(sent))

# 软确认：第一次只挂起，不执行
sent.clear()
msg, pend, _ = U.step(UA.IDLE, st_of(), "r", sender=spy_send)
ok("可逆动作第一次只待确认", pend == UA.ACT_RUN_NOW and not sent,
   f"pend={pend} sent={sent}")
ok("待确认提示说明怎么生效", "再按" in msg, msg)
msg, pend, _ = U.step(UA.IDLE, st_of(), "r", pend, sender=spy_send)
ok("第二次同键才真发命令", pend is None and sent == [("run_now", {})],
   str(sent))

# 待确认时按别的键 = 作废，绝不能执行
sent.clear()
_, pend, _ = U.step(UA.IDLE, st_of(), "r", sender=spy_send)
msg, pend2, _ = U.step(UA.IDLE, st_of(), "s", pend, sender=spy_send)
ok("待确认时换键作废（不执行原动作）",
   pend2 != "run_now" and ("run_now", {}) not in sent, str(sent))

# 未识别键必须作废 pending —— 否则下一次按键会被当成它的确认而误执行
sent.clear()
_, pend, _ = U.step(UA.IDLE, st_of(), "r", sender=spy_send)
msg, pend2, _ = U.step(UA.IDLE, st_of(), "9", pend, sender=spy_send)
ok("未识别键清空 pending（防误确认）", pend2 is None and not sent,
   f"pend={pend2} sent={sent}")

# 禁用动作：回显原因，且不发命令
sent.clear()
msg, pend, _ = U.step(UA.ROUND_RUNNING, st_of(busy=True), "r", sender=spy_send)
ok("态不允许的动作不发命令", not sent, str(sent))
ok("并回显不可用原因", "不可用" in msg and "G7" in msg or "抢同一个编辑器" in msg,
   msg)

# 硬确认：token 不匹配则不执行
sent.clear()
answers, lines = ["WRONG"], []
msg, pend, _ = U.step(UA.IDLE, st_of(), "Q", sender=spy_send,
                      line_reader=spy_line)
ok("token 不符 ⇒ 不执行不可逆动作", not sent and "不匹配" in msg,
   f"{msg} sent={sent}")
answers, lines = ["Q"], []
msg, pend, _ = U.step(UA.IDLE, st_of(), "Q", sender=spy_send,
                      line_reader=spy_line)
ok("token 正确 ⇒ 发 quit 命令", sent == [("quit", {})], str(sent))
# 提示里必须含"请输入什么"，否则用户不知道该输什么（只读回显不算提示）
answers, lines = ["Q"], []
msg, _, _ = U.step(UA.IDLE, st_of(), "Q", sender=spy_send, line_reader=spy_line)
ok("硬确认提示含 label 与 token 要求", "停止守护" in lines[0] and "Q" in lines[0],
   lines[0][:120])

# q = 只退界面，绝不停守护
sent.clear()
msg, pend, quit_ = U.step(UA.IDLE, st_of(), "q", sender=spy_send)
ok("q 退出 UI 但不发 quit（无人值守不受影响）",
   quit_ is True and not sent, f"quit={quit_} sent={sent}")

# 取消的"不是立刻停"必须显式说出来。取消走文件通道（换来子进程 finally
# 真执行），粒度是下一个采样边界；界面若不提示，按下 c 之后看似毫无反应，
# 人只会连按几次或干脆硬杀进程——后者正是 G10 禁止的。
# 用轮次中状态测：c 在 idle 被守卫拦（"当前没有进行中的轮次"），根本到不了
# 发送分支——拿 idle 断言"最长提示"必然失败，而且失败得像是实现缺陷。
print("== ⑤b 取消的边界粒度提示 ==")
sent.clear()
msg, pend, _ = U.step(UA.ROUND_RUNNING, st_of(busy=True), "c", sender=spy_send)
ok("c 第一次只进待确认，不发命令", pend == "cancel" and not sent,
   f"pend={pend} sent={sent}")
msg, pend, _ = U.step(UA.ROUND_RUNNING, st_of(busy=True), "c", pend,
                      sender=spy_send)
ok("c 第二次才发 cancel", sent == [("cancel", {})], str(sent))
ok("取消已受理 ⇒ 说明等边界收尾并给出最长时限",
   "边界" in msg and "最长" in msg, msg[:100])
# 未受理时不得承诺"等边界"——命令根本没进通道，承诺只会误导人去等
msg, _, _ = U.step(UA.ROUND_RUNNING, st_of(busy=True), "c", "cancel",
                   sender=lambda n, **k: (False, f"通道忙 {n}"))
ok("取消未受理 ⇒ 不谎称已受理", "受理" not in msg and "边界" not in msg,
   msg[:80])


# ───────── ⑤ 配置表单 ─────────
print("== ⑤ 配置编辑：逐项、可取消、挡越界 ==")


def _idx(key):
    """SPEC 里该字段的表单序号（1 基）。⚠️ 绝不再写死数字——上一版用 "1"/"5"
    直接当序号，SPEC 从 6 项扩到 11 项后 "5" 指向的是别的字段，测试假失败得
    像产品坏了；按 key 反查才跟着 SPEC 走。"""
    return str(next(i for i, it in enumerate(_SPEC, 1) if it.key == key))


# 需求"任何配置都由 UI 支持"的直接机器校验：表单覆盖的字段 == SPEC 全部字段
ok("表单覆盖 SPEC 每一个可改字段（无一遗漏）",
   {k for k, _l, _t in U.CFG_FIELDS} == {i.key for i in _SPEC},
   str({i.key for i in _SPEC} - {k for k, _l, _t in U.CFG_FIELDS}))

sent.clear()
answers, lines = [_idx("interval_s"), "900"], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("改数值项 ⇒ 发 set 带字段与新值",
   sent == [("set", {"interval_s": 900})], str(sent))
ok("回显 old→new", "1800" in msg and "900" in msg, msg)

# 回车取消：不发命令
sent.clear()
answers, lines = [_idx("interval_s"), ""], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("新值留空 ⇒ 取消，不发命令", not sent and "取消" in msg, f"{msg} {sent}")

# 选项越界
sent.clear()
answers, lines = ["99"], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("选项越界 ⇒ 不改动", not sent and ("未改动" in msg or "未选择" in msg),
   f"{msg} {sent}")

# 类型不符
sent.clear()
answers, lines = [_idx("interval_s"), "abc"], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("非整数 ⇒ 拒绝提交", not sent and "无效" in msg, f"{msg} {sent}")

# 间隔下限（UI 这关就挡下，不发注定被守护回绝的命令）
sent.clear()
answers, lines = [_idx("interval_s"), "10"], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("间隔 <60s ⇒ 提前挡住不发命令（防高频烧积分）",
   not sent and "60" in msg, f"{msg} {sent}")

# 上界也挡：token 上限填一个失控值
sent.clear()
answers, lines = [_idx("budget_max_tokens"), "999999999"], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("超上界 ⇒ 挡住不发命令", not sent and "高于上限" in msg, f"{msg} {sent}")

# 布尔项
sent.clear()
answers, lines = [_idx("live_llm"), "y"], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("布尔项 y ⇒ 发 True", sent == [("set", {"live_llm": True})], str(sent))
sent.clear()
answers, lines = [_idx("live_llm"), "maybe"], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("布尔项乱填 ⇒ 拒绝（不当成 False 悄悄改）",
   not sent and "y/n" in msg, f"{msg} {sent}")

# 嵌套 budget 项也能改（这是"配置一律走 UI"新增的重点：以前只能手改 yaml）
sent.clear()
answers, lines = [_idx("budget_wallclock_s"), "7200"], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("嵌套 budget 项可改 ⇒ 发 set", sent == [("set", {"budget_wallclock_s": 7200})],
   str(sent))
ok("嵌套项回显当前值（从扁平 conf 取到 3600）", "3600" in msg, msg)


# 有修改代价的项（windows 进协议指纹）：必须先报代价并确认
print("== ⑤c 配置项的修改代价提示（SPEC.warn）==")
sent.clear()
answers, lines = [_idx("windows"), "n"], []          # 看了代价后拒绝
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("代价未确认 ⇒ 不发命令", not sent and "代价未确认" in msg, f"{msg} {sent}")
ok("代价提示出现在确认问句里", "不可比" in (lines[1] or ""), str(lines)[:2])
sent.clear()
answers, lines = [_idx("windows"), "y", "6"], []     # 确认代价再改
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("确认代价 ⇒ 正常提交", sent == [("set", {"windows": 6})], str(sent))
ok("提交结果重复代价（改完就该想起要重标定）", "不可比" in msg, msg[:120])
# 同值提交不该挂警告：填了个没变的窗口数，可比性没被破坏
sent.clear()
answers, lines = [_idx("windows"), "y", "4"], []     # st_of 的 windows 就是 4
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("值未变 ⇒ 不谎报代价（4→4 不该提示不可比）",
   sent == [("set", {"windows": 4})] and "不可比" not in msg, msg[:120])
# 无代价项不该多问一道确认（否则每个配置都要多按一次）
sent.clear()
answers, lines = [_idx("interval_s"), "900"], []
U.edit_config(st_of(), spy_send, spy_line)
ok("无 warn 项不多问确认（问答数=选项+新值）", len(lines) == 2, str(lines))


# ───────── ⑥ 命令通道语义（经总线，但不启守护） ─────────
print("== ⑥ send 的 ack 语义 ==")
# ⚠️ 上一版把"acked 路径"写成 E.ack_cmd(seq) 后再 send——那是错的：
#   ack 的是**上一条**的 seq，新发的命令 seq+1 依然没人回执，必然 no_ack。
#   测"已受理"必须模拟一个持续消费的守护（与 test_daemon 的 acker 同法）。
seq = E.send_cmd("pause")
ok("无人回执时 send 报未受理（不假装成功）",
   U.send("pause", timeout_s=0.4)[0] is False)
E.ack_cmd(seq)                         # 先清掉这条遗留 pending，否则下一条被 busy 拒
import threading
import time as _t
_stop = [False]


def _acker():
    # seen 必须从"当前已 acked 水位"起，不能从 0 起。真实守护是单写者消费模式：
    # 已受理过的 seq 绝不会再回头 ack。上一版从 0 起，会对主线程**早已 ack 掉的
    # seq=1 再补一次多余 ack**——这次 ack 的 read-modify-write 撞上主线程正在写
    # seq=2，就把新命令整条覆盖没了（不是崩，是丢更新），wait_for_ack 拿不到 acked≥2
    # ⇒ 间歇超时。这正是"测试替身不忠实还原真实语义会掩盖/制造 bug"的活例。
    seen = int((E.cmd_state() or {}).get("acked_seq", 0) or 0)
    while not _stop[0]:
        stt = E.cmd_state()
        if stt["seq"] > seen:
            seen = stt["seq"]
            E.ack_cmd(seen)
        _t.sleep(0.05)


_th = threading.Thread(target=_acker, daemon=True)
_th.start()
ok("有守护回执 ⇒ send 报已受理", U.send("resume", timeout_s=2.0)[0] is True)
# 停掉模拟回执（同 test_daemon 的教训：留着会把后续用例的 pending 抹平）
_stop[0] = True
_th.join(timeout=2)

# 动态菜单真的按状态变。
# 先造一份假标定：合成区没有 baselines/thresholds_*.json 时 derive_state
# 会判成 uncalibrated，[r] 出现在**置灰行**里——上一版用子串断言分不出
# "可用行"与"置灰行"，误报成实现缺陷（菜单其实显示正确）。
print("== ⑦ 渲染层：菜单随状态变 ==")
_b = TMP / "baselines"
_b.mkdir(exist_ok=True)
(_b / "thresholds_fake.json").write_text("{}", encoding="utf-8")
ok("假标定就位 ⇒ 态可进 idle", UA.is_calibrated() is True)

import re


def menu_keys(frame):
    """解析帧底部菜单，返回 (可用键集合, 置灰键集合)。

    按**行角色**取而不是全文子串：置灰行也写着 "[r]立即巡检"，
    子串断言会把"禁用但带原因显示"误判成"可用"——那正是本 UI 的
    设计特性（显示并附原因），断言必须能区分两者。
    """
    on, off = set(), set()
    for ln in frame.splitlines():
        plain = re.sub(r"\x1b\[[0-9;]*m", "", ln)
        keys = set(re.findall(r"\[([A-Za-z])\]", plain))
        if not keys:
            continue
        if plain.strip().startswith("置灰不可用："):
            off |= keys
        elif "状态：" not in plain:            # 排除"指令 [状态：x]"标题行
            on |= keys
    return on, off


E.write_status(st_of(busy=False, open_tickets=[], alerts=[]))
on_idle, off_idle = menu_keys(U.snapshot())
ok("idle：[r] 可用且不在置灰行", "r" in on_idle and "r" not in off_idle,
   f"on={sorted(on_idle)} off={sorted(off_idle)}")
E.write_status(st_of(busy=True, open_tickets=[], alerts=[]))
on_run, off_run = menu_keys(U.snapshot())
ok("轮次中：[r] 被禁用（出现在置灰行）", "r" not in on_run and "r" in off_run,
   f"on={sorted(on_run)} off={sorted(off_run)}")
ok("轮次中：[c] 可用", "c" in on_run, str(sorted(on_run)))
E.write_status(st_of(busy=False, open_tickets=["runs/p/approvals/T/request.md"],
                     alerts=[]))
on_aw, off_aw = menu_keys(U.snapshot())
ok("待决态：[r] 禁用并带原因行", "r" not in on_aw and "r" in off_aw,
   f"on={sorted(on_aw)}")
ok("快照顶部给出当前状态名",
   "状态：" in U.snapshot())


# ───────── ⑧ 审批闭环（阶段 5） ─────────
print("== ⑧ 工单审批：逐页读完 + 页脚码 + 守护硬闸 ==")
# 借 test_m4_approval 的构造器造一张真工单（五件套齐、Pydantic 校验过）。
# 手写假 md 测不出真链路：request.md 必须由 render_markdown 生成，
# 分页/校验码才与生产一致。
from upi.core.approval import ApprovalStore, build_request, read_token, split_pages
from upi.core.models import (EditPlan, Evidence, Issue, IssueSignature)
from upi.core.attribution import FactorJudgment, build_hypothesis

_ev = [Evidence(tool="t", raw_ref="runs/x/e.json", value=77977.0,
                unit="B/frame", noise_band=1200.0, data_tier="A2")]
_plan = EditPlan(playbook_id="PB-T",
                 diffs=[{"file": "a/B.cs", "find": "x", "replace": "y",
                         "expect_count": 1, "_why": "测试"}],
                 invariants_assert=["不变"], side_effect="none",
                 touched_assets=["a/B.cs"], permission_tier="semi")
_iss = Issue(signature=IssueSignature(dim="drawCalls", metric="drawCalls",
                                      mechanism="m", threshold=30.0),
             link="gpu", evidence=list(_ev), magnitude=62.0,
             deviation_ratio=2.1, state="confirmed")
_js = [FactorJudgment(factor=f, value=0.8, evidence_ref="runs/x/e.json")
       for f in ("proximity", "explains_share", "competitiveness",
                 "prior_hit", "counterfactual", "mechanism")]
_hyp = build_hypothesis("rc", ["a/B.cs:1"], _js, _iss)
_req = build_request(run_id="apr1", issue=_iss, hypothesis=_hyp,
                     plan=_plan, permission_tier="semi",
                     reason_not_auto="测试", git_ref="upi/attempt-apr1")
_run_dir = TMP / "runs_apr" / "patrol_apr1"
_run_dir.mkdir(parents=True, exist_ok=True)   # 目录不存在就 write_text 会 FileNotFoundError
(_run_dir / "state.json").write_text('{"records": []}', encoding="utf-8")
_store = ApprovalStore(_run_dir)
_where = _store.submit(_req)
_rid = _req.request_id
_tok = read_token(_rid)
_md = (_run_dir / "approvals" / _rid / "request.md").read_text(encoding="utf-8")

ok("工单按五件套分页", len(split_pages(_md)) == 5, len(split_pages(_md)))
# 断言用**带标签**的页脚行，不用裸子串计数：正文全是数值与十六进制串，
# 4 位码偶然出现在正文里完全可能，那时"count==1"会莫名变红——假故障。
_foot = f"本单审批校验码：`{_tok}`"
ok("校验码只印在末页页脚（读到末页才抄得到）",
   _md.count(_foot) == 1 and _foot in split_pages(_md)[-1]
   and not any(_foot in p for p in split_pages(_md)[:-1]),
   f"命中页={[i for i, p in enumerate(split_pages(_md)) if _foot in p]}")

asked = []


def reader(p):                      # 线程式答题器
    asked.append(p)
    return answers.pop(0) if answers else ""


def st_apr():
    return st_of(open_tickets=[str(_run_dir / "approvals" / _rid
                                   / "request.md")])


# a) 中止阅读 ⇒ 不提交任何意向
answers, asked = ["", "", "q"], []
msg = U.approve_flow(st_apr(), reader)
ok("中途退出 ⇒ 未提交决定", "未提交" in msg
   and E.peek_decide()[1] == "missing", msg[:80])

# b) 校验码输错 ⇒ 不提交（这是"未读全不能批"在 UI 层的闸）
answers, asked = ["", "", "", "", "", "a", "小明", "批合批恢复", "0000"], []
msg = U.approve_flow(st_apr(), reader)
ok("校验码错误 ⇒ 拒绝提交", "校验码不匹配" in msg
   and E.peek_decide()[1] == "missing", msg[:80])

# c) 读完全文 + 正确码 + 审批 ⇒ 意向提交、守护消费、decision 生效
answers, asked = ["", "", "", "", "", "a", "小明",
                  "批第一条：合批恢复，预期 drawCalls 降", _tok], []
# 守护侧用同一套（不启线程）：直接调 _consume_decide 走真实落盘路径
_d = Daemon.__new__(Daemon)
_d.conf = {}
U.DECIDE_WAIT_S = 0.5               # 本用例不启守护线程，缩短等待避免拖慢
msg = U.approve_flow(st_apr(), reader)      # 意向已写，但没人落盘 ⇒ 报超时
ok("正确码 ⇒ 意向已提交（等守护落盘）", "意向已提交" in msg, msg[:80])
ok("意向内容含决定/批准人/批语/码",
   (E.peek_decide()[0] or {}).get("decision") == "approve"
   and (E.peek_decide()[0] or {}).get("approver") == "小明", str(E.peek_decide()[0]))
_d._consume_decide()
_dec = _store.load_decision(_rid)
ok("守护消费后 decision.json 生效", _dec is not None
   and _dec.decision == "approve" and _dec.decided_via == "ui",
   str(_dec.decided_via) if _dec else "None")
# 短路保护：决定已生效的工单再按 d，不该把人多带一遍五页再报错
answers, asked = [], []
msg = U.approve_flow(st_apr(), reader)
ok("重复审批 ⇒ 直接见已有决定，不再走阅读流程",
   "已有决定" in msg and not asked, f"{msg[:60]} asked={len(asked)}")

# d) 守护独立复算：token 错的意向即便绕过 UI 直塞也被拒（两道闸互不依赖）
asked = []
E.stage_decide(str(_run_dir / "approvals" / _rid / "request.md"), "reject",
               approver="小红", note="测试旁路", token="dead")
(_run_dir / "approvals" / _rid / "decision.json").unlink()  # 清掉 c) 的决定
_before = E.peek_decide()[0]
_d._consume_decide()
ok("token 不符的意向被守护拒绝（不落 decision.json）",
   _store.load_decision(_rid) is None, "绕过 UI 也批不动")
ok("被拒意向已认掉（不反复重试）",
   E.peek_decide()[1] in ("missing", "already_committed"), str(_before))

# e) approve 缺批准人/批语 ⇒ UI 先挡（与守护 ApprovalDecision 双闸同源）
answers, asked = ["", "", "", "", "", "a", "", ""], []
msg = U.approve_flow(st_apr(), reader)
ok("批准缺人名/批语 ⇒ UI 拒绝提交", "必须同时给出" in msg
   and E.peek_decide()[1] == "missing", msg[:80])

# f) defer 同样要读满 5 页 + 输码：读完才谈"批"，也才谈"驳/缓"
answers, asked = ["", "", "", "", "", "d", "小明", "证据够但方案要讨论", _tok], []
msg = U.approve_flow(st_apr(), reader)
ok("defer 也必须翻到第 5 页才问决定",
   len(asked) >= 6 and "第 5/5 页" in asked[4] and "决定：" in asked[5],
   f"asked={len(asked)}")
_d._consume_decide()
_dec2 = _store.load_decision(_rid)
ok("defer 意向经守护生效（驳回/暂缓同样吃码闸）",
   _dec2 is not None and _dec2.decision == "defer", str(_dec2))


# ───────── ⑨ 接入向导流程（注册 / 下限表编辑）─────────
# 矩阵测试只证明"这键此刻可点"，不证明"点下去做了什么"。接线的两个本地流程
# 必须用按键序列驱动（本仓纪律：写了字段没验证消费者等于没写）。
# 关键断言是 **sent 为空**：注册与改表都不该发守护命令——守护没有"切工程"
# 这种命令，发过去只会 command_ignored（界面看起来像功能坏了）。
print("== ⑨ 接入向导：注册与下限表编辑（本地流程，不发守护命令）==")
import subprocess as _sp  # noqa: E402
from upi.config import project_profile as PP  # noqa: E402

_proj = TMP / "game-proj"
(_proj / "Assets" / "Scenes").mkdir(parents=True, exist_ok=True)
(_proj / "Assets" / "Scenes" / "Menu.unity").write_text("%YAML 1.1\n", encoding="utf-8")
# 档案硬要求工程在 git 工作树内（快照/回滚只依赖 git），所以造一个真 git 根
_sp.run(["git", "init", "-q"], cwd=str(_proj), capture_output=True, timeout=60)

_sent_n0 = len(sent)
# ⚠️ 必须传 line_reader=spy_line：register_flow / floor_flow 全靠 line_read 收答案，
#   漏传就退回真实 stdin —— 测试里 stdin 是 EOF，流程一律走"已取消"分支，
#   于是"注册没成功"被伪装成"功能坏了"（第一版就是这么红的，报错还指向 use_profile）。
# 首项 "N" 是 step 的通用 token 门（不可逆动作必输），消费完才进 register_flow；
# 其后依次：profile_id / 工程路径 / scenes_dir / 模板 / 噪声基准 / 下限表 / y 确认
answers[:] = ["N", "ui-probe", str(_proj), "", "", "", "", "y"]
lines.clear()
msg9, _p9, _q9 = U.step(UA.IDLE, st_of(), "n", sender=spy_send, line_reader=spy_line)
ok("注册流程不发守护命令（本地落盘）", len(sent) == _sent_n0, str(sent[_sent_n0:]))
ok("注册成功提示里写明当前守护仍服务原工程（不假装已切换）",
   "仍服务原工程" in msg9, msg9[:120])
ok("档案落盘在隔离区（不写真实仓库 projects/）",
   PP.profile_file("ui-probe").exists()
   and str(PP.projects_dir()).startswith(str(TMP)), str(PP.projects_dir()))
ok("激活指针指向新工程（下次进程默认接管）",
   PP.get_active_profile_id() == "ui-probe", PP.get_active_profile_id())

# 取消路径：第一个答案（profile_id）留空 ⇒ 不落任何文件
_ghost = PP.profile_file("ghost-probe")
answers[:] = ["N", ""]
msg9b, _p, _q = U.step(UA.IDLE, st_of(), "n", sender=spy_send, line_reader=spy_line)
ok("注册可取消：id 留空 ⇒ 不写文件",
   "取消" in msg9b and not _ghost.exists(), msg9b[:90])

# 下限表：新工程还没表 ⇒ 第一条必须先给出处（§8.1 无出处=手调）
PP.use_profile("ui-probe")
try:
    _floor_fp = PP.active_profile().floor_targets_path
    answers[:] = ["F", "2", "", "drawCalls", "<=", "50", "", ""]
    msg9c, _p, _q = U.step(UA.IDLE, st_of(), "f", sender=spy_send, line_reader=spy_line)
    ok("无出处 ⇒ 下限表拒绝写入（纪律走通到界面）",
       "未改动" in msg9c and (_floor_fp is None or not _floor_fp.exists()),
       msg9c[:130])

    answers[:] = ["F", "2", "性能预算 v1", "drawCalls", "<=", "50", "calls", "", "y"]
    msg9d, _p, _q = U.step(UA.IDLE, st_of(), "f", sender=spy_send, line_reader=spy_line)
    ok("带出处 ⇒ 第一条下限写入工程侧文件",
       _floor_fp is not None and _floor_fp.exists() and "1 条目标线" in msg9d,
       msg9d[:130])
    if _floor_fp and _floor_fp.exists():
        import json as _j
        _tb = _j.loads(_floor_fp.read_text(encoding="utf-8"))
        ok("写入内容含出处与线值（可被下限腿读回）",
           _tb["source"] == "性能预算 v1"
           and _tb["targets"][0]["metric"] == "drawCalls"
           and _tb["targets"][0]["value"] == 50, _tb)
    ok("下限表编辑同样不发守护命令", len(sent) == _sent_n0, str(sent[_sent_n0:]))
finally:
    PP.reset_profile()

# ───────── ⑩ 键位文档与动作表差集核对 ─────────
# 阶段 6 立的判据形态：文档命令表与代码清单的差集必须为空，靠人记会漏。
# 接线状态变化时（本批 n/f 解禁、l 仍未接）文档若不同步，用户按到的就是
# 一个说明书里没有的键 —— 比功能缺失更难查，因为看起来"文档说它存在"。
print("== ⑩ 使用说明键位表与动作表对齐 ==")
import re as _re  # noqa: E402
_doc = (ROOT / "docs" / "使用说明.md").read_text(encoding="utf-8")
_doc_keys = set(_re.findall(r"^\| `(.)` \|", _doc, _re.M))
_code_keys = {a["key"] for a in UA._ALL_ACTIONS}
ok("代码有而文档无的键 ⇒ 补文档", not (_code_keys - _doc_keys),
   sorted(_code_keys - _doc_keys))
ok("文档有而代码无的键 ⇒ 文档写了不存在的键", not (_doc_keys - _code_keys),
   sorted(_doc_keys - _code_keys))
_unwired = {a["key"] for a in UA._ALL_ACTIONS if not a.get("wired", True)}
ok("未接线的键必须在文档里标\"尚未接线\"（不许默默列成可用）",
   all(f"`{k}`" in _doc and "尚未接线" in _doc for k in _unwired),
   f"未接线={sorted(_unwired)}")

print(f"\nUI 动作层测试：{len(PASS)} 通过 / {len(FAIL)} 失败")
for f in FAIL:
    print("  ✗", f)
import shutil
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
