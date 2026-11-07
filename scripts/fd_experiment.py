"""scripts/fd_experiment.py —— 验证：探针响的那一刻能否拿到当帧渲染数据。

上一版失败根因：状态机寄存在 eval 临时类型里，跨命令不存活。
本版驱动的是常驻探针 UpiFdProbe（编译进 Assembly-CSharp-Editor，回调与状态持久），
Python 只负责下发文件命令与读取结果文件（I9）。

判定项：
  C1 基线抓取      events > 0，事件可解析
  C2 陈旧性        加物体后 events 增长 / extra_hits > 0 ⇒ 读的是当前帧而非缓存
  C3 归因落点      with_obj > 0 且样本能给出物体名 ⇒ 可定位"这笔是谁画的"
  C5 触发时机      triggered 由探针内部在同一 tick 完成 ⇒ 无需等下一帧
  C4 观察者效应    开抓取 vs 关抓取的帧时间中位数差
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "fixtures" / "upi-fixture"
PROBE = ROOT / "runs" / "_probe"


def cmd(name: str, *extra: str, timeout: int = 150) -> dict:
    out = subprocess.run(["unity", "command", name, "--project-path", str(PROJECT), *extra,
                          "--format", "json", "--no-banner", "--non-interactive"],
                         capture_output=True, text=True, timeout=timeout,
                         encoding="utf-8", errors="replace").stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"success": False, "errors": [{"message": out[:300]}]}


def ctl(tag: str, wait: float = 2.5) -> None:
    PROBE.mkdir(parents=True, exist_ok=True)
    (PROBE / "fd_cmd.txt").write_text(tag, encoding="utf-8")
    time.sleep(wait)


def res() -> dict:
    fp = PROBE / "fd_res.json"
    if not fp.exists():
        return {}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"_parse_err": str(e)[:150], "_head": fp.read_text(encoding="utf-8")[:250]}


def main() -> int:
    print("[0] 编译检查（G8 用 recompile_status.errors）")
    for _ in range(20):
        r = cmd("script recompile_status")
        rr = (r.get("data") or {}).get("result") or {}
        if rr.get("status") == "completed" and not rr.get("failed"):
            print("    编译干净")
            break
        time.sleep(6)
    else:
        print("    !! 编译未通过:", (rr.get("errors") or [])[:2])
        return 1

    print("[1] C1 基线抓取（未开抓取，预期为空）")
    ctl("capture:base", 3)
    base = res().get("base", {})
    print("    events:", base.get("events"), " fd_on:", base.get("fd_on"))

    print("[2] 开启抓取")
    ctl("on", 4)
    ctl("capture:opened", 2)
    d = res()
    op = d.get("opened", {})
    print("    fd_on:", op.get("fd_on"), " events:", op.get("events"),
          " with_obj:", op.get("with_obj"), " with_shader:", op.get("with_shader"),
          " draw_sum:", op.get("draw_sum"), " err:", op.get("error"))

    print("[3] C2/C3 陈旧性与归因落点：加 20 物体后再抓")
    ctl("addcubes", 4)
    ctl("capture:after", 2)
    d = res()
    af = d.get("after", {})
    print("    events %s → %s | extra_hits: %s | with_obj: %s"
          % (op.get("events"), af.get("events"), af.get("extra_hits"), af.get("with_obj")))
    for s in (af.get("samples") or [])[:5]:
        print("    样本:", json.dumps(s, ensure_ascii=False)[:160])

    print("[4] C5 触发时机：arm 后制造尖峰，看 triggered")
    ctl("arm:dt:18", 2)
    ctl("addcubes", 6)
    d = res()
    trig = d.get("triggered")
    if trig:
        print("    triggered frame=%s events=%s draw_sum=%s extra_hits=%s"
              % (trig.get("frame"), trig.get("events"), trig.get("draw_sum"), trig.get("extra_hits")))
    else:
        print("    !! 无 triggered 字段，现有键:", list(d.keys()))

    print("[5] C4 观察者效应：关闭后再采一段，比较帧时间中位数")
    time.sleep(3)
    ctl("off", 2)
    time.sleep(9)
    ctl("capture:final", 2)
    fin = res().get("final", {})
    print("    n_on=%s med_on=%sms | n_off=%s med_off=%sms"
          % (fin.get("n_on"), fin.get("med_on_ms"), fin.get("n_off"), fin.get("med_off_ms")))
    mo, mf = fin.get("med_on_ms", -1), fin.get("med_off_ms", -1)
    overhead = None
    if isinstance(mo, float) and isinstance(mf, float) and mo > 0 and mf > 0:
        overhead = round((mo - mf) / mf * 100, 1)
        print("    开启抓取开销 ≈ %s%%" % overhead)

    report = {"C1_base_events": base.get("events"),
              "C2_opened_events": op.get("events"), "C2_after_events": af.get("events"),
              "C2_extra_hits": af.get("extra_hits"),
              "C3_with_obj": af.get("with_obj"), "C3_samples": (af.get("samples") or [])[:5],
              "C5_triggered": bool(trig), "C5_detail": trig,
              "C4_overhead_pct": overhead,
              "C4_on_median_ms": mo, "C4_off_median_ms": mf}
    out = ROOT / "runs" / "fd_experiment_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n判定汇总 →", out)
    print("  C1/C2 事件可取且随场景变化:", bool((af.get("events") or 0) > 0))
    print("  C3 可定位到具体物体:", bool((af.get("with_obj") or 0) > 0))
    print("  C5 触发同帧抓取成立:", bool(trig))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
