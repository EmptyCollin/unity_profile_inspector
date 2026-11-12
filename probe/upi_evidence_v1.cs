// I8a 外部证据采集器：从 Profiler RawFrameDataView 读样本/函数/调用栈。
// 通过 `unity command script eval --file` 注入；不改被测工程。
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditorInternal;
using UnityEditor.Profiling;
using UnityEngine.Profiling;

UpiEvidenceV1.Main();
return UpiEvidenceV1.LastLine;

public static class UpiEvidenceV1
{
    const string Out = @"D:/unity_profile_inspector/runs/_probe/evidence_v1.json";
    public static string LastLine = "";
    static int _ticks;
    static bool _done;

    public static string Main()
    {
        Profiler.enabled = true;
        ProfilerDriver.enabled = true;
        try { ProfilerDriver.profileEditor = true; } catch (Exception) { }
        try { ProfilerDriver.deepProfiling = true; } catch (Exception) { }
        try { Profiler.enableAllocationCallstacks = true; } catch (Exception) { }
        _done = false;
        _ticks = 0;
        EditorApplication.update += Tick;
        LastLine = "installed";
        return LastLine;
    }

    static void Tick()
    {
        if (_done) return;
        _ticks++;
        if (_ticks < 60) return;
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
        int last = ProfilerDriver.lastFrameIndex;
        int first = Math.Max(ProfilerDriver.firstFrameIndex, last - 1);
        if (first < 0) first = last;
        try { File.WriteAllText(Out + ".start", "start " + first + "-" + last); } catch (Exception) { }
        var frames = new List<int>();
        var funcs = new Dictionary<string, long[]>();
        var stacks = new List<string>();
        var allocs = new List<string>();
        int threads = 0;
        for (int frame = first; frame <= last; frame++)
        {
            frames.Add(frame);
            for (int ti = 0; ti < 32; ti++)
            {
                using (var view = ProfilerDriver.GetRawFrameDataView(frame, ti))
                {
                    if (!view.valid) break;
                    if (frame == last) threads++;
                    int n = view.sampleCount;
                    for (int i = 0; i < n; i++)
                    {
                        string name = view.GetSampleName(i);
                        if (string.IsNullOrEmpty(name)) continue;

                        // GC.Alloc 的样本名不叫工程符号；先处理调用栈，再过滤函数名。
                        if (name == "GC.Alloc" && stacks.Count < 16)
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
           .Append(threads).Append(",\"frame_first\":").Append(first).Append(",\"frame_last\":").Append(last).Append("}}");
        Directory.CreateDirectory(Path.GetDirectoryName(Out));
        File.WriteAllText(Out, sb2.ToString(), new UTF8Encoding(false));
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
