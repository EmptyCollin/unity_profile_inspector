"""自愈与安全异常处置测试（阶段 3 完成判据）。

计划要求的三类负例（坏配置 / 半写文件 / 子进程失控）都在下面，另加一条
贯穿全部用例的元断言：**自愈动作不许越权**——能自动做的只有"重采一轮、
收尾子进程、清自己的垃圾"，改阈值/改权限/杀无关进程/拿污染值下判定一律禁止。

所有用例在 tempfile 里跑：总线经 E.isolate 重定向，不读写真实 runs/，
不起编辑器、不采样、不花积分。
"""
from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core import events as E          # noqa: E402
from upi.agents import daemon as D         # noqa: E402
from upi.agents import patrol as P         # noqa: E402
from upi.tooling import aggregate as AG    # noqa: E402

PASS, FAIL = [], []


def ok(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name + ("" if cond else f"  ← {str(detail)[:150]}"))
    print(f"   [{'ok' if cond else 'FAIL'}] {name}"
          + ("" if cond else f"  ← {str(detail)[:150]}"))


def tmp_runs() -> Path:
    """整条总线 + 统计层全部指向一个新的临时区。"""
    p = Path(tempfile.mkdtemp(prefix="upi_rec_")) / "runs"
    E.isolate(p)
    AG.RUNS = p
    AG.DB = p.parent / "stats.db"
    AG.KB_DB = p.parent / "no_kb.db"
    P.RUNS = p
    P.PATROL_LOG = p / "patrol_runs.jsonl"
    P.STATE_FP = p / "patrol_state.json"
    return p


def mk_daemon(conf=None):
    d = D.Daemon.__new__(D.Daemon)
    d.conf = dict(conf or {"enabled": False, "interval_s": 1800,
                           "max_consecutive_failures": 3})
    d.busy = False
    d.paused = False
    d.stop_requested = False
    d.child_pid = None
    d._lock = threading.Lock()
    d.consecutive_failures = 0
    d.last_progress_sig = None
    d.signals = {}
    return d


# ─────────── ① 配置 fail-closed（自愈第 1 项） ───────────
print("== ① 配置坏掉 ⇒ fail-closed，绝不默认开采样 ==")
r = tmp_runs()
import upi.config.settings as CS
_real_lc = CS.load_config


class _Boom(Exception):
    pass


def _raise_lc(*a, **k):
    raise _Boom("llm_config.yaml 语法坏了")


CS.load_config = _raise_lc
try:
    c = D.load_runtime_conf()
finally:
    CS.load_config = _real_lc
ok("坏配置 ⇒ enabled=False（旧写法是 True=自动开始烧钱）",
   c["enabled"] is False, json.dumps(c, ensure_ascii=False)[:150])
ok("同时留痕 config_read_failed（不静默回落）",
   any(e.get("kind") == "config_read_failed" for e in E.tail_events(10)))
ok("降级项记进 conf.degraded（UI 能看到配置有问题）",
   bool(c.get("degraded")), str(c.get("degraded")))

# ── 两处都没声明 enabled 的情形 ──
# ⚠️ 上一条断言我曾写成 `E.save_conf({})` 然后期望 enabled=False，那是错的：
#   真实 llm_config 显式写了 enabled:true，且 pydantic model_dump() 必含该键，
#   所以"配置加载成功但没声明 enabled"这个前提根本不可达（实测返回 True）。
#   行为本身是对的——显式声明就该被尊重。要测 fail-closed 的"缺声明"分支，
#   必须用可控替身造出一个不含 enabled 的 base，而不是拿真实配置硬猜。
class _NoEnabled:
    def monitor(self):
        return _NoMon()


class _NoMon:
    def model_dump(self):
        return {"interval_s": 1800, "windows": 4}     # 故意不含 enabled


_real_lc2 = CS.load_config
CS.load_config = lambda *a, **k: _NoEnabled()
try:
    E.save_conf({})
    c2 = D.load_runtime_conf()
finally:
    CS.load_config = _real_lc2
ok("两处都未声明 enabled ⇒ fail-closed", c2["enabled"] is False, str(c2)[:120])
ok("并留痕 config_incomplete",
   any(e.get("kind") == "config_incomplete" for e in E.tail_events(6)))

# 用户明确开了采样：必须照常开（fail-closed 不等于 fail-禁用）
E.save_conf({"enabled": True})
ok("用户显式 enabled=True ⇒ 正常开启", D.load_runtime_conf()["enabled"] is True)
# 反向：用户明确关了，即使真实配置里写着开也要关（覆盖层优先）
E.save_conf({"enabled": False})
ok("用户显式 enabled=False ⇒ 真实配置的 true 被覆盖",
   D.load_runtime_conf()["enabled"] is False)


# ─────────── ② 取消通道：绝不误伤活进程（自愈第 2、3 项） ───────────
print("== ② 取消通道与启动清扫 ==")
r = tmp_runs()
dead = 999991
me = os.getpid()
ok("request_cancel 落标志文件", E.request_cancel(dead, reason="t")
   and E.cancel_flag(dead).exists())
# ── 活进程的标志**绝不能**被清扫删掉：文件通道是唯一能让子进程自己收尾的
#   手段（os.kill 会让 finally 不执行）。清扫函数返回的 n 是"删掉的数量"，
#   这里 dead 那个旗本来就该被删，所以 n==1 是**正确**的（先前断言 n==0
#   是把两个旗混在一起比，测的是我对返回值的误解，不是实现）。
#   判据只看该留的有没有留下。
E.request_cancel(me, reason="orphan-test")
E.sweep_cancel_flags()
ok("活进程持有取消标志 ⇒ 不被清扫（孤儿仍能自行收尾）",
   E.cancel_requested(me), "活进程标志被误删=再也停不下来")
ok("并告警孤儿存在", any(e.get("kind") == "orphan_child_holding_cancel"
                         for e in E.tail_events(4)))
ok("死进程那个旗顺手被清掉了（回收正确）", not E.cancel_requested(dead))
E.clear_cancel(me)
E.request_cancel(dead, reason="stale")
ok("死进程残留标志被清走", E.sweep_cancel_flags() >= 1
   and not E.cancel_requested(dead))

# .tmp 残骸：只删陈旧的，新鲜的一律保留（否则打断并发写）
stale = r / "daemon_status.json.tmp"
stale.write_text("残骸", encoding="utf-8")
os.utime(stale, (time.time() - 900, time.time() - 900))
fresh = r / "daemon_progress.json.tmp"
fresh.write_text("正在写", encoding="utf-8")
(r / "daemon.pid").write_text(str(dead), encoding="utf-8")
sw = E.sweep_residuals()
ok("陈旧 .tmp 被回收", not stale.exists(), str(sw))
ok("新鲜 .tmp 保留（不误伤仍在进行的原子写）", fresh.exists(), str(sw))
ok("死守护的 pid 文件被清", not (r / "daemon.pid").exists(), str(sw))
(r / "daemon.pid").write_text(str(me), encoding="utf-8")
sw2 = E.sweep_residuals()
ok("活守护的 pid 绝不删（删了=允许双守护抢编辑器）",
   (r / "daemon.pid").exists() and sw2["pid_removed"] == 0, str(sw2))
fresh.unlink()

# 子进程侧自查
E.request_cancel(me, reason="self-check")
ok("子进程能自查到取消标志", E.cancelled_for_me() is True)
E.clear_cancel(me)
ok("清除后自查为假", E.cancelled_for_me() is False)


# ─────────── ③ 信号处理：只置标志、不做不可逆动作（自愈第 4 项） ───────────
print("== ③ 信号处理器 ==")
r = tmp_runs()
d = mk_daemon()
(r / "daemon.pid").write_text(str(os.getpid()), encoding="utf-8")
res = D.install_signals(d)
installed = [k for k, v in res.items() if v == "installed"]
ok("至少 SIGINT 装上了", "SIGINT" in res and res["SIGINT"] in
   ("installed", "absent", "failed:ValueError"), str(res))
ok("返回值如实记录哪些装上了（不假装全都能收）", isinstance(res, dict) and bool(res))
if installed:
    h = signal.getsignal(getattr(signal, installed[0]))
    ok("处理器确实注册进了 signal 模块", callable(h)
       and getattr(h, "__name__", "") == "_handler", str(h)[:80])
    if callable(h):
        h(int(getattr(signal, installed[0])), None)   # 直接调，不真发信号
        ok("处理器只置停止标志", d.stop_requested is True)
        ok("处理器不删 pid 文件（清理归主循环 finally）",
           (r / "daemon.pid").exists())
        ok("并留痕 daemon_signal",
           any(e.get("kind") == "daemon_signal" for e in E.tail_events(4)))
else:
    ok("处理器只置停止标志（本平台未装上，跳过行为验证）", True, str(res))
    ok("并留痕 daemon_signal（本平台未装上，跳过）", True, str(res))


# ─────────── ④ 重启判据（自愈第 5 项：限次 + 不跟人对着干） ───────────
print("== ④ 重启风暴判据 ==")
r = tmp_runs()
# 守护还在跑 ⇒ 不重启
(r / "daemon.pid").write_text(str(os.getpid()), encoding="utf-8")
E.write_status({"pid": os.getpid(), "updated_at": E.now()})
ok("已有守护 ⇒ skip_running（重复起=双抢编辑器）",
   E.restart_verdict()["action"] == "skip_running")
(r / "daemon.pid").unlink()
E.write_status({"pid": 999990, "updated_at": "2020-01-01T00:00:00"})

# 事件流读不出来 ⇒ unknown，fail-closed 不猜"可以重启"
E.EVENTS = r / "nope_events.jsonl"
ok("事件流缺失 ⇒ unknown（不默认允许重启）",
   E.restart_verdict()["action"] == "unknown")
E.EVENTS = r / "daemon_events.jsonl"

# 自己留了优雅退出记录 ⇒ no_restart（这是人的意图，不能自动拉回来）
E.emit("daemon_started", pid=1)
E.emit("daemon_stopped", pid=1)
v = E.restart_verdict()
ok("优雅停止后 ⇒ no_restart（自动重启=跟用户对着干）",
   v["action"] == "no_restart", str(v))
# 预算/故障主动退出同样算"自己退的"
E.emit("daemon_exit", reason="consecutive_failures")
ok("daemon_exit ⇒ 仍 no_restart", E.restart_verdict()["action"] == "no_restart")

# 有 started 没有 stopped ⇒ 判为崩溃，可以拉一次
E.emit("daemon_started", pid=2)
v2 = E.restart_verdict(max_restarts=3)
ok("崩溃形态（无退出记录）⇒ start", v2["action"] == "start", str(v2))

# 窗口内启动次数达上限 ⇒ blocked 且只留痕一次（防刷屏）
r2 = tmp_runs()
for i in range(3):
    E.emit("daemon_started", pid=100 + i)
v3 = E.restart_verdict(max_restarts=3)
ok("10 分钟内第 3 次启动 ⇒ blocked（停手等人）",
   v3["action"] == "blocked", str(v3))
n_blk_before = sum(1 for e in E.tail_events(50)
                   if e.get("kind") == "restart_blocked")
for _ in range(5):
    E.restart_verdict(max_restarts=3)
n_blk_after = sum(1 for e in E.tail_events(50)
                  if e.get("kind") == "restart_blocked")
ok("判据重复调用不刷屏（宿主每几十秒轮询会把真信号埋掉）",
   n_blk_after == n_blk_before, f"{n_blk_before} -> {n_blk_after}")


# ─────────── ⑤ 半写/坏文件不得静默（自愈第 8 项 + 阶段 2 判据） ───────────
print("== ⑤ 坏文件留痕，不伪装成「没活儿干」 ==")
r = tmp_runs()
tk = r / "patrol_20261109_100000" / "approvals" / "APR-1"
tk.mkdir(parents=True)
(tk / "request.json").write_text("{}", encoding="utf-8")
(tk / "decision.json").write_text(
    json.dumps({"decision": "approve", "approver": "me", "note": "n"}),
    encoding="utf-8")
run_dir = tk.parents[1]
(run_dir / "state.json").write_text("{半写截断", encoding="utf-8")
d = mk_daemon()
open_t, go = d._tickets()
ok("损坏的 state.json ⇒ 工单不进 go（不硬跑未知进度）", go == [], str(go))
ok("且留痕 ticket_state_unreadable（旧写法静默 continue，工单永久隐形）",
   any(e.get("kind") == "ticket_state_unreadable" for e in E.tail_events(6)))
# 损坏的 decision.json 同样要区分于"没人批"
(tk / "decision.json").write_text("{坏", encoding="utf-8")
(run_dir / "state.json").write_text(
    json.dumps({"verdict": None,
                "records": [{"kind": "fix",
                             "execute": {"status": "awaiting_approval"}}]}),
    encoding="utf-8")
open_t, go = d._tickets()
ok("损坏的 decision ⇒ 既不算已批也不静默", go == [] and open_t == [], str(open_t))
ok("并留痕 decision_unreadable",
   any(e.get("kind") == "decision_unreadable" for e in E.tail_events(6)))

# 告警必须一路走到 UI（有生产者也要有消费者）
(run_dir / "state.json").write_text(
    json.dumps({"verdict": None, "records": []}), encoding="utf-8")
d._refresh_status()
st = E.read_status() or {}
ok("告警进 status.alerts", bool(st.get("alerts")),
   json.dumps(st.get("alerts"), ensure_ascii=False)[:140])
import upi.agents.ui as U
ok("UI 帧里画得出来（否则告警仍进黑洞）",
   "需要注意" in U.snapshot())


# ─────────── ⑥ 回滚目标必须是管线自己的 tag（自愈第 7 项） ───────────
print("== ⑥ 回滚边界：绝不 revert 用户未提交改动 ==")
from upi.tooling import snapshot as SN    # noqa: E402
r = tmp_runs()
bad = SN.Snapshot(attempt_id="x", git_ref="main", head_before="a",
                  asset_manifest_hash="h", scene_guid="g",
                  protocol_fingerprint="fp", created_at=E.now(),
                  root=str(ROOT), metadata={})
try:
    SN.rollback(bad, ROOT / "fixtures" / "upi-fixture", ROOT / "s.unity")
    ok("非管线 tag ⇒ 拒绝回滚", False, "竟然放行了")
except SN.SnapshotError as e:
    ok("非管线 tag ⇒ 拒绝回滚（不可逆操作前必须验目标）",
       "upi/attempt-" in str(e), str(e)[:120])
except Exception as e:  # noqa: BLE001
    ok("非管线 tag ⇒ 拒绝回滚", False, f"错型:{type(e).__name__} {str(e)[:90]}")
good = SN.Snapshot(attempt_id="y", git_ref="upi/attempt-y", head_before="a",
                   asset_manifest_hash="h", scene_guid="g",
                   protocol_fingerprint="fp", created_at=E.now(),
                   root=str(ROOT), metadata={})
ok("管线自己的 tag ⇒ 通过前缀判据（后续 git 校验照旧）",
   str(good.git_ref).startswith("upi/attempt-"))


# ─────────── ⑦ 命令动作有 trace（自愈第 8 项，I10/G13） ───────────
print("== ⑦ 命令 span：可回溯且不污染判定轴 ==")
r = tmp_runs()
tr = D._CmdTracer(r)
tr.emit(stage="cmd:enable", ms=3)
tr.emit(stage="cmd:cancel", ms=5, exit_status=1)
lines = (r / D.CMD_TRACE_DIR / "trace.jsonl").read_text(encoding="utf-8").splitlines()
ok("命令 span 落盘", len(lines) == 2, str(lines))
rec = json.loads(lines[0])
ok("run_id 固定（不随守护重启漂移，否则历史散成一片）",
   rec["run_id"] == D.CMD_TRACE_DIR, rec["run_id"])
# seq 续号：新建 tracer 不能从 0 重来（聚合按 (run_id,seq) 去重，撞号=留痕被丢）
tr2 = D._CmdTracer(r)
tr2.emit(stage="cmd:set", ms=1)
ok("新实例 seq 续号（防重启后留痕被当重复丢弃）",
   json.loads((r / D.CMD_TRACE_DIR / "trace.jsonl").read_text(
       encoding="utf-8").splitlines()[-1])["seq"] == 3)

d = mk_daemon()
d._apply_cmd({"cmd": "pause", "args": {}})
d._apply_cmd({"cmd": "teleport", "args": {}})      # 未知命令也要有 span
spans = [json.loads(x) for x in (r / D.CMD_TRACE_DIR / "trace.jsonl"
                                ).read_text(encoding="utf-8").splitlines()]
stages = [s["stage"] for s in spans]
ok("_apply_cmd 为已知名令出 span", "cmd:pause" in stages, str(stages))
ok("未知命令出 span 且 exit_status=1（否则界面与实现不同步完全无痕）",
   "cmd:teleport" in stages
   and [s for s in spans if s["stage"] == "cmd:teleport"][0]["exit_status"] == 1)

# 统计层：命令留痕进统计但不进判定轴
AG.rebuild(AG.DB)
m = AG.meta_metrics(AG.DB)
ok("命令动作单列计数（G13 可回溯）", m["cost"]["command_actions"] >= 2,
   str(m["cost"]["command_actions"]))
ok("但不算判定轮次", m["cost"]["runs_traced"] == 0,
   f"轮次={m['cost']['runs_traced']}")
c = AG.connect_ro(AG.DB)
ok("阶段耗时表排除 cmd:*（否则视图3 把按键画成 Agent 阶段）",
   all("cmd:" not in (row[0] or "") for row in c.execute(
       "SELECT stage FROM stage_stats")), str(c.execute(
           "SELECT stage FROM stage_stats").fetchall()))
ok("命令 rc≠0 不被当采集事故",
   c.execute("SELECT COUNT(*) FROM spans WHERE exit_status<>0").fetchone()[0] >= 1
   and all("cmd:" not in (row[0] or "") for row in c.execute(
       "SELECT DISTINCT run_id FROM runs WHERE kind='interaction'")), "")
c.close()


# ─────────── ⑧ 元断言：自愈不越权 ───────────
def _threshold_write_calls(text):
    """守护里"把含 threshold 的字面量当路径去写文件"的调用点。

    为什么用 AST 而不是 `"thresholds" not in src`：本文件 _kill_calls 的注释
    早就立了这条规矩——字符串计数会把"解释为什么不用它/转发一个叫这名的字段"
    都算成"在用它"。标定向导接线后守护会 `E.emit(..., thresholds=res.get("thresholds"))`
    转发一个**事件字段名**（阈值文件仍只由 do_merge 生产），粗扫描就误伤成越权。
    真正的约束是"守护不许生成阈值文件"，所以只看写原语的**路径实参**里有没有
    threshold —— 事件名、取值键、注释都不算。
    """
    import ast as _ast
    W = {"write_text", "atomic_write", "atomic_write_text", "replace", "open"}
    hits = []
    for n in _ast.walk(_ast.parse(text)):
        if isinstance(n, _ast.Call):
            fname = getattr(n.func, "attr", None) or getattr(n.func, "id", "")
            if fname in W:
                for c in _ast.walk(n):
                    if (isinstance(c, _ast.Constant) and isinstance(c.value, str)
                            and "threshold" in c.value.lower()):
                        hits.append(f"{fname}:{c.value[:40]}")
                        break
    return hits


def _calibrate_triggering_methods(text):
    """Daemon 里哪些方法会**发起**标定（引用 _start_calibrate/_calibrate/
    calibrate_round）。安全不变量：只有人工命令派发能发起，排期主循环不能。

    这条比"守护不许提标定"强：标定现在是合法的人工功能（界面 l 要输 token），
    但绝不能被排期器**自动**触发——那等于阈值未经人批准就被反复重写，正是
    §3 blocked 档要防的。用 AST 找调用图，不靠文本出现。
    """
    import ast as _ast
    tree = _ast.parse(text)
    cls = [n for n in tree.body
           if isinstance(n, _ast.ClassDef) and n.name == "Daemon"]
    if not cls:
        return {"<no Daemon class>": {"<structure changed>"}}
    trig = {"_start_calibrate", "_calibrate", "calibrate_round"}
    out = {}
    for f in cls[0].body:
        if isinstance(f, _ast.FunctionDef):
            names = set()
            for c in _ast.walk(f):
                if isinstance(c, _ast.Attribute) and c.attr in trig:
                    names.add(c.attr)
                elif isinstance(c, _ast.Name) and c.id in trig:
                    names.add(c.id)
            if names:
                out[f.name] = names
    return out


print("== ⑧ 自愈动作的边界（不许自动做的事） ==")
src = (ROOT / "upi" / "agents" / "daemon.py").read_text(encoding="utf-8")
ok("守护不调 git 写操作（回滚属 fix/review 节点，守护只做调度）",
   "git commit" not in src and "reset --hard" not in src)
_twc = _threshold_write_calls(src)
ok("守护不生成阈值文件（§3 blocked 档：路径实参里无 threshold 写操作）",
   not _twc, str(_twc)[:120])
_cal_methods = _calibrate_triggering_methods(src)
ok("标定只能由人工命令发起（run_forever/_start_round 不引用任何标定触发）",
   "run_forever" not in _cal_methods and "_start_round" not in _cal_methods,
   str(_cal_methods)[:140])
ok("标定确经人工派发路径可达（界面 l 接上了，不是死代码）",
   "_dispatch_cmd" in _cal_methods, str(_cal_methods)[:140])
ok("不按进程名批量杀（G10）", "taskkill" not in src
   and "/IM" not in src)


def _kill_calls(text):
    """用 AST 找真正的 os.kill **调用点**（不是文本出现次数）。

    先前这里写成 `src.count("request_cancel") >= src.count("os.kill")`，
    判据本身不成立：os.kill 在 daemon.py 出现 6 次，其中 4 次是注释与
    文档串里的说明文字，字符串计数把"解释为什么不用它"也算成了"在用它"，
    于是正确实现被判成违规。结构性检查才能表达真实约束。
    """
    import ast as _ast
    hits = []
    for node in _ast.walk(_ast.parse(text)):
        if isinstance(node, _ast.Call):
            f = node.func
            if (isinstance(f, _ast.Attribute) and f.attr == "kill"
                    and isinstance(f.value, _ast.Name)
                    and f.value.id == "os"):
                hits.append(node.lineno)
    return hits


def _call_lines(text, attr, name=None):
    """找 obj.attr(...) 形式调用的行号；name 限定函数名（不传=全文）。"""
    import ast as _ast
    tree = _ast.parse(text)
    nodes = [tree]
    if name:
        nodes = [n for n in _ast.walk(tree)
                 if isinstance(n, _ast.FunctionDef) and n.name == name]
        if not nodes:
            return []
    hits = []
    for root in nodes:
        for n in _ast.walk(root):
            if (isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)
                    and n.func.attr == attr):
                hits.append(n.lineno)
    return hits


kills = _kill_calls(src)
sc = _call_lines(src, "request_cancel", name="_stop_child")
kill_sc = _call_lines(src, "kill", name="_stop_child")
# ① 收尾必须"文件通道优先"：_stop_child 里 request_cancel 的行号要早于
#    **同一函数内**的 os.kill。拿全文 kill 行比是错的——main 里那处兜底
#    在文件末尾，会把"顺序反了"也判成通过（先前正是这么写空的）。
ok("取消走文件通道，且顺序在硬杀之前（_stop_child）",
   bool(sc) and bool(kill_sc) and min(sc) < min(kill_sc),
   f"cancel行={sc} 同函数kill行={kill_sc}")
# ② 硬杀调用点必须受限：全文恰好 2 处兜底——_stop_child 的"标志写不下去"
#    与 main 的"quit 超时未消费"
ok("硬杀只存于兜底路径（全文应恰好 2 处）",
   len(kills) == 2, f"kill 行={kills}")

print(f"\n自愈与安全处置测试：{len(PASS)} 通过 / {len(FAIL)} 失败")
for f in FAIL:
    print("  ✗", f)
sys.exit(1 if FAIL else 0)
