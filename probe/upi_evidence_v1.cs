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
        try { File.WriteAllText(Out + ".start", "start " + ProfilerDriver.lastFrameIndex); } catch (Exception) { }
        int frame = ProfilerDriver.lastFrameIndex;
        var funcs = new Dictionary<string, long[]>();
        var stacks = new List<string>();
        var allocs = new List<string>();
        int threads = 0;
        for (int ti = 0; ti < 32; ti++)
        {
            using (var view = ProfilerDriver.GetRawFrameDataView(frame, ti))
            {
                if (!view.valid) break;
                threads++;
                int n = view.sampleCount;
                for (int i = 0; i < n; i++)
                {
                    string name = view.GetSampleName(i);
                    if (string.IsNullOrEmpty(name)) continue;
                    long ns = (long)view.GetSampleTimeNs(i);
                    long[] agg;
                    if (!funcs.TryGetValue(name, out agg))
                    {
                        agg = new long[2];
                        funcs[name] = agg;
                    }
                    agg[0]++;
                    agg[1] += ns;

                    if (name == "GC.Alloc" && stacks.Count < 64)
                    {
                        var ptrs = new List<ulong>();
                        try { view.GetSampleCallstack(i, ptrs); } catch (Exception) { }
                        if (ptrs.Count == 0) continue;
                        long bytes = 0;
                        try { bytes = (long)view.GetSampleMetadataAsLong(i, 0); } catch (Exception) { }
                        var frames = new List<string>();
                        foreach (var addr in ptrs)
                        {
                            try
                            {
                                var mi = view.ResolveMethodInfo(addr);
                                if (mi.methodName == null) continue;
                                frames.Add(FrameJson(mi.methodName, mi.sourceFileName, (int)mi.sourceFileLine));
                            }
                            catch (Exception) { }
                        }
                        if (frames.Count == 0) continue;
                        var sb = new StringBuilder();
                        sb.Append("{\"frame_idx\":").Append(frame)
                          .Append(",\"thread\":").Append(ti)
                          .Append(",\"frames\":[").Append(string.Join(",", frames.ToArray()))
                          .Append("],\"metric_context\":{\"gc_alloc_bytes\":").Append(bytes)
                          .Append("},\"data_tier\":\"A2\"}");
                        stacks.Add(sb.ToString());
                        allocs.Add("{\"frame_idx\":" + frame + ",\"bytes\":" + bytes
                                   + ",\"count\":1,\"stack\":[" + string.Join(",", frames.ToArray()) + "]"
                                   + ",\"data_tier\":\"A2\"}");
                    }
                }
            }
        }

        var fs = new List<string>();
        foreach (var kv in funcs)
        {
            long[] a = kv.Value;
            fs.Add("{\"frame_idx\":" + frame + ",\"symbol\":" + Q(kv.Key)
                   + ",\"calls\":" + a[0] + ",\"total_ms\":" + (a[1] / 1e6).ToString("0.000")
                   + ",\"self_ms\":" + (a[1] / 1e6).ToString("0.000")
                   + ",\"data_tier\":\"A2\"}");
        }

        var sb2 = new StringBuilder();
        sb2.Append("{\"run_id\":\"evidence-v1\",\"project_id\":\"\",\"scene\":\"\"")
           .Append(",\"protocol_fingerprint\":\"\"")
           .Append(",\"frames\":[").Append(frame).Append("]")
           .Append(",\"callstacks\":[").Append(string.Join(",", stacks.ToArray())).Append("]")
           .Append(",\"functions\":[").Append(string.Join(",", fs.ToArray())).Append("]")
           .Append(",\"render_objects\":[]")
           .Append(",\"allocations\":[").Append(string.Join(",", allocs.ToArray())).Append("]")
           .Append(",\"blindspots\":[")
           .Append("{\"code\":\"render_object_unavailable\",\"reason\":\"本采集器未接 FrameDebugger\",\"unlock\":\"FrameDebugger/RenderDoc\"}")
           .Append("],\"raw_refs\":[],\"meta\":{\"source\":\"rawframedataview\",\"threads\":")
           .Append(threads).Append("}}");
        Directory.CreateDirectory(Path.GetDirectoryName(Out));
        File.WriteAllText(Out, sb2.ToString(), new UTF8Encoding(false));
        LastLine = "captured frame=" + frame + " threads=" + threads
                   + " functions=" + fs.Count + " callstacks=" + stacks.Count;
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
