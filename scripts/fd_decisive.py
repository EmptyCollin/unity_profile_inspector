"""scripts/fd_decisive.py —— 冷重启后的决定性实验（唯一待答问题）

Q: Frame Debugger 的事件缓冲能否以编程方式取到，并且能取到"哪一笔是谁画的"？

上一轮实验无效的原因（已查明）：编辑器播放循环停摆（frameCount 卡死不动）⇒
无新渲染帧 ⇒ 帧调试器无帧可抓 ⇒ count 恒为 0 属状态问题，不是能力问题。

开销测量必须按 off → on → off 三段（上一版我把 on 段写在 disable 之后，测错了）：
  p_off1 = 未开 FD；p_on = 开着 FD；p_off2 = 关掉 FD 复测。
  开销 = p_on 相对 (p_off1, p_off2) 均值的抬升；两次 off 差异本身当噪声看。
"""
from __future__ import annotations

import json
import statistics
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "fixtures" / "upi-fixture"
EXE = r"D:\unity_editor\6000.6.7f1\Editor\Unity.exe"
SCENE = "Assets/UpiFixtures/Scenes/Scene_F-GPU-01.unity"


def unity(*args: str, timeout: int = 200) -> dict:
    try:
        out = subprocess.run(["unity", *args, "--format", "json", "--no-banner", "--non-interactive"],
                             capture_output=True, text=True, timeout=timeout,
                             encoding="utf-8", errors="replace").stdout
    except subprocess.TimeoutExpired:
        return {"success": False, "errors": [{"code": "CLI_TIMEOUT", "message": ""}]}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"success": False, "errors": [{"code": "BAD_JSON", "message": out[:200]}]}


def cmd(name: str, *extra: str, timeout: int = 200) -> dict:
    return unity("command", name, "--project-path", str(PROJECT), *extra, timeout=timeout)


def ev(code: str, timeout: int = 260) -> str:
    r = cmd("script eval", "--code", code, timeout=timeout)
    if r.get("success"):
        res = (r["data"].get("result") or {})
        return str(res.get("result", res))
    return "ERR " + str(r.get("errors", [{}])[0].get("message", ""))[:300]


def state() -> dict:
    return (cmd("editor status").get("data") or {}).get("result") or {}


def frames_ok(label: str, gap: float = 4.0) -> tuple[bool, int]:
    a = state().get("frameCount")
    time.sleep(gap)
    b = state().get("frameCount")
    ok = (b or 0) > (a or 0)
    print(f"    {label}: frame {a} → {b}  推进={ok}")
    return ok, int(b or 0)


def perf_median(n: int = 8) -> dict:
    vals: dict[str, list[float]] = {}
    for _ in range(n):
        res = (cmd("get_performance", timeout=90).get("data") or {}).get("result") or {}
        for grp in ("render", "memory", "frameTiming"):
            for k, v in (res.get(grp) or {}).items():
                if isinstance(v, (int, float)):
                    vals.setdefault(k, []).append(float(v))
        time.sleep(1.0)
    return {k: statistics.median(v) for k, v in vals.items() if len(v) >= 3}


FD_TOGGLE = r'''
Type util = null;
foreach (var a in System.AppDomain.CurrentDomain.GetAssemblies()) {
  Type[] ts; try { ts = a.GetTypes(); } catch (System.Exception) { continue; }
  util = Array.Find(ts, x => x.Name == "FrameDebuggerUtility");
  if (util != null) break;
}
if (util == null) return "util NULL";
var se = util.GetMethod("SetEnabled", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static);
se.Invoke(null, new object[] { __ON__, 0 });
var fd = typeof(UnityEngine.FrameDebugger).GetProperty("enabled", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static);
return "set __ON__, FrameDebugger.enabled=" + fd.GetValue(null, null);
'''

READ = r'''
var sb = new System.Text.StringBuilder();
const System.Reflection.BindingFlags SF = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static;
const System.Reflection.BindingFlags IF = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance;
Type util = null, dt = null;
foreach (var a in System.AppDomain.CurrentDomain.GetAssemblies()) {
  Type[] ts; try { ts = a.GetTypes(); } catch (System.Exception) { continue; }
  if (util == null) util = Array.Find(ts, x => x.Name == "FrameDebuggerUtility");
  if (dt == null) dt = Array.Find(ts, x => x.Name == "FrameDebuggerEventData");
}
if (util == null) return "util NULL";
sb.Append("frame=" + UnityEngine.Time.frameCount + " rendered=" + UnityEngine.Time.renderedFrameCount + "\n");
int c = Convert.ToInt32(util.GetProperty("count", SF).GetValue(null, null));
sb.Append("util.count=" + c + " locallySupported=" + util.GetProperty("locallySupported", SF).GetValue(null, null) + "\n");
if (c <= 0) { sb.Append("缓冲为空：无当帧数据可读"); return sb.ToString(); }
var raw = util.GetMethod("GetFrameEvents", SF).Invoke(null, null) as Array;
sb.Append("events=" + (raw == null ? -1 : raw.Length) + "\n");
var ged = util.GetMethod("GetFrameEventData", SF);
var geo = util.GetMethod("GetFrameEventObject", SF);
string[] causes = null;
try { causes = util.GetMethod("GetBatchBreakCauseStrings", SF).Invoke(null, null) as string[]; } catch (Exception) { }
sb.Append("causes_len=" + (causes == null ? -1 : causes.Length) + "\n");
var fD = dt.GetField("m_DrawCallCount", IF);
var fS = dt.GetField("m_RealShaderName", IF);
var fP = dt.GetField("m_PassName", IF);
var fV = dt.GetField("m_VertexCount", IF);
int shown = 0, withObj = 0, realCause = 0;
for (int i = 0; i < raw.Length && shown < 10; i++) {
  object inst; try { inst = Activator.CreateInstance(dt); } catch (Exception) { break; }
  bool ok = false;
  try { ok = (bool)ged.Invoke(null, new object[] { raw.GetValue(i), inst }); } catch (Exception) { }
  if (!ok) continue;
  object go = null;
  try { go = geo == null ? null : geo.Invoke(null, new object[] { raw.GetValue(i) }); } catch (Exception) { }
  string nm = go is UnityEngine.GameObject ? ((UnityEngine.GameObject)go).name
            : (go is UnityEngine.Component ? ((UnityEngine.Component)go).name : "");
  if (go != null) withObj++;
  string cause = (causes != null && i < causes.Length) ? causes[i] : "?";
  if (!string.IsNullOrEmpty(cause) && cause != "Unknown reason." && cause != "") realCause++;
  int dc = fD == null ? 0 : Convert.ToInt32(fD.GetValue(inst));
  int vc = fV == null ? 0 : Convert.ToInt32(fV.GetValue(inst));
  if (dc <= 0) continue;
  shown++;
  string sh = fS == null ? "" : (string)fS.GetValue(inst);
  string ps = fP == null ? "" : (string)fP.GetValue(inst);
  if (cause != null && cause.Length > 44) cause = cause.Substring(0, 44);
  sb.Append("  #" + i + " dc=" + dc + " verts=" + vc + " obj=[" + nm + "] shader=" + sh + " pass=" + ps + " cause=" + cause + "\n");
}
sb.Append("展示=" + shown + " 可定位物体=" + withObj + " 有实际合批原因=" + realCause);
return sb.ToString();
'''


def main() -> int:
    print("[1] 退出停摆实例")
    r = cmd("editor quit")
    if not r.get("success"):
        print("    常规退出被拒（Play Mode 中），改 --discard")
        r = cmd("editor quit", "--discard", "true")
    if not r.get("success"):
        r = cmd("editor quit", "--discard")
    print("   ", json.dumps((r.get("data") or {}).get("result"), ensure_ascii=False)[:140]
          if r.get("success") else "仍失败: " + str(r.get("errors", [{}])[0].get("message", ""))[:200])
    for _ in range(36):
        time.sleep(5)
        if not state().get("status"):
            print("    实例已退出")
            break
    else:
        print("    !! 实例未退出，停止（避免双实例互相干扰）")
        return 2

    print("[2] 重启 GUI 编辑器")
    subprocess.Popen([EXE, "-projectPath", str(PROJECT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for i in range(40):
        time.sleep(12)
        st = state()
        if st.get("status") == "ready" and not st.get("compiling") and not st.get("domainReloadInProgress"):
            print(f"    就绪（第 {i + 1} 轮）")
            break
    else:
        print("    !! 未就绪")
        return 3

    print("[3] 载入 F-GPU-01 进 Play，解除 vsync 与后台暂停")
    cmd("scene open", "--path", SCENE)
    time.sleep(6)
    print("   ", (cmd("editor.playmode start").get("data") or {}).get("result"))
    time.sleep(9)
    print("   ", ev('UnityEngine.Application.runInBackground = true; UnityEngine.Application.targetFrameRate = -1; return "runBg=true fps=-1 play=" + UnityEngine.Application.isPlaying;')[:90])

    print("[4] p_off1：未开 Frame Debugger")
    ok0, _ = frames_ok("控制组")
    if not ok0:
        print("    !! 停摆复现，需人工看编辑器窗口（可能是弹窗阻塞）")
        return 4
    p_off1 = perf_median(8)
    print("    cpuFrameTimeMs =", round(p_off1.get("cpuFrameTimeMs", -1), 3),
          "mainThread =", round(p_off1.get("cpuMainThreadFrameTimeMs", -1), 3),
          "| drawCalls =", p_off1.get("drawCalls"), "setPass =", p_off1.get("setPassCalls"))

    print("[5] 开启 Frame Debugger")
    print("   ", ev(FD_TOGGLE.replace("__ON__", "true"))[:160])
    time.sleep(4)
    ok1, _ = frames_ok("开 FD 后")
    print("    读取当帧事件：")
    rd = ev(READ, timeout=300)
    print(rd[:2800])

    print("[6] p_on：FD 开着时帧时间")
    p_on = perf_median(8)
    print("    cpuFrameTimeMs =", round(p_on.get("cpuFrameTimeMs", -1), 3),
          "mainThread =", round(p_on.get("cpuMainThreadFrameTimeMs", -1), 3))

    print("[7] 关闭 FD → p_off2 复测（验证 off 段可重复）")
    print("   ", ev(FD_TOGGLE.replace("__ON__", "false"))[:120])
    time.sleep(3)
    p_off2 = perf_median(8)
    print("    cpuFrameTimeMs =", round(p_off2.get("cpuFrameTimeMs", -1), 3))

    offs = [x for x in (p_off1.get("cpuFrameTimeMs"), p_off2.get("cpuFrameTimeMs")) if x]
    on = p_on.get("cpuFrameTimeMs")
    overhead = None
    if offs and on:
        base = statistics.mean(offs)
        overhead = round((on - base) / base * 100, 1)
        print(f"\n    ⇒ 开启抓取开销 = {on:.3f} vs off基线均值 {base:.3f} → {overhead}%"
              f"（off 两段自身差异 {abs(offs[0] - offs[1]):.3f}ms 视为噪声）")

    rep = {"control_advance": ok0, "fd_advance": ok1, "read_output": rd,
           "p_off1": p_off1, "p_on": p_on, "p_off2": p_off2, "overhead_pct": overhead}
    out = ROOT / "runs" / "fd_decisive_report.json"
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print("报告:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
