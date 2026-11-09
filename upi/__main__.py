"""`python -m upi <command>` —— 生命周期入口（日常操作请用 `upi ui`）。

  ui            控制台界面：**唯一的日常交互入口**（进度、开关采样、暂停、
                立即巡检、取消、改配置、批工单全部在这里按键完成）
  status        环境自检：CLI / Editor / 标定链 / 探针 / 密钥 / 巡检配置 / 守护存活
  start         后台启动常驻守护（不占终端；之后用 upi ui 附着观察与控制）
  stop          停止后台守护（走命令通道优雅收尾，不是硬杀）

开发/诊断通道（`python -m upi dev <command>`，日常操作不需要，文档不再宣传）：
  monitor       前台跑常驻守护（start 内部就是这样拉起守护的）
  detect        水位检测一轮（等价 scripts/m1_detect.py）
  patrol-once   巡检单轮（外部调度器拉起：Windows 任务计划 / CI）
  resume        工单批准后续跑（--run <run_id>；正式审批请走 UI 按 d）
  drill         演练两阶段（透传 scripts/m4_drill.py 参数）
  report        生成历史统计报告 HTML
  stats         终端快览统计指标（JSON，脚本/CI 取数用）

"彻底唯一入口"的诚实边界（阶段 6）：任何进程都得有人第一次把它启动，可执行
入口消灭不掉；真正落地的形态是**用户永远只在 UI 里操作，不需要知道命令行的
存在**。dev 通道保留给开发与诊断，它产生的决定在留痕里如实标注来源
（decided_via=external），与界面审批可区分、可分层统计。

设计约定（与 AGENTS 全局不变量对齐）：
- 本入口不产生任何数值（I1），只做组装与调度；
- 密钥只查环境变量存在性、只输出"有/无"（I13）；
- Editor 命令可达性运行时探测（I8），探测不到如实报告、不降级猜测。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def cmd_status(_args=None) -> int:
    print("== 管线环境自检 ==")
    ok = True

    u = shutil.which("unity")
    print(f"  Unity CLI      : {'可用（' + u + '）' if u else '缺失——先装 CLI'}")
    ok = ok and bool(u)

    # ⚠️ 档案只解析一次。上一版在 except 分支里又调了一次 active_profile()
    #   ——档案不可用时那次调用同样抛错，自检命令自己崩掉（本该"报缺失"变成
    #   "报不出来"），并且 proj=None 时 .is_dir() 还会 AttributeError。
    #   自检是排查入口，它自己不许有任何一种"读不出来"的形态。
    from upi.config.project_profile import active_profile, ProfileError
    try:
        _pf = active_profile()
        _prof_name, proj = _pf.profile_id, _pf.abs_project
        print(f"  工程档案        : {_prof_name}")
        print(f"  夹具工程        : {'存在' if proj.is_dir() else '缺失（档案指向的目录不在）'}")
        ok = ok and proj.is_dir()
    except ProfileError as e:          # 档案坏了：如实报，绝不拿内置路径凑"存在"
        print(f"  工程档案        : 不可用 —— {str(e)[:160]}")
        ok = False

    inst = None
    try:
        from upi.tooling.unity_cli import run_unity
        r = run_unity(["status", "--format", "json"], timeout_s=30)
        data = r.data or {}
        insts = data.get("instances") or (data.get("result") or {}).get("instances") or []
        inst = insts[0].get("state") if insts else None
    except Exception as e:  # noqa: BLE001 — I8：连不上如实报告，区分于"没有实例"
        print(f"  Editor 可达    : 探测失败 {type(e).__name__}（G9：如怀疑 Safe Mode 跑 unity pipeline list）")
        inst = "probe-error"
    print(f"  Editor 状态     : {inst or '无实例'}")
    # Editor 不可达不算致命：detect 通路可等编辑器就绪，monitor 会如实标盲区

    cal = sorted((ROOT / "baselines").glob("thresholds_*.json"),
                 key=lambda p: p.stat().st_mtime) if (ROOT / "baselines").is_dir() else []
    print(f"  阈值标定        : {cal[-1].name if cal else '缺失（§8.1 禁止未标定检测）'}")
    ok = ok and bool(cal)

    bundle = (ROOT / "calibration" / "multiwin_full_summary.json")
    print(f"  标定数据束      : {'存在' if bundle.exists() else '缺失'}")
    ok = ok and bundle.exists()

    hb = ROOT / "runs" / "_probe" / "pb_hb.txt"
    age = time.time() - hb.stat().st_mtime if hb.exists() else None
    print("  越界探针心跳    : " + ("无文件" if age is None else  # noqa: UP032
          f"{age:.0f}s（非播放态停更是实测正常行为，不代表探针死亡）"))

    try:
        from upi.config.settings import load_config
        cfg = load_config(ROOT / "llm_config.yaml")
        mon = cfg.monitor()
        print(f"  巡检配置        : enabled={mon.enabled} 间隔={mon.interval_s}s "
              f"auto_fix={mon.auto_fix.get('fixtures') or []} "
              f"live_llm={mon.auto_fix.get('live_llm')}")
        for name, p in cfg.providers.items():
            present, var = p.key_presence()
            print(f"  模型 Key({name}) : {'有' if present else '无'}（变量名 {var}，值不回显 I13）")
    except Exception as e:  # noqa: BLE001
        print(f"  配置加载        : 失败 {type(e).__name__}: {str(e)[:150]}")
        ok = False

    # 守护是否在跑 + 有无待批工单：这两项是"能不能交给它自己跑"的关键，
    # 只报配置不报存活等于没体检（实测中 UI/CLI 都要靠这两个数判断）
    try:
        from upi.core import events as E
        running, dst = E.daemon_running()
        if running:
            print(f"  守护进程        : 运行中 pid={dst.get('pid')} "
                  f"采样={'开' if dst.get('sampling_enabled') else '关'} "
                  f"阶段={dst.get('phase')} 下次轮次={dst.get('next_round_at')}")
        elif dst:
            print(f"  守护进程        : 已停止（最后心跳 {dst.get('updated_at')}）")
        else:
            print("  守护进程        : 从未启动（python -m upi start 后台起）")
        # 待批队列**自己扫盘**，不读上面 dst 快照的 open_tickets：快照只在
        # 守护活着时刷新，守护停着的时候恰好最需要知道"有没有单子在等"
        # （文档曾把这记成已知缺口）。判据取自 daemon._is_open，与守护
        # 调度侧同源 ⇒ 不会出现"自检说有单、守护说没单"。
        from upi.agents.daemon import scan_open_tickets
        ot = scan_open_tickets()
        if ot:
            print(f"  待批工单        : {len(ot)} 张"
                  f"{'（守护在等，未批不采基线）' if running else '（守护未运行，批了也要等它起来才续跑）'}")
            for p in ot[:2]:
                print(f"                    {p}")
        else:
            print("  待批工单        : 无")
    except Exception as e:  # noqa: BLE001
        print(f"  守护进程        : 探测失败 {type(e).__name__}: {str(e)[:120]}")

    print(f"自检结论: {'就绪' if ok else '有缺失项（↑）'}")
    return 0 if ok else 1


def cmd_detect(args) -> int:
    import subprocess
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "m1_detect.py"),
                        *args], timeout=400)
    return p.returncode


def cmd_patrol_once(_args=None) -> int:
    from upi.agents.patrol import patrol_once
    from upi.config.settings import load_config
    try:
        cfg = load_config(ROOT / "llm_config.yaml")
    except Exception as e:  # noqa: BLE001
        print("配置无效:", str(e)[:200])
        return 1
    mon = cfg.monitor()
    if not mon.enabled:
        print("pipeline.monitor.enabled=false——巡检未启用（缺配置段=不启用，不猜默认）")
        return 2
    rec = patrol_once(mon.model_dump())
    print(json.dumps(rec, ensure_ascii=False, indent=2, default=str)[:2000])
    return 0


def cmd_report(_args=None) -> int:
    """生成历史统计报告（单文件 HTML，DESIGN §7.3 A 形态 / M7）。

    先重灌 SQLite 再渲染：聚合库是派生缓存，跳过这步会让人对着
    旧缓存里的数字读新留档，报告与真相脱节。"""
    from upi.tooling import aggregate as AG
    from upi.tooling.report import build
    st = AG.rebuild()
    print(f"聚合：纳入 {st['runs_total']} 轮次"
          f"（新建 {st['created']}、更新 {st['updated']}、"
          f"未追踪 {st['untraced']}）")
    p = build()
    print(f"报告已生成：{p}")
    return 0


def cmd_stats(_args=None) -> int:
    """终端快览统计指标（不出 HTML，脚本/CI 取数用）。"""
    from upi.tooling import aggregate as AG
    m = AG.meta_metrics()
    print(json.dumps(m, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_monitor(_args=None) -> int:
    """前台常驻守护（占住当前终端，Ctrl+C 停）。想后台跑用 upi start。"""
    from upi.agents.daemon import main as daemon_main
    return daemon_main([])


def cmd_start(_args=None) -> int:
    """后台分离启动守护：不占终端，之后用 upi ui 附着观察与控制。

    实现收在 daemon.spawn_detached()——以前这里与 ui.start_daemon() 各写
    一份完整逻辑（DETACHED 标志、12 次心跳轮询、日志重定向），拉起命令的
    字面量因此写了两遍。阶段 6 把 monitor 移入 `upi dev` 时，只改一处就会让
    另一处报"启动失败"，看起来像守护坏了，其实是分层没同步。防呆（已在跑
    就拒绝、必须看到心跳才算成功）都在那个函数里，两处行为必然一致。
    """
    from upi.agents.daemon import spawn_detached
    ok, msg = spawn_detached()
    print(msg)
    if ok:
        print("界面：python -m upi ui ｜ 停止：python -m upi stop")
    return 0 if ok else 1


def cmd_stop(_args=None) -> int:
    from upi.agents.daemon import main as daemon_main
    return daemon_main(["stop"])


def cmd_ui(_args=None) -> int:
    """控制台界面：唯一日常入口。键位随状态动态给出（菜单里禁用项也会显示并附原因）。

    s 开关采样 / p 暂停继续 / r 立即巡检 / c 取消本轮 / k 改运行配置 /
    a 看工单 / d 审批工单 / b 界面内起守护 / x 刷新 / q 只退界面 / Q 停守护。
    非终端环境自动退化成一次性快照打印。
    """
    from upi.agents.ui import run_loop
    return run_loop()


def cmd_resume(args) -> int:
    """工单批准后续跑（开发通道）：python -m upi dev resume --run <run_id>

    巡检进程停在 awaiting_approval 的轮次，从 fix 断点续跑（不重烧 detect
    采样与 diagnose 归因积分）。

    ⚠️ 这是开发/诊断通道，不是正式审批路径：正式审批在界面按 d（逐页读完
      五件套 + 手输页脚校验码）。本命令只读已有的 decision.json，若那份决定
      是终端手放的（decided_via=external），会打印警告——它绕过了读毕校验，
      统计里必须能与界面审批区分开。
    """
    import argparse as _ap
    ap = _ap.ArgumentParser(prog="upi resume")
    ap.add_argument("--run", required=True, help="处于待批态的 run_id")
    ns = ap.parse_args(args)
    rd = ROOT / "runs" / ns.run
    if not (rd / "state.json").exists():
        print("!! 无 state.json，不能续跑:", rd)
        return 1
    from upi.agents.nodes import make_nodes
    from upi.agents.orchestrator import Runner
    from upi.core.approval import ApprovalStore
    st = json.loads((rd / "state.json").read_text(encoding="utf-8"))
    ar = st.get("approval_request")
    if not ar:
        # 兜底找回（实测停轮 patrol_20261108_232305 实况）：approval_request
        # 声明进 RunState 之前的停轮，键被 LangGraph 丢弃，但 request_id 还在
        # records 的 fix.execute 里——工单文件本身在磁盘上，从那里认。
        for rec in reversed(st.get("records", [])):
            rid = ((rec.get("execute") or {}).get("request_id"))
            if rid and (rd / "approvals" / rid / "request.json").exists():
                ar = {"md": str(rd / "approvals" / rid / "request.md")}
                break
    if not ar:
        print("!! 该 run 无待批工单记录（approval_request 缺失），不是可续跑的停轮")
        return 1
    store = ApprovalStore(rd)
    rid = Path(ar["md"]).parent.name
    dec = store.load_decision(rid)
    if dec is None:
        print("!! 未找到有效决定:", store._dir(rid) / "decision.json")
        print("   通道规则：无 decision=未批；approve 缺批准人或批的内容=视同未批。")
        return 1
    if dec.decision != "approve":
        print(f"   决定为 {dec.decision}（{dec.approver}）——按拒绝路径收尾，不续跑。")
        return 0
    print(f"   批准有效：{dec.approver}：{dec.note[:80]}")
    if getattr(dec, "decided_via", "external") != "ui":
        # 阶段 6 的诚实边界：功能入口已收进 UI，本命令只留作开发/诊断通道。
        # 手写 decision.json 绕过了"逐页读完 + 校验码"，留痕里必须能看出来。
        print("   ⚠ 该决定来自外部手写（approver_source=external），"
              "未经界面读毕校验；正式审批请用 upi ui 按 [d]。")
    runner = Runner(ns.run, st["fixture"])   # 同 run_id：trace/state 续写续读
    nodes = make_nodes(runner.dir, live_llm=False, approval_note="")
    runner.resume(nodes, entry="fix")        # fix 节点重读 decision.json，幂等
    st2 = json.loads((runner.dir / "state.json").read_text(encoding="utf-8"))
    v = st2.get("verdict") or {}
    print("   route:", st2.get("route"), "| attempts:", st2.get("attempts"))
    if v:
        t = v.get("target", {})
        print(f"   目标 {t.get('metric')}: {t.get('before')} -> {t.get('after')}"
              f" Δ{t.get('delta')} significant={t.get('significant')}")
        print("   判定:", v.get("status"))
        reg = [g["metric"] for g in v.get("guards", []) if g.get("regressed")]
        print("   守护劣化:", reg or "无")
    else:
        print("   未走到 verdict。error:", str(st2.get("error"))[:200])
    print("   状态:", runner.dir / "state.json", "| trace:", runner.tracer.path)
    return 0


def cmd_drill(args) -> int:
    import subprocess
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "m4_drill.py"),
                        *args], timeout=4000)
    return p.returncode


# 主表 = 界面 + 生命周期。日常操作全部走 ui（阶段 6 唯一入口收口）。
_CMDS = {"ui": cmd_ui, "status": cmd_status,
         "start": cmd_start, "stop": cmd_stop}
# 开发/诊断通道：`upi dev <name>`。仍在仓库里、仍可复现，只是不再作为
# 宣传用法（阶段 6 完成判据要求"文档命令表与 _CMDS/_DEV_CMDS 差集为空"，
# 所以两张表就是帮助文本的唯一事实来源，改表必须同步改 __doc__）。
# 拉起守护的 `-m upi dev monitor` 字面量只在 daemon.spawn_detached() 出现
# 一次（cmd_start 与 UI 都调它）。分层时若仍两处各写，漏改一处的表现是
# "点启动守护报失败"，看起来像守护坏了——分层重构最常见的自伤，已消除。
_DEV_CMDS = {"monitor": cmd_monitor, "detect": cmd_detect,
             "patrol-once": cmd_patrol_once, "resume": cmd_resume,
             "drill": cmd_drill, "report": cmd_report, "stats": cmd_stats}


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "dev":
        if not rest:
            print(f"用法: python -m upi dev <{'|'.join(sorted(_DEV_CMDS))}>")
            return 2
        fn = _DEV_CMDS.get(rest[0])
        if fn is None:
            print(f"未知开发子命令: {rest[0]}\n"
                  f"可用: {', '.join(sorted(_DEV_CMDS))}")
            return 2
        return fn(rest[1:])
    fn = _CMDS.get(cmd)
    if fn is not None:
        return fn(rest)
    if cmd in _DEV_CMDS:
        # 旧命令名给迁移路径而不是冷冰冰的"未知命令"：分层是本仓库自己的
        # 变更，按老文档敲命令的人需要知道去哪儿找它。
        tail = (" " + " ".join(rest)) if rest else ""
        print(f"该命令已移入开发通道：python -m upi dev {cmd}{tail}")
        print("日常操作请用界面：python -m upi ui（见 使用说明.md）")
        return 2
    print(f"未知命令: {cmd}\n可用: {', '.join(sorted(_CMDS))}"
          f"（另有 dev 开发通道）")
    return 2


if __name__ == "__main__":
    sys.exit(main())
