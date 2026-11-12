// 窄 Board 证据采集：只扫最近 30 帧，只保留 BoardModel 相关函数/分配。
using System;
using System.Collections.Generic;
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
        int last = ProfilerDriver.lastFrameIndex;
        int first = Math.Max(ProfilerDriver.firstFrameIndex, last - 30);
        var funcs = new Dictionary<string, long[]>();
        var allocs = new List<string>();
        var frames = new List<int>();
        for (int frame = first; frame <= last; frame++)
        {
            frames.Add(frame);
            for (int ti = 0; ti < 8; ti++)
            {
                using (var view = ProfilerDriver.GetRawFrameDataView(frame, ti))
                {
                    if (!view.valid) break;
                    int n = view.sampleCount;
                    for (int i = 0; i < n; i++)
                    {
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
          .Append(",\"blindspots\":[]}");
        try { File.WriteAllText(Out, sb.ToString(), new UTF8Encoding(false)); } catch (Exception e) { }
        LastLine = "frames=" + frames.Count + " funcs=" + fs.Count + " allocs=" + allocs.Count;
        return LastLine;
    }
}
