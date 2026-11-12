// B 模式开销测量件：浅档 profiler 常开 + 每 N 秒滚动清帧，逐帧采帧时与进程内存。
// 只做测量，不写工程。配置由 runs/_probe/bench_cfg.json 传入，结果写 bench_out.json。
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditorInternal;
using UnityEngine;
using UnityEngine.Profiling;

UpiBench.Main();
return UpiBench.LastLine;

public static class UpiBench
{
    const string Dir = @"D:/unity_profile_inspector/runs/_probe";
    const string CfgPath = Dir + "/bench_cfg.json";
    const string OutPath = Dir + "/bench_out.json";
    public static string LastLine = "";

    static string _mode = "off";
    static double _duration = 20.0;
    static double _clearEvery = 2.0;
    static bool _uncapped;
    static int _prevVSync, _prevTargetFPS;
    static bool _running;
    static double _t0, _tLast, _lastClear, _lastMem;
    static int _lastFrame = -1;
    static readonly List<double> _deltas = new List<double>();
    static readonly List<double> _mem = new List<double>();
    static bool _weProfiler, _weDriver, _weDeep, _weAlloc;
    static bool _prevProfileEditor;

    public static string Main()
    {
        string cfg = File.Exists(CfgPath) ? File.ReadAllText(CfgPath) : "{}";
        _mode = ReadStr(cfg, "profiler", "off");
        _duration = ReadNum(cfg, "duration_s", 20.0);
        _clearEvery = ReadNum(cfg, "clear_every_s", 2.0);
        _uncapped = ReadNum(cfg, "uncapped", 0) > 0;

        ApplyProfiler();
        if (_uncapped)
        {
            // 只有去掉 vsync/帧率上限，帧时才能反映 CPU 开销；测量结束恢复原值。
            try { _prevVSync = QualitySettings.vSyncCount; _prevTargetFPS = Application.targetFrameRate; }
            catch (Exception) { }
            try { QualitySettings.vSyncCount = 0; Application.targetFrameRate = -1; } catch (Exception) { }
        }

        _deltas.Clear(); _mem.Clear();
        _t0 = Now(); _tLast = _t0; _lastClear = _t0; _lastMem = _t0; _lastFrame = -1;
        _running = true;
        EditorApplication.update += Tick;
        LastLine = "bench installed mode=" + _mode;
        return LastLine;
    }

    static double Now() { return EditorApplication.timeSinceStartup; }

    static void ApplyProfiler()
    {
        try { _prevProfileEditor = ProfilerDriver.profileEditor; } catch (Exception) { }
        if (_mode == "off") return;
        try { if (!Profiler.enabled) { Profiler.enabled = true; _weProfiler = true; } } catch (Exception) { }
        try { if (!ProfilerDriver.enabled) { ProfilerDriver.enabled = true; _weDriver = true; } } catch (Exception) { }
        try { ProfilerDriver.profileEditor = true; } catch (Exception) { }
        if (_mode == "shallow_alloc")
        {
            try { if (!Profiler.enableAllocationCallstacks) { Profiler.enableAllocationCallstacks = true; _weAlloc = true; } } catch (Exception) { }
        }
    }

    static void RestoreProfiler()
    {
        // 深档在这个测量里从不打开；只把本件打开过的开关关掉，并清帧。
        try { if (_weAlloc) Profiler.enableAllocationCallstacks = false; } catch (Exception) { }
        try { if (_weDeep) ProfilerDriver.deepProfiling = false; } catch (Exception) { }
        try { if (_weDriver) ProfilerDriver.enabled = false; } catch (Exception) { }
        try { if (_weProfiler) Profiler.enabled = false; } catch (Exception) { }
        try { ProfilerDriver.profileEditor = _prevProfileEditor; } catch (Exception) { }
        try { ProfilerDriver.ClearAllFrames(); } catch (Exception) { }
    }

    static void Tick()
    {
        if (!_running) return;
        double now = Now();
        int f = Time.frameCount;
        if (f != _lastFrame)
        {
            if (_lastFrame >= 0) _deltas.Add((now - _tLast) * 1000.0);
            _lastFrame = f;
            _tLast = now;
        }
        if (now - _lastMem >= 1.0)
        {
            _mem.Add(MemMB());
            _lastMem = now;
        }
        if (_mode != "off" && now - _lastClear >= _clearEvery)
        {
            try { ProfilerDriver.ClearAllFrames(); } catch (Exception) { }
            _lastClear = now;
        }
        if (now - _t0 >= _duration) Finish();
    }

    static double MemMB()
    {
        try { return Process.GetCurrentProcess().WorkingSet64 / (1024.0 * 1024.0); }
        catch (Exception) { return 0; }
    }

    static void Finish()
    {
        _running = false;
        EditorApplication.update -= Tick;
        var names = _mode == "off" ? new List<string>() : SampleNames();
        double[] ds = _deltas.ToArray();
        Array.Sort(ds);
        var sb = new StringBuilder();
        sb.Append("{\"mode\":\"").Append(_mode).Append("\"")
          .Append(",\"duration_s\":").Append(Num(_duration))
          .Append(",\"frames\":").Append(ds.Length)
          .Append(",\"frame_ms\":{\"median\":").Append(Num(Pct(ds, 50)))
          .Append(",\"mean\":").Append(Num(Mean(ds)))
          .Append(",\"p95\":").Append(Num(Pct(ds, 95)))
          .Append(",\"max\":").Append(Num(ds.Length > 0 ? ds[ds.Length - 1] : 0))
          .Append("}")
          .Append(",\"mem_mb\":{\"start\":").Append(Num(_mem.Count > 0 ? _mem[0] : 0))
          .Append(",\"end\":").Append(Num(_mem.Count > 0 ? _mem[_mem.Count - 1] : 0))
          .Append(",\"max\":").Append(Num(MaxOf(_mem)))
          .Append(",\"samples\":[").Append(JoinNums(_mem)).Append("]}")
          .Append(",\"sample_names\":[");
        for (int i = 0; i < names.Count; i++)
        {
            if (i > 0) sb.Append(",");
            sb.Append(Q(names[i]));
        }
        sb.Append("]")
          .Append(",\"switches\":{\"profiler\":").Append(_weProfiler ? "true" : "false")
          .Append(",\"driver\":").Append(_weDriver ? "true" : "false")
          .Append(",\"deep\":").Append(_weDeep ? "true" : "false")
          .Append(",\"alloc_callstacks\":").Append(_weAlloc ? "true" : "false")
          .Append("},\"finished_at\":\"").Append(DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")).Append("\"}");
        Directory.CreateDirectory(Dir);
        File.WriteAllText(OutPath, sb.ToString(), new UTF8Encoding(false));
        if (_uncapped)
        {
            try { QualitySettings.vSyncCount = _prevVSync; } catch (Exception) { }
            try { Application.targetFrameRate = _prevTargetFPS; } catch (Exception) { }
        }
        RestoreProfiler();
        LastLine = "bench done mode=" + _mode + " frames=" + ds.Length;
    }

    static List<string> SampleNames()
    {
        var set = new SortedSet<string>();
        try
        {
            int last = ProfilerDriver.lastFrameIndex;
            int first = Math.Max(ProfilerDriver.firstFrameIndex, last - 2);
            for (int frame = first; frame <= last; frame++)
            {
                for (int ti = 0; ti < 8; ti++)
                {
                    using (var view = ProfilerDriver.GetRawFrameDataView(frame, ti))
                    {
                        if (!view.valid) break;
                        int n = view.sampleCount;
                        for (int i = 0; i < n && set.Count < 40; i++)
                        {
                            string name = view.GetSampleName(i);
                            if (string.IsNullOrEmpty(name)) continue;
                            if (name.Contains("ChromaGame") || name.Contains("BoardModel") || name.Contains("DevSim"))
                                set.Add(name);
                        }
                    }
                }
            }
        }
        catch (Exception) { }
        return new List<string>(set);
    }

    static double Pct(double[] sorted, double p)
    {
        if (sorted.Length == 0) return 0;
        double idx = (p / 100.0) * (sorted.Length - 1);
        int lo = (int)Math.Floor(idx), hi = (int)Math.Ceiling(idx);
        if (lo == hi) return sorted[lo];
        return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
    }
    static double Mean(double[] a) { if (a.Length == 0) return 0; double s = 0; foreach (var x in a) s += x; return s / a.Length; }
    static double MaxOf(List<double> a) { double m = 0; foreach (var x in a) if (x > m) m = x; return m; }
    static string JoinNums(List<double> a)
    {
        var sb = new StringBuilder();
        for (int i = 0; i < a.Count; i++) { if (i > 0) sb.Append(","); sb.Append(Num(a[i])); }
        return sb.ToString();
    }
    static string Num(double v) { return v.ToString("0.###", CultureInfo.InvariantCulture); }
    static string Q(string s) { return "\"" + (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\""; }

    static string ReadStr(string json, string key, string dflt)
    {
        int i = json.IndexOf("\"" + key + "\"");
        if (i < 0) return dflt;
        int c = json.IndexOf(':', i); if (c < 0) return dflt;
        int q1 = json.IndexOf('"', c + 1); if (q1 < 0) return dflt;
        int q2 = json.IndexOf('"', q1 + 1); if (q2 < 0) return dflt;
        return json.Substring(q1 + 1, q2 - q1 - 1);
    }

    static double ReadNum(string json, string key, double dflt)
    {
        int i = json.IndexOf("\"" + key + "\"");
        if (i < 0) return dflt;
        int c = json.IndexOf(':', i); if (c < 0) return dflt;
        int j = c + 1;
        while (j < json.Length && (json[j] == ' ' || json[j] == '\t' || json[j] == '\n' || json[j] == '\r')) j++;
        int k = j;
        while (k < json.Length && (char.IsDigit(json[k]) || json[k] == '.' || json[k] == '-' || json[k] == '+' || json[k] == 'e' || json[k] == 'E')) k++;
        double v;
        if (k > j && double.TryParse(json.Substring(j, k - j), NumberStyles.Float, CultureInfo.InvariantCulture, out v)) return v;
        return dflt;
    }
}
