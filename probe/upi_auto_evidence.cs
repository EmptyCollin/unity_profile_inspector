// B 模式最小闭环：浅档 profiler 常开（滚动清帧）→ 越界触发 → 再录 N 帧 → dump 前后窗口。
// 触发量：GC Allocated In Frame（Memory 类目）。dump 格式与 evidence_v1 兼容，可直接进候选管道。
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditorInternal;
using Unity.Profiling;
using UnityEngine;
using UnityEngine.Profiling;

UpiAutoEvidence.Main();
return UpiAutoEvidence.LastLine;

public static class UpiAutoEvidence
{
    const string Dir = @"D:/unity_profile_inspector/runs/_probe";
    const string CfgPath = Dir + "/auto_cfg.json";
    const string Out = Dir + "/auto_evidence.json";
    const string Status = Dir + "/auto_status.json";
    public static string LastLine = "";

    static long _triggerBytes = 64;
    static int _afterFrames = 10;
    static int _maxThreads = 8;
    static long _budgetMs = 4000;
    static double _durationS = 30;

    static ProfilerRecorder _gc;
    static bool _running, _triggered, _done;
    static int _breachProfFrame = -1;
    static long _breachValue;
    static int _afterLeft;
    static int _lastGameFrame = -1;
    static int _warmupFrames;
    static bool _warmupDone;
    static double _t0, _lastClear;
    static readonly List<string> _ring = new List<string>();
    static readonly List<long> _series = new List<long>();
    static bool _weProfiler;

    public static string Main()
    {
        LoadCfg();
        try
        {
            if (!Profiler.enabled) { Profiler.enabled = true; _weProfiler = true; }
            if (!ProfilerDriver.enabled) ProfilerDriver.enabled = true;
            ProfilerDriver.profileEditor = true;
            ProfilerDriver.deepProfiling = false;
            Profiler.enableAllocationCallstacks = false;
        }
        catch (Exception) { }
        try { _gc = ProfilerRecorder.StartNew(ProfilerCategory.Memory, "GC Allocated In Frame", 128); }
        catch (Exception) { }
        _t0 = EditorApplication.timeSinceStartup;
        _lastClear = _t0;
        _running = true; _triggered = false; _done = false;
        EditorApplication.update += Tick;
        LastLine = "auto installed trigger=" + _triggerBytes + "B after=" + _afterFrames;
        return LastLine;
    }

    static void Tick()
    {
        if (!_running) return;
        double now = EditorApplication.timeSinceStartup;
        int gf = Time.frameCount;
        bool newFrame = gf != _lastGameFrame;
        if (newFrame) _lastGameFrame = gf;

        long v = 0;
        try { if (_gc.Valid) v = _gc.LastValue; } catch (Exception) { }

        // B 模式：浅档常开，但滚动清帧，内存有上限。
        if (now - _lastClear >= 2.0)
        {
            try { ProfilerDriver.ClearAllFrames(); } catch (Exception) { }
            _lastClear = now;
        }

        // 预热：至少走 5 帧再允许触发，否则 replay/start_game 那一帧会在
        // profiler 还没有帧时立刻触发（实测 breach_frame=-1、窗口只有 [0]）。
        if (newFrame && !_warmupDone && ++_warmupFrames >= 5) _warmupDone = true;

        if (!_triggered && _warmupDone && v >= _triggerBytes)
        {
            _triggered = true;
            _breachValue = v;
            _breachProfFrame = ProfilerDriver.lastFrameIndex + 1;
            _afterLeft = _afterFrames;
            _series.Add(v);
            _ring.Add("{\"frame\":" + gf + ",\"gc_bytes\":" + v + "}");
            WriteStatus("triggered");
        }
        else if (_triggered && newFrame)
        {
            _series.Add(v);
            _ring.Add("{\"frame\":" + gf + ",\"gc_bytes\":" + v + "}");
            if (_ring.Count > 64) _ring.RemoveAt(0);
            _afterLeft--;
            if (_afterLeft <= 0) { Capture(); return; }
        }

        if (now - _t0 >= _durationS)
        {
            if (!_triggered) { WriteStatus("no_trigger"); }
            Finish();
        }
    }

    static void Capture()
    {
        var budget = Stopwatch.StartNew();
        int last = ProfilerDriver.lastFrameIndex;
        int firstAvailable = ProfilerDriver.firstFrameIndex;
        int from = Math.Max(firstAvailable, _breachProfFrame - 1);
        int to = Math.Min(last, _breachProfFrame + _afterFrames);
        var funcs = new Dictionary<string, long[]>();
        var frames = new List<int>();
        bool truncated = false;
        for (int frame = to; frame >= from; frame--)
        {
            if (budget.ElapsedMilliseconds > _budgetMs) { truncated = true; break; }
            frames.Add(frame);
            for (int ti = 0; ti < _maxThreads; ti++)
            {
                if (budget.ElapsedMilliseconds > _budgetMs) { truncated = true; break; }
                using (var view = ProfilerDriver.GetRawFrameDataView(frame, ti))
                {
                    if (!view.valid) break;
                    int n = view.sampleCount;
                    for (int i = 0; i < n; i++)
                    {
                        if (budget.ElapsedMilliseconds > _budgetMs) { truncated = true; break; }
                        string name = view.GetSampleName(i);
                        if (string.IsNullOrEmpty(name)) continue;
                        if (!IsProjectName(name)) continue;
                        string key = frame + "|" + name;
                        long[] a;
                        if (!funcs.TryGetValue(key, out a)) { a = new long[2]; funcs[key] = a; }
                        a[0]++;
                        a[1] += (long)view.GetSampleTimeNs(i);
                    }
                }
            }
        }
        var fs = new List<string>();
        foreach (var kv in funcs)
        {
            int sep = kv.Key.IndexOf('|');
            fs.Add("{\"frame_idx\":" + kv.Key.Substring(0, sep) + ",\"symbol\":" +
                   Q(kv.Key.Substring(sep + 1)) + ",\"calls\":" + kv.Value[0] +
                   ",\"total_ms\":" + (kv.Value[1] / 1e6).ToString("0.000", CultureInfo.InvariantCulture) +
                   ",\"self_ms\":" + (kv.Value[1] / 1e6).ToString("0.000", CultureInfo.InvariantCulture) +
                   ",\"data_tier\":\"A2\"}");
        }
        string shape = Shape();
        var sb = new StringBuilder();
        sb.Append("{\"run_id\":\"auto-b\",\"project_id\":\"\",\"scene\":\"\"")
          .Append(",\"protocol_fingerprint\":\"\"")
          .Append(",\"frames\":[").Append(string.Join(",", frames.ConvertAll(x => x.ToString()).ToArray())).Append("]")
          .Append(",\"callstacks\":[]")
          .Append(",\"functions\":[").Append(string.Join(",", fs.ToArray())).Append("]")
          .Append(",\"render_objects\":[]")
          .Append(",\"allocations\":[]")
          .Append(",\"blindspots\":[]")
          .Append(",\"meta\":{\"source\":\"auto-b-shallow\",\"trigger_bytes\":").Append(_triggerBytes)
          .Append(",\"breach_prof_frame\":").Append(_breachProfFrame)
          .Append(",\"breach_value\":").Append(_breachValue)
          .Append(",\"after_frames\":").Append(_afterFrames)
          .Append(",\"shape\":\"").Append(shape).Append("\"")
          .Append(",\"truncated\":").Append(truncated ? "true" : "false")
          .Append(",\"series\":[").Append(string.Join(",", _ring.ToArray())).Append("]")
          .Append("}}");
        Directory.CreateDirectory(Dir);
        try { File.WriteAllText(Out, sb.ToString(), new UTF8Encoding(false)); } catch (Exception) { }
        WriteStatus("captured");
        Finish();
    }

    static string Shape()
    {
        // 触发帧算第 0 个；之后连续 >= 触发的帧数决定脉冲/平台。
        int run = 0;
        for (int i = 0; i < _series.Count; i++)
        {
            if (_series[i] >= _triggerBytes) run++; else break;
        }
        return run >= 3 ? "plateau" : "pulse";
    }

    static void WriteStatus(string state)
    {
        try
        {
            File.WriteAllText(Status, "{\"state\":\"" + state + "\",\"triggered\":" +
                (_triggered ? "true" : "false") + ",\"breach_value\":" + _breachValue + "}");
        }
        catch (Exception) { }
    }

    static void Finish()
    {
        if (_done) return;
        _done = true;
        _running = false;
        EditorApplication.update -= Tick;
        try { if (_gc.Valid) _gc.Dispose(); } catch (Exception) { }
        try { if (_weProfiler) Profiler.enabled = false; } catch (Exception) { }
        try { ProfilerDriver.deepProfiling = false; } catch (Exception) { }
        try { Profiler.enableAllocationCallstacks = false; } catch (Exception) { }
        try { ProfilerDriver.ClearAllFrames(); } catch (Exception) { }
        WriteStatus("finished");
        LastLine = "auto done triggered=" + _triggered;
    }

    static bool IsProjectName(string name)
    {
        return name.Contains("ChromaGame") || name.Contains("BoardModel") ||
               name.Contains("DevSim");
    }

    static string Q(string s)
    {
        return "\"" + (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }

    static void LoadCfg()
    {
        try
        {
            if (!File.Exists(CfgPath)) return;
            string j = File.ReadAllText(CfgPath);
            _triggerBytes = (long)ReadNum(j, "trigger_bytes", 64);
            _afterFrames = (int)Math.Min(30, Math.Max(2, ReadNum(j, "after_frames", 10)));
            _maxThreads = (int)Math.Min(64, Math.Max(1, ReadNum(j, "threads", 8)));
            _budgetMs = (long)Math.Min(20000, Math.Max(500, ReadNum(j, "budget_ms", 4000)));
            _durationS = Math.Min(120, Math.Max(5, ReadNum(j, "duration_s", 30)));
        }
        catch (Exception) { }
    }

    static double ReadNum(string json, string key, double dflt)
    {
        int i = json.IndexOf("\"" + key + "\"");
        if (i < 0) return dflt;
        int c = json.IndexOf(':', i);
        if (c < 0) return dflt;
        int j = c + 1;
        while (j < json.Length && (json[j] == ' ' || json[j] == '\t' || json[j] == '\n' || json[j] == '\r')) j++;
        int k = j;
        while (k < json.Length && (char.IsDigit(json[k]) || json[k] == '.' || json[k] == '-' || json[k] == '+')) k++;
        double v;
        if (k > j && double.TryParse(json.Substring(j, k - j), NumberStyles.Float,
                                      CultureInfo.InvariantCulture, out v)) return v;
        return dflt;
    }
}
