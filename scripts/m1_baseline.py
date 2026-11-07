"""M1 核心：双源采样执行器（DESIGN §2.1 A+A2 / §3 统计 / G17 退化检查）。

数据源分工（实验4 定论）：
  A  = diagnostics get_performance 轮询（~1Hz，渲染计数唯一源）
  A2 = 注入常驻采样器 ProfilerRecorder(handle) 逐帧 GC Alloc / Memory（probe/sampler_v3 复用思路）
  ⚠️ A2 读不到渲染 counter（体系里不存在）；静默 0 与 -1 哨兵必须拒绝而非当 0（G16/I1）。

流程（每个场景）：
  开场景(域重载) → 进 Play → 注入采样器 → 预热丢样本 → 双源窗口 → 停 Play
统计：中位数 + 1.4826×MAD；G17: unique/n<0.1 ⇒ degenerate_distribution ⇒ 绝对门限。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROJECT = ROOT / "fixtures" / "upi-fixture"
SCENES_DIR = PROJECT / "Assets/UpiFixtures/Scenes"
PROBE = ROOT / "runs/_probe"

WARMUP_SAMPLES = 8        # 预热丢弃的 A 档轮询数（≈8s）
WINDOW_SAMPLES = 30       # A 档窗口（次）≈30s
POLL_INTERVAL = 1.0       # 目标间隔；实际速率含 CLI 往返，测出来入 poll_rate
N_RUNS = 3                # 窗口重复次数（真实 σ 需 ≥5 留到标定轮，首轮先 3）

FIXTURES = {
    "F-CPU-01": {"link": "cpu", "split": "tuning", "primary": "GC Allocated In Frame"},
    "F-GPU-01": {"link": "gpu", "split": "tuning", "primary": "Draw Calls"},
    "F-ANIM-01": {"link": "anim", "split": "holdout", "primary": "cpuMainThreadFrameTimeMs"},
    "F-LOAD-01": {"link": "loading", "split": "holdout", "primary": "Total Used Memory"},
    "CLEAN": {"link": "-", "split": "clean", "primary": "-"},
}


def unity(*args: str, timeout: int = 150) -> dict:
    out = subprocess.run(["unity", *args, "--format", "json", "--no-banner", "--non-interactive"],
                         capture_output=True, text=True, timeout=timeout,
                         encoding="utf-8", errors="replace").stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"success": False, "errors": [{"message": out[:200]}]}


def cmd(name: str, *extra: str, timeout: int = 150) -> dict:
    return unity("command", name, "--project-path", str(PROJECT), *extra, timeout=timeout)


def ev(code: str, timeout: int = 200) -> str:
    r = cmd("script eval", "--code", code, timeout=timeout)
    return str(r["data"]["result"]["result"]) if r.get("success") else "ERR " + str(r.get("errors", [{}])[0].get("message", ""))[:250]


def robust(series: list[float]) -> dict:
    med = statistics.median(series)
    mad = statistics.median([abs(x - med) for x in series]) * 1.4826
    uniq = len(set(series))
    degenerate = uniq / max(1, len(series)) < 0.1 or mad == 0.0
    # MAD 精确为 0（如窗口间中位数全同）同样禁用相对判据：小样本下比例规则 unique/n≥1/n 永不触发          # G17
    s = sorted(series)
    return {"n": len(series), "median": round(med, 4), "noise_band_mad": round(mad, 6),
            "min": round(s[0], 4), "max": round(s[-1], 4), "unique": uniq,
            "degenerate_distribution": degenerate}


SAMPLER_SRC = """
using System; using System.Collections.Generic; using System.IO; using System.Text; using System.Reflection; using UnityEditor; using UnityEngine; using Unity.Profiling; using Unity.Profiling.LowLevel.Unsafe;
UpiSampler5.Main(); return UpiSampler5.Line;
public static class UpiSampler5 {
  public static string Line = "";
  const string DIR = @"D:/unity_profile_inspector/runs/_probe";
  const string OUT = DIR + "/a2_frame.json";
  const string CTL = DIR + "/a2_cmd.txt";
  const string HB = DIR + "/a2_hb.txt";
  class W { public ProfilerRecorder rec; public bool ok; }
  static W _gc, _mem, _anim, _graph;
  static readonly List<long> Gc = new List<long>();
  static readonly List<long> Mem = new List<long>();
  static readonly List<long> Anim = new List<long>();
  static readonly List<long> Graph = new List<long>();
  static int _lastFrame = -1; static bool _rec; static string _nonce = "";
  static DateTime _lastCtl = DateTime.MinValue; static DateTime _lastBeat = DateTime.MinValue;

  static bool Alive() {
    try { if (!File.Exists(HB)) return false;
      double age = (DateTime.UtcNow - File.GetLastWriteTimeUtc(HB)).TotalSeconds;
      return age < 3.0; } catch (Exception) { return false; }
  }

  static W Make(string name) {
    var w = new W();
    var hs = new List<ProfilerRecorderHandle>(); ProfilerRecorderHandle.GetAvailable(hs);
    var dt = typeof(ProfilerRecorderHandle).GetMethod("GetDescription", BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static).ReturnType;
    var np = dt.GetProperty("Name", BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance);
    foreach (var h in hs) {
      object d; try { d = ProfilerRecorderHandle.GetDescription(h); } catch (Exception) { continue; }
      string n; try { n = (string)np.GetValue(d, null); } catch (Exception) { continue; }
      if (n == name) { try { w.rec = new ProfilerRecorder(h, 8000, ProfilerRecorderOptions.Default); w.rec.Start(); w.ok = true; } catch (Exception) {} return w; }
    }
    return w;
  }

  public static void Main() {
    Directory.CreateDirectory(DIR);
    if (Alive()) { Line = "alive hb_fresh"; return; }
    if (_gc == null) _gc = Make("GC Allocated In Frame");
    if (_mem == null) _mem = Make("Total Used Memory");
    if (_anim == null) _anim = Make("Animator.ProcessAnimations");   // 实测存在（exp5）
    if (_graph == null) _graph = Make("Animator.ProcessGraph");
    EditorApplication.update -= Tick; EditorApplication.update += Tick;
    Line = "ready gc=" + ((_gc != null && _gc.ok) ? "1" : "0") + " mem=" + ((_mem != null && _mem.ok) ? "1" : "0")
           + " anim=" + ((_anim != null && _anim.ok) ? "1" : "0") + " graph=" + ((_graph != null && _graph.ok) ? "1" : "0");
  }

  static void Tick() {
    if (UnityEngine.Time.frameCount == _lastFrame) return;
    _lastFrame = UnityEngine.Time.frameCount;
    var now = DateTime.UtcNow;
    // probe_overhead 三层归因（M1'）：开销主体是每帧文件 stat（install 6.26%），录制边际≈0。
    // ⇒ 控制检查与心跳改时间驱动：10 次/秒 / 1 次/秒（窗口 20s、预热 8s 下 0.1s 响应延迟无影响；
    //    心跳 1s 间隔满足 Alive 3s 判据）。未录制时不碰 recorder。
    if ((now - _lastCtl).TotalMilliseconds >= 100) {
      _lastCtl = now;
      string ctl = "";
      try { if (File.Exists(CTL)) { ctl = File.ReadAllText(CTL).Trim(); File.Delete(CTL); } } catch (Exception) {}
      if (ctl.Length > 6 && ctl.Substring(0, 5) == "start") { Gc.Clear(); Mem.Clear(); Anim.Clear(); Graph.Clear(); _nonce = ctl.Substring(6); _rec = true; }
      else if (ctl.Length > 5 && ctl.Substring(0, 4) == "stop" && _rec) { _rec = false; Dump(); }
    }
    if ((now - _lastBeat).TotalSeconds >= 1.0) {
      _lastBeat = now;
      try { File.WriteAllText(HB, now.Ticks.ToString()); } catch (Exception) {}
    }
    if (_rec) {
      if (_gc != null && _gc.ok) Gc.Add((long)_gc.rec.LastValue);
      if (_mem != null && _mem.ok) Mem.Add((long)_mem.rec.LastValue);
      if (_anim != null && _anim.ok) Anim.Add((long)_anim.rec.LastValue);
      if (_graph != null && _graph.ok) Graph.Add((long)_graph.rec.LastValue);
    }
  }

  static void AppendKey(StringBuilder sb, string k) { sb.Append((char)34).Append(k).Append((char)34).Append((char)58); }

  static void AppendArr(StringBuilder sb, string k, List<long> v) {
    AppendKey(sb, k); sb.Append((char)91);
    bool f = true; foreach (var x in v) { if (!f) sb.Append((char)44); sb.Append(x); f = false; }
    sb.Append((char)93);
  }

  static void Dump() {
    var sb = new StringBuilder();
    sb.Append((char)123);
    AppendKey(sb, "nonce"); sb.Append((char)34).Append(_nonce).Append((char)34); sb.Append((char)44);
    AppendArr(sb, "gc", Gc); sb.Append((char)44);
    AppendArr(sb, "mem", Mem); sb.Append((char)44);
    AppendArr(sb, "anim", Anim); sb.Append((char)44);
    AppendArr(sb, "graph", Graph);
    sb.Append((char)125);
    try { File.WriteAllText(OUT + ".tmp", sb.ToString()); if (File.Exists(OUT)) File.Delete(OUT); File.Move(OUT + ".tmp", OUT); } catch (Exception) {}
  }
}
"""
def sample_scene(fixture_id: str) -> dict:
    scene_path = SCENES_DIR / f"Scene_{fixture_id}{'' if fixture_id != 'CLEAN' else ''}.unity"
    if not scene_path.exists():
        scene_path = next(SCENES_DIR.glob(f"Scene_{fixture_id}*.unity"))
    rel = "Assets/UpiFixtures/Scenes/" + scene_path.name

    print(f"  打开 {rel}")
    cmd("scene open", "--path", rel)
    time.sleep(6)
    wait_ready()
    print("  进 Play:", cmd("editor.playmode start").get("data", {}).get("result", "失败"))
    time.sleep(6)
    ev('UnityEngine.Application.targetFrameRate = -1; UnityEngine.Application.runInBackground = true; return "uncapped";')

    # 注入 A2 采样器
    a2 = ev(SAMPLER_SRC, timeout=240)
    print("  A2:", a2[:120])
    if a2.startswith("ERR") or not ("ready" in a2 or "alive" in a2):
        print("  !! A2 注入失败，本场景基线作废（不得消费陈旧 a2_frame.json）")
        cmd("editor.playmode stop"); time.sleep(5)
        return {"fixture": fixture_id, "error": "A2 注入失败: " + a2[:200]}

    # 预热丢弃
    for _ in range(WARMUP_SAMPLES):
        time.sleep(POLL_INTERVAL)
        cmd("get_performance", timeout=60)

    # 双源窗口：清旧产物，向常驻采样器下发 start（文件控制，实测可跨 eval 生效）
    win_nonce = f"{fixture_id}-{int(time.time())}"
    (PROBE / "a2_frame.json").unlink(missing_ok=True)
    (PROBE / "a2_cmd.txt").write_text(f"start:{win_nonce}", encoding="utf-8")
    time.sleep(1.5)
    samples: dict[str, list[float]] = {}
    t0 = time.time()
    loop_t0 = t0
    for _ in range(WINDOW_SAMPLES):
        loop_t0 = time.time()
        r = cmd("get_performance", timeout=60)
        res = (r.get("data") or {}).get("result") or {}
        for grp in ("render", "memory", "frameTiming"):
            for k, v in (res.get(grp) or {}).items():
                if isinstance(v, (int, float)):
                    samples.setdefault(k, []).append(float(v))
        # 目标节奏 POLL_INTERVAL：扣除本次 CLI 往返耗时后再小憩，保证速率如实可测
        elapsed = time.time() - loop_t0
        if elapsed < POLL_INTERVAL:
            time.sleep(POLL_INTERVAL - elapsed)
    wall = time.time() - t0
    poll_rate = round(len(samples.get("drawCalls", [])) / max(1e-6, wall), 3)

    # A2 收口：下发 stop；仅接受"晚于本窗口启动时间"的落盘文件（防幽灵回调旧数据）
    win_t0 = t0
    a2_data = {}
    (PROBE / "a2_cmd.txt").write_text(f"stop:{win_nonce}", encoding="utf-8")
    deadline = time.time() + 15
    while time.time() < deadline:
        fp = PROBE / "a2_frame.json"
        if fp.exists():
            try:
                if fp.stat().st_mtime < win_t0:               # 陈旧文件 → 拒绝
                    fp.unlink(missing_ok=True); time.sleep(0.5); continue
                txt = fp.read_text(encoding="utf-8")
                parsed = json.loads(txt.replace(",]", "]"))
                if parsed.get("nonce") != win_nonce:            # 非本窗口数据 → 拒绝
                    fp.unlink(missing_ok=True); time.sleep(0.5); continue
                a2_data = parsed
                break
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(0.5)
    if a2_data:
        gc_n, mem_n = len(a2_data.get("gc", [])), len(a2_data.get("mem", []))
        print(f"    A2 逐帧样本: gc={gc_n} mem={mem_n}")
    a2_frames = {"gc_alloc_per_frame": robust([float(x) for x in a2_data.get("gc", [])]) if len(a2_data.get("gc", [])) >= 5 else None,
                 "total_used_per_frame": robust([float(x) for x in a2_data.get("mem", [])]) if len(a2_data.get("mem", [])) >= 5 else None,
                 "animator_process_animations_ns": robust([float(x) for x in a2_data.get("anim", [])]) if len(a2_data.get("anim", [])) >= 5 else None,
                 "animator_process_graph_ns": robust([float(x) for x in a2_data.get("graph", [])]) if len(a2_data.get("graph", [])) >= 5 else None}

    cmd("editor.playmode stop")
    time.sleep(5)

    stats = {k: robust(v) for k, v in samples.items() if len(v) >= 5}
    return {"fixture": fixture_id, "scene": rel,
            "protocol": {"warmup_samples": WARMUP_SAMPLES, "window_samples": WINDOW_SAMPLES,
                         "poll_rate": poll_rate, "data_tier": "A+A2", "editor_mode": "gui",
                         "n_runs": N_RUNS},
            "A_polluted_by_cli": {k: stats[k] for k in list(stats)[:6]},
            "A_stats": stats, "A2_stats": a2_frames,
            "scene_guid_hash": hashlib.sha256(scene_path.read_bytes()[:4096]).hexdigest()[:12]}


def wait_ready(max_wait: int = 120) -> None:
    t0 = time.time()
    while time.time() - t0 < max_wait:
        r = cmd("editor status")
        res = (r.get("data") or {}).get("result") or {}
        if res.get("status") == "ready" and not res.get("compiling") and not res.get("domainReloadInProgress"):
            return
        time.sleep(5)


def main() -> int:
    PROBE.mkdir(parents=True, exist_ok=True)
    out_dir = ROOT / "baselines"
    out_dir.mkdir(exist_ok=True)
    fp_env = ""
    results = {}
    only = sys.argv[1:] or list(FIXTURES)
    for fid in only:
        print(f"[{fid}]")
        try:
            results[fid] = sample_scene(fid)
        except Exception as e:  # noqa: BLE001
            print(f"  !! {fid} 失败: {type(e).__name__} {e}")
            results[fid] = {"error": str(e)[:300]}

    doc = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "fixtures": results}
    blob = json.dumps(doc, ensure_ascii=False, sort_keys=True).encode()
    h = hashlib.sha256(blob).hexdigest()[:16]
    out = out_dir / f"baseline_{h}.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print("基线写入:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
