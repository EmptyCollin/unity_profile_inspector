"""`python -m upi <command>` —— 管线统一入口（独立进程，与 AI 助手无关）。

  status        环境自检：Unity CLI / Editor 可达 / 标定链 / 探针 / 密钥存在性 / 巡检配置
  detect        水位检测一轮（等价 scripts/m1_detect.py）
  patrol-once   巡检单轮：发作(只读) + 水位(重采)；检出按配置唤起状态机
  monitor       前台周期巡检（Ctrl+C 停止；外部调度器用 patrol-once 拉起）
  resume        工单批准后续跑（--run <run_id>：从 fix 断点，不重烧采样/归因）
  drill ...     演练两阶段（透传 scripts/m4_drill.py 参数）

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

    proj = ROOT / "fixtures" / "upi-fixture"
    print(f"  夹具工程        : {'存在' if proj.is_dir() else '缺失'}")
    ok = ok and proj.is_dir()

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


def cmd_monitor(_args=None) -> int:
    from upi.agents.patrol import monitor_loop
    return monitor_loop()


def cmd_resume(args) -> int:
    """工单批准后续跑：python -m upi resume --run <run_id>

    巡检进程停在 awaiting_approval 的轮次，人在终端写 decision.json 之后，
    用本命令从 fix 断点续跑（不重烧 detect 采样与 diagnose 归因积分）。
    这是批准回流的**独立入口**——不依赖 AI 助手，也不依赖 m4_drill，
    补上它才算闭环：巡检发现 → 出工单 → 人批 → 自动修复验证 → 留证。
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


_CMDS = {"status": cmd_status, "detect": cmd_detect,
         "patrol-once": cmd_patrol_once, "monitor": cmd_monitor,
         "resume": cmd_resume, "drill": cmd_drill}


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    fn = _CMDS.get(cmd)
    if fn is None:
        print(f"未知命令: {cmd}\n可用: {', '.join(sorted(_CMDS))}")
        return 2
    return fn(rest)


if __name__ == "__main__":
    sys.exit(main())
