// 窄 Board 证据采集：自己触发 resolve，等 2 帧后扫最近 8 帧，只保留 BoardModel 函数/分配。
// 由外部先打开 profiler（深档），本件不开关 profiler，采集结束只写文件。
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditorInternal;
using UnityEditor.Profiling;

UpiBoardEvidence.Main();
return UpiBoardEvidence.LastLine;

public static class UpiBoardEvidence
{
    const string Out = @"D:/unity_profile_inspector/runs/_probe/board_evidence.json";
    public static string LastLine = "";
    static int _startFrame;
    static int _resolveFrame;
    static DateTime _startUtc;
    static bool _done;

    static string Q(string s)
    {
        if (s == null) return "\"\"";
        return "\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"")
            .Replace("\n", "\\n").Replace("\r", "") + "\"";
    }

    static string FrameJson(string method, string file, int line)
    {
        return "{\"symbol\":" + Q(method) + ",\"source\":{\"file\":" + Q(file ?? "") +
            ",\"start_line\":" + line + ",\"end_line\":" + line +
            ",\"symbol\":" + Q(method) + "}}";
    }

    public static string Main()
    {
        // 在本件内部触发 resolve：若由外部 eval 触发，再 eval 本件时已经过去 1~2 秒，
        // resolve 帧会落在扫描窗口之外（实测 last=66 而 resolve 在 45）。
        var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();
        if (g == null) { LastLine = "no-game"; return LastLine; }
        // 域重载后 cfg/rng 会被清空，不先 DebugLoadConfig 就 BuildBoard 会在
        // BoardModel.Build 里 NPE（rng==null），整条板子取证就静默失败。
        g.DebugLoadConfig("{\"seed\":20261112,\"autoplay\":false,\"round_seconds\":30}");
        int[] colors = new int[36];
        for (int i = 0; i < 36; i++)
            colors[i] = i < 3 ? 0 : ((i / 6 + i % 6) % 5);
        int[] layers = new int[36];
        for (int i = 0; i < 36; i++) layers[i] = 1;
        g.DebugSetBoard(6, colors, layers);
        g.DebugResolveStep();
        _resolveFrame = ProfilerDriver.lastFrameIndex;

        _startFrame = ProfilerDriver.lastFrameIndex;
        _startUtc = DateTime.UtcNow;
        _done = false;
        EditorApplication.update += Tick;
        LastLine = "installed";
        return LastLine;
    }

    static void Tick()
    {
        if (_done) return;
        bool enough = ProfilerDriver.lastFrameIndex - _startFrame >= 2;
        bool timeout = (DateTime.UtcNow - _startUtc).TotalSeconds > 3.0;
        if (!enough && !timeout) return;
        _done = true;
        EditorApplication.update -= Tick;
        try { Capture(); }
        catch (Exception e)
        {
            LastLine = "capture-error: " + e;
            try { File.WriteAllText(Out + ".err", e.ToString()); } catch (Exception) { }
        }
    }

    static void Capture()
    {
        var budget = Stopwatch.StartNew();
        bool truncated = false;
        int last = ProfilerDriver.lastFrameIndex;
        int firstAvailable = ProfilerDriver.firstFrameIndex;
        // 先扫 resolve 之后那一帧，再扫 resolve 帧本身：深档下每帧扫描要数秒，
        // 按“最新优先”会把真正含 marker 的帧挤掉（实测只扫到 113/112）。
        var order = new List<int>();
        foreach (int f in new int[] { _resolveFrame + 1, _resolveFrame, _resolveFrame + 2, _resolveFrame - 1 })
            if (f >= firstAvailable && f <= last) order.Add(f);
        var funcs = new Dictionary<string, long[]>();
        var allocs = new List<string>();
        var frames = new List<int>();
        foreach (int frame in order)
        {
            if (budget.ElapsedMilliseconds > 5000) { truncated = true; break; }
            frames.Add(frame);
            for (int ti = 0; ti < 8; ti++)
            {
                if (budget.ElapsedMilliseconds > 5000) { truncated = true; break; }
                using (var view = ProfilerDriver.GetRawFrameDataView(frame, ti))
                {
                    if (!view.valid) break;
                    int n = view.sampleCount;
                    for (int i = 0; i < n; i++)
                    {
                        if (budget.ElapsedMilliseconds > 5000) { truncated = true; break; }
                        string name = view.GetSampleName(i);
                        if (name == null) continue;
                        if (name == "GC.Alloc" && allocs.Count < 16)
                        {
                            var ptrs = new List<ulong>();
                            try { view.GetSampleCallstack(i, ptrs); } catch (Exception) { }
                            var sf = new List<string>();
                            bool board = false;
                            foreach (var addr in ptrs)
                            {
                                try
                                {
                                    var mi = view.ResolveMethodInfo(addr);
                                    if (mi.methodName == null) continue;
                                    if (mi.methodName.Contains("BoardModel")) board = true;
                                    sf.Add(FrameJson(mi.methodName, mi.sourceFileName,
                                                     (int)mi.sourceFileLine));
                                }
                                catch (Exception) { }
                            }
                            if (!board || sf.Count == 0) continue;
                            long bytes = 0;
                            try { bytes = (long)view.GetSampleMetadataAsLong(i, 0); } catch (Exception) { }
                            allocs.Add("{\"frame_idx\":" + frame + ",\"bytes\":" + bytes +
                                ",\"count\":1,\"stack\":[" + string.Join(",", sf.ToArray()) +
                                "],\"data_tier\":\"A2\"}");
                            continue;
                        }
                        if (!name.Contains("BoardModel")) continue;
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
                ",\"total_ms\":" + (kv.Value[1] / 1e6).ToString("0.000") +
                ",\"self_ms\":" + (kv.Value[1] / 1e6).ToString("0.000") +
                ",\"data_tier\":\"A2\"}");
        }
        var sb = new StringBuilder();
        sb.Append("{\"run_id\":\"board\",\"project_id\":\"\",\"scene\":\"\"")
          .Append(",\"protocol_fingerprint\":\"\"")
          .Append(",\"frames\":[").Append(string.Join(",", frames.ConvertAll(x => x.ToString()).ToArray())).Append("]")
          .Append(",\"callstacks\":[]")
          .Append(",\"functions\":[").Append(string.Join(",", fs.ToArray())).Append("]")
          .Append(",\"render_objects\":[]")
          .Append(",\"allocations\":[").Append(string.Join(",", allocs.ToArray())).Append("]")
          .Append(",\"blindspots\":[],\"truncated\":").Append(truncated ? "true" : "false").Append("}");
        try { File.WriteAllText(Out, sb.ToString(), new UTF8Encoding(false)); } catch (Exception) { }
        LastLine = "frames=" + frames.Count + " funcs=" + fs.Count + " allocs=" + allocs.Count;
        return;
    }
}
