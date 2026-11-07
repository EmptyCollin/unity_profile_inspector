"""scripts/build_fixtures.py —— M1 步骤1：导入夹具脚本 → 编译 → 反射构建五套夹具场景。

为什么走反射：eval 的临时程序集不引用 Assembly-CSharp-Editor（实测类型名不可见），
直接写类型名会 CS0103。⇒ 用 AppDomain 找类型后 Invoke（DESIGN I8：不假设通路可用）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "fixtures" / "upi-fixture"


def unity(*args: str, timeout: int = 150) -> dict:
    out = subprocess.run(["unity", *args, "--format", "json", "--no-banner", "--non-interactive"],
                         capture_output=True, text=True, timeout=timeout,
                         encoding="utf-8", errors="replace").stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"success": False, "errors": [{"message": out[:300]}]}


def cmd(name: str, *extra: str, timeout: int = 150) -> dict:
    return unity("command", name, "--project-path", str(PROJECT), *extra, timeout=timeout)


def ev(code: str, timeout: int = 180) -> str:
    r = cmd("script eval", "--code", code, timeout=timeout)
    if r.get("success"):
        return str(r["data"]["result"]["result"])
    return "ERR " + str(r.get("errors", [{}])[0].get("message", ""))[:300]


def wait_compile(max_wait: int = 240) -> None:
    t0 = time.time()
    while time.time() - t0 < max_wait:
        s = cmd("editor status")
        r = (s.get("data") or {}).get("result") or {}
        if r.get("status") == "ready" and not r.get("compiling") and not r.get("domainReloadInProgress"):
            return
        time.sleep(6)


def main() -> int:
    missing = [p for p in (
        PROJECT / "Assets/UpiFixtures/F_CPU_01/CombatSkillManager.cs",
        PROJECT / "Assets/UpiFixtures/F_GPU_01/BatchBreaker.cs",
        PROJECT / "Assets/UpiFixtures/F_ANIM_01/AnimatorOverhead.cs",
        PROJECT / "Assets/UpiFixtures/F_LOAD_01/SyncLoadHitch.cs",
        PROJECT / "Assets/UpiFixtures/Runtime/Drivers.cs",
        PROJECT / "Assets/UpiFixtures/Editor/FixtureBuilder.cs",
    ) if not p.exists()]
    print(f"[1] 磁盘夹具脚本: {6 - len(missing)}/6 就位" + (f" 缺失={missing}" if missing else ""))
    if missing:
        return 1

    print("[2] 触发资源导入")
    print("   ", ev("UnityEditor.AssetDatabase.Refresh(); return \"refreshed\";")[:120])
    time.sleep(8)
    wait_compile()
    print("   编译/重载结束")

    print("[3] 校验类型已进程序集")
    found = ev('''
var sb = new System.Text.StringBuilder();
foreach (var a in System.AppDomain.CurrentDomain.GetAssemblies()) {
  System.Type[] ts; try { ts = a.GetTypes(); } catch (System.Exception) { continue; }
  foreach (var t in ts)
    if (t.FullName != null && t.FullName.StartsWith("Upi.Fixtures"))
      sb.Append(t.FullName + " [" + a.GetName().Name + "]" + "\\n");
}
return sb.Length == 0 ? "NOT_FOUND" : sb.ToString();''')
    print(found[:900])
    if found.strip().startswith("NOT_FOUND") or found.startswith("ERR"):
        print("!! 类型未编译进程序集：查控制台错误")
        c = cmd("console get")
        msgs = ((c.get("data") or {}).get("result") or {}).get("messages") or []
        for m in msgs:
            if isinstance(m, dict) and str(m.get("type", "")).lower() == "error":
                print("   ERROR:", str(m.get("message", ""))[:300])
        return 2

    print("[4] 反射调用 BuildAll（含资源占位生成，耗时较长）")
    out = ev('''
var sb = new System.Text.StringBuilder();
foreach (var a in System.AppDomain.CurrentDomain.GetAssemblies()) {
  System.Type[] ts; try { ts = a.GetTypes(); } catch (System.Exception) { continue; }
  var t = Array.Find(ts, x => x.FullName == "Upi.Fixtures.EditorTools.FixtureBuilder");
  if (t == null) continue;
  var mi = t.GetMethod("BuildAll", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static);
  if (mi == null) { sb.Append("BuildAll 方法未找到"); break; }
  try { mi.Invoke(null, null); sb.Append("BuildAll OK via " + a.GetName().Name); }
  catch (System.Exception e) { sb.Append("Invoke 异常: " + (e.InnerException ?? e).GetType().Name + " :: " + (e.InnerException ?? e).Message); }
  break;
}
if (sb.Length == 0) sb.Append("FixtureBuilder 类型仍未加载");
return sb.ToString();''', timeout=300)
    print("   ", out[:400])

    time.sleep(6)
    wait_compile()
    scenes = sorted((PROJECT / "Assets/UpiFixtures/Scenes").glob("*.unity")) if (PROJECT / "Assets/UpiFixtures/Scenes").exists() else []
    payloads = list((PROJECT / "Assets/UpiFixtures/Resources").glob("*.txt")) if (PROJECT / "Assets/UpiFixtures/Resources").exists() else []
    print(f"[5] 产物核验: 场景={len(scenes)} 占位资源={len(payloads)}")
    for s in scenes:
        print("   ", s.name)
    return 0 if len(scenes) >= 5 else 3


if __name__ == "__main__":
    raise SystemExit(main())
