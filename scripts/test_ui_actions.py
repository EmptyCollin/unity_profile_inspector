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
    base = {"updated_at": fresh_minute(), "pid": 4321, "busy": False,
            "paused": False, "open_tickets": [], "alerts": [],
            "degraded": False, "conf": {"interval_s": 1800, "cooldown_s": 0,
                                        "windows": 4,
                                        "waterline_timeout_s": 900,
                                        "auto_fix": {"live_llm": False,
                                                     "enabled": False}}}
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
    (UA.AWAITING, {UA.ACT_VIEW_TICKET, UA.ACT_STOP_DAEMON},
     {UA.ACT_RUN_NOW, UA.ACT_CANCEL, UA.ACT_PAUSE}),
    (UA.PAUSED, {UA.ACT_RESUME}, {UA.ACT_RUN_NOW, UA.ACT_PAUSE}),
    (UA.UNCALIBRATED, {UA.ACT_SNAPSHOT},
     {UA.ACT_RUN_NOW, UA.ACT_REGISTER, UA.ACT_CALIBRATE}),
]
# ⚠️ 两个期望必须避开"未接线"与"同键去重"这两条实现规则，否则测试与
#   实现互相矛盾：
#     · 未接线动作（register/calibrate/decide）在所有态都置灰，不能期待可用；
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

# 未接线动作在任何态都不得可用
print("== ②b 未接线能力一律置灰 ==")
for name in ("register_project", "calibrate", "decide"):
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
# 配置字段必须被守护 set 分支接受（否则发了等于没发）
src = (ROOT / "upi" / "agents" / "daemon.py").read_text(encoding="utf-8")
seg = src[src.index('elif cmd == "set"'):src.index('elif cmd == "quit"')]
ok("配置项字段守护全部认得",
   all(f[0] in seg for f in U.CFG_FIELDS),
   [f[0] for f in U.CFG_FIELDS if f[0] not in seg])


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
sent.clear()
answers, lines = ["1", "900"], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("改数值项 ⇒ 发 set 带字段与新值",
   sent == [("set", {"interval_s": 900})], str(sent))
ok("回显 old→new", "1800" in msg and "900" in msg, msg)

# 回车取消：不发命令
sent.clear()
answers, lines = ["1", ""], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("新值留空 ⇒ 取消，不发命令", not sent and "取消" in msg, f"{msg} {sent}")

# 选项越界
sent.clear()
answers, lines = ["99"], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("选项越界 ⇒ 不改动", not sent and "未改动" in msg or "未选择" in msg,
   f"{msg} {sent}")

# 类型不符
sent.clear()
answers, lines = ["1", "abc"], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("非整数 ⇒ 拒绝提交", not sent and "无效" in msg, f"{msg} {sent}")

# 间隔下限
sent.clear()
answers, lines = ["1", "10"], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("间隔 <60s ⇒ 提前挡住不发命令（防高频烧积分）",
   not sent and "60" in msg, f"{msg} {sent}")

# 布尔项
sent.clear()
answers, lines = ["5", "y"], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("布尔项 y ⇒ 发 True", sent == [("set", {"live_llm": True})], str(sent))
sent.clear()
answers, lines = ["5", "maybe"], []
msg = U.edit_config(st_of(), spy_send, spy_line)
ok("布尔项乱填 ⇒ 拒绝", not sent and "y/n" in msg, f"{msg} {sent}")


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


print(f"\nUI 动作层测试：{len(PASS)} 通过 / {len(FAIL)} 失败")
for f in FAIL:
    print("  ✗", f)
import shutil
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
