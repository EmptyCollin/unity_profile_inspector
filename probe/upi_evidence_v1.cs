// I8a 外部证据采集器：从 Profiler RawFrameDataView 读样本/函数/调用栈。
// 通过 `unity command script eval --file` 注入；不改被测工程。
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditorInternal;
using UnityEditor.Profiling;
using UnityEngine.Profiling;
using Unity.Profiling;

UpiEvidenceV1.Main();
return UpiEvidenceV1.LastLine;

public static class UpiEvidenceV1
{
    const string Out = @"D:/unity_profile_inspector/runs/_probe/evidence_v1.json";
    const string CfgPath = @"D:/unity_profile_inspector/runs/_probe/capture_cfg.json";
    public static string LastLine = "";
    static int _ticks;
    static bool _done;
    static int _startFrame;
    static DateTime _startUtc;
    // 取证参数（用户可在 config/i8_lab.json 或 Lab 页面改；每次采集前由 Lab 写到这里）
    static int _maxThreads = 8;
    static int _framesBack = 8;
    static long _budgetMs = 6000;
    static bool _weEnabledProfiler, _weEnabledDriver, _weEnabledDeep, _weEnabledAlloc;
    static bool _prevProfileEditor;
    static readonly Dictionary<string, ProfilerRecorder> _rec = new Dictionary<string, ProfilerRecorder>();
    static readonly string[] MarkerNames = {
        "ChromaGame.Update", "ChromaGame.UpdateHud",
        "ChromaGame.RefreshItemLabels", "ChromaGame.UpdateBody",
        "ChromaGame.ResolvePendingWhenIdle",
        "BoardModel.FindMatches"
    };

    public static string Main()
    {
        LoadCfg();
        // 只在这些开关确实关闭时才打开：反复“打开已打开”的 profiler 会重启采样流，
        // 实测可把 ProfilerEditor 缓冲推到 1GB 并在采集途中 OOM 崩溃。
        try { if (!Profiler.enabled) { Profiler.enabled = true; _weEnabledProfiler = true; } } catch (Exception) { }
        try { if (!ProfilerDriver.enabled) { ProfilerDriver.enabled = true; _weEnabledDriver = true; } } catch (Exception) { }
        try { _prevProfileEditor = ProfilerDriver.profileEditor; } catch (Exception) { }
        try { ProfilerDriver.profileEditor = true; } catch (Exception) { }
        try { if (!ProfilerDriver.deepProfiling) { ProfilerDriver.deepProfiling = true; _weEnabledDeep = true; } } catch (Exception) { }
        try { if (!Profiler.enableAllocationCallstacks) { Profiler.enableAllocationCallstacks = true; _weEnabledAlloc = true; } } catch (Exception) { }
        foreach (var name in MarkerNames)
        {
            try
            {
                var r = ProfilerRecorder.StartNew(ProfilerCategory.Scripts, name, 128);
                if (r.Valid) _rec[name] = r;
            }
            catch (Exception) { }
        }
        _done = false;
        _ticks = 0;
        _startFrame = ProfilerDriver.lastFrameIndex;
        _startUtc = DateTime.UtcNow;
        EditorApplication.update += Tick;
        LastLine = "installed";
        return LastLine;
    }

    static void Tick()
    {
        if (_done) return;
        _ticks++;
        // 按“帧”等待，而不是按 update tick：进入 Play Mode 后 profiler 历史很短，
        // 60 tick(~30ms) 内可能连一帧都没有，最后 4 帧会完全错过 10Hz 的 HUD 更新。
        int now = ProfilerDriver.lastFrameIndex;
        bool enoughFrames = now - _startFrame >= 2;
        bool timedOut = (DateTime.UtcNow - _startUtc).TotalSeconds > 4.0;
        if (!enoughFrames && !timedOut) return;
        _done = true;
        EditorApplication.update -= Tick;
        try { Capture(); }
        catch (Exception e)
        {
            LastLine = "capture-error: " + e;
            try { File.WriteAllText(Out + ".err", e.ToString()); } catch (Exception) { }
        }
        finally { DisposeRecorders(); RestoreProfilerState(); }
    }

    /// <summary>采集结束必须把“录像机”关掉并清帧：否则编辑器会一直逐帧录制，
    /// 内存随会话时间线性上涨（实测 4 分钟到 7GB，把 dwm/DSH 一起带崩）。</summary>
    static void RestoreProfilerState()
    {
        // 先关掉只由本脚本打开的开关，再清已录帧释放缓冲；不动用户自己开着的状态。
        try { if (_weEnabledAlloc) Profiler.enableAllocationCallstacks = false; } catch (Exception) { }
        try { if (_weEnabledDeep) ProfilerDriver.deepProfiling = false; } catch (Exception) { }
        try { if (_weEnabledDriver) ProfilerDriver.enabled = false; } catch (Exception) { }
        try { if (_weEnabledProfiler) Profiler.enabled = false; } catch (Exception) { }
        try { ProfilerDriver.profileEditor = _prevProfileEditor; } catch (Exception) { }
        try { ProfilerDriver.ClearAllFrames(); } catch (Exception) { }
    }

    static void LoadCfg()
    {
        try
        {
            if (!File.Exists(CfgPath)) return;
            string j = File.ReadAllText(CfgPath);
            _maxThreads = (int)Math.Min(64, Math.Max(1, ReadNum(j, "evidence_threads", 8)));
            _framesBack = (int)Math.Min(32, Math.Max(1, ReadNum(j, "evidence_frames", 8)));
            _budgetMs = (long)Math.Min(20000, Math.Max(500, ReadNum(j, "evidence_budget_ms", 6000)));
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
        while (k < json.Length && (char.IsDigit(json[k]) || json[k] == '.' || json[k] == '-' || json[k] == '+'))
            k++;
        double v;
        if (k > j && double.TryParse(json.Substring(j, k - j),
                System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out v)) return v;
        return dflt;
    }

    static void DisposeRecorders()
    {
        foreach (var kv in _rec) { try { kv.Value.Dispose(); } catch (Exception) { } }
        _rec.Clear();
    }

    static void Capture()
    {
        // 硬预算：主线程上的回调一旦失控，Python 的收尾 eval 也进不来（主线程被占），
        // profiler 会一直录到内存爆掉（实测 18GB）。这里到点就截断，先保证能落盘。
        var budget = Stopwatch.StartNew();
        bool truncated = false;
        int last = ProfilerDriver.lastFrameIndex;
        int first = Math.Max(ProfilerDriver.firstFrameIndex, last - (_framesBack - 1));
        if (first < 0) first = last;
        try { File.WriteAllText(Out + ".start", "start " + first + "-" + last); } catch (Exception) { }
        var frames = new List<int>();
        var funcs = new Dictionary<string, long[]>();
        var stacks = new List<string>();
        var allocs = new List<string>();
        int threads = 0;
        // 从最新帧往前扫：预算不足时优先保住“刚发生”的帧，旧帧被截断没关系。
        for (int frame = last; frame >= first; frame--)
        {
            if (budget.ElapsedMilliseconds > _budgetMs) { truncated = true; break; }
            frames.Add(frame);
            for (int ti = 0; ti < _maxThreads; ti++)
            {
                if (budget.ElapsedMilliseconds > _budgetMs) { truncated = true; break; }
                using (var view = ProfilerDriver.GetRawFrameDataView(frame, ti))
                {
                    if (!view.valid) break;
                    if (frame == last) threads++;
                    int n = view.sampleCount;
                    for (int i = 0; i < n; i++)
                    {
                        if (budget.ElapsedMilliseconds > _budgetMs) { truncated = true; break; }
                        string name = view.GetSampleName(i);
                        if (string.IsNullOrEmpty(name)) continue;

                        // GC.Alloc 的样本名不叫工程符号；先处理调用栈，再过滤函数名。
                        if (name == "GC.Alloc" && stacks.Count < 64)
                        {
                            var ptrs = new List<ulong>();
                            try { view.GetSampleCallstack(i, ptrs); } catch (Exception) { }
                            if (ptrs.Count == 0) continue;
                            long bytes = 0;
                            try { bytes = (long)view.GetSampleMetadataAsLong(i, 0); } catch (Exception) { }
                            var stackFrames = new List<string>();
                            bool hasProject = false;
                            foreach (var addr in ptrs)
                            {
                                try
                                {
                                    var mi = view.ResolveMethodInfo(addr);
                                    if (mi.methodName == null) continue;
                                    if (IsProjectName(mi.methodName) ||
                                        (!string.IsNullOrEmpty(mi.sourceFileName) &&
                                         mi.sourceFileName.Replace('\\', '/').Contains("/Assets/DevSim/")))
                                        hasProject = true;
                                    stackFrames.Add(FrameJson(mi.methodName, mi.sourceFileName, (int)mi.sourceFileLine));
                                }
                                catch (Exception) { }
                            }
                            if (!hasProject || stackFrames.Count == 0) continue;
                            var sb = new StringBuilder();
                            sb.Append("{\"frame_idx\":").Append(frame)
                              .Append(",\"thread\":").Append(ti)
                              .Append(",\"frames\":[").Append(string.Join(",", stackFrames.ToArray()))
                              .Append("],\"metric_context\":{\"gc_alloc_bytes\":").Append(bytes)
                              .Append("},\"data_tier\":\"A2\"}");
                            stacks.Add(sb.ToString());
                            allocs.Add("{\"frame_idx\":" + frame + ",\"bytes\":" + bytes
                                       + ",\"count\":1,\"stack\":[" + string.Join(",", stackFrames.ToArray()) + "]"
                                       + ",\"data_tier\":\"A2\"}");
                            continue;
                        }

                        if (!IsProjectName(name)) continue;
                        long ns = (long)view.GetSampleTimeNs(i);
                        string key = frame + "|" + name;
                        long[] agg;
                        if (!funcs.TryGetValue(key, out agg))
                        {
                            agg = new long[2];
                            funcs[key] = agg;
                        }
                        agg[0]++;
                        agg[1] += ns;
                    }
                }
            }
        }

        var fs = new List<string>();
        // ProfilerRecorder 补小 marker：它们的单次耗时太短，可能进不了 RawFrameDataView。
        foreach (var kv in _rec)
        {
            long ns = 0;
            try { ns = (long)kv.Value.LastValue; } catch (Exception) { }
            if (ns <= 0) continue;
            fs.Add("{\"frame_idx\":" + last + ",\"symbol\":" + Q(kv.Key)
                   + ",\"calls\":0,\"total_ms\":" + (ns / 1e6).ToString("0.000")
                   + ",\"self_ms\":" + (ns / 1e6).ToString("0.000")
                   + ",\"data_tier\":\"A2\"}");
        }
        foreach (var kv in funcs)
        {
            int sep = kv.Key.IndexOf('|');
            string f = kv.Key.Substring(0, sep);
            string sym = kv.Key.Substring(sep + 1);
            long[] a = kv.Value;
            fs.Add("{\"frame_idx\":" + f + ",\"symbol\":" + Q(sym)
                   + ",\"calls\":" + a[0] + ",\"total_ms\":" + (a[1] / 1e6).ToString("0.000")
                   + ",\"self_ms\":" + (a[1] / 1e6).ToString("0.000")
                   + ",\"data_tier\":\"A2\"}");
        }

        var sb2 = new StringBuilder();
        sb2.Append("{\"run_id\":\"evidence-safe\",\"project_id\":\"\",\"scene\":\"\"")
           .Append(",\"protocol_fingerprint\":\"\"")
           .Append(",\"frames\":[").Append(string.Join(",", frames.ConvertAll(x => x.ToString()).ToArray())).Append("]")
           .Append(",\"callstacks\":[").Append(string.Join(",", stacks.ToArray())).Append("]")
           .Append(",\"functions\":[").Append(string.Join(",", fs.ToArray())).Append("]")
           .Append(",\"render_objects\":[]")
           .Append(",\"allocations\":[").Append(string.Join(",", allocs.ToArray())).Append("]")
           .Append(",\"blindspots\":[")
           .Append("{\"code\":\"render_object_unavailable\",\"reason\":\"本采集器未接 FrameDebugger\",\"unlock\":\"FrameDebugger/RenderDoc\"}")
           .Append("],\"raw_refs\":[],\"meta\":{\"source\":\"rawframedataview-safe\",\"threads\":")
           .Append(threads).Append(",\"frame_first\":").Append(first).Append(",\"frame_last\":").Append(last)
           .Append(",\"probe_switches\":{\"enabled_profiler\":").Append(_weEnabledProfiler ? "true" : "false")
           .Append(",\"enabled_driver\":").Append(_weEnabledDriver ? "true" : "false")
           .Append(",\"enabled_deep\":").Append(_weEnabledDeep ? "true" : "false")
           .Append(",\"enabled_alloc_callstacks\":").Append(_weEnabledAlloc ? "true" : "false")
           .Append(",\"restore_on_exit\":true,\"truncated\":")
           .Append(truncated ? "true" : "false")
           .Append(",\"capture_cfg\":{\"threads\":").Append(_maxThreads)
           .Append(",\"frames\":").Append(_framesBack)
           .Append(",\"budget_ms\":").Append(_budgetMs)
           .Append("}}}").Append("}");
        Directory.CreateDirectory(Path.GetDirectoryName(Out));
        File.WriteAllText(Out, sb2.ToString(), new UTF8Encoding(false));
        foreach (var kv in _rec) { try { kv.Value.Dispose(); } catch (Exception) { } }
        _rec.Clear();
        LastLine = "captured frames=" + frames.Count + " threads=" + threads
                   + " functions=" + fs.Count + " callstacks=" + stacks.Count;
    }

    static bool IsProjectName(string name)
    {
        if (string.IsNullOrEmpty(name)) return false;
        return name.Contains("ChromaGame") || name.Contains("BoardModel") ||
               name.Contains("CaseConfig") || name.Contains("DevSimScenarios") ||
               name.Contains("Assembly-CSharp.dll!DevSim") ||
               name.Contains("DevSim.");
    }

    static string FrameJson(string method, string file, int line)
    {
        string src = "null";
        if (!string.IsNullOrEmpty(file) && line > 0)
            src = "{\"file\":" + Q(file) + ",\"start_line\":" + line
                  + ",\"end_line\":" + line + ",\"symbol\":" + Q(method) + "}";
        return "{\"symbol\":" + Q(method) + ",\"source\":" + src + ",\"native\":false}";
    }

    static string Q(string s)
    {
        if (s == null) return "\"\"";
        return "\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"")
                       .Replace("\n", "\\n").Replace("\r", "") + "\"";
    }
}
