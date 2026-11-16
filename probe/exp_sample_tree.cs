// 可行性实验：导出最近 N 帧的 Profiler 样本树，检查 GC.Alloc 与调用路径。
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditorInternal;
using UnityEditor.Profiling;
using UnityEngine;

var __r = UpiExpSampleTree.Main();
return __r;

public static class UpiExpSampleTree
{
    const string Cfg = @"D:/unity_profile_inspector/runs/_probe/exp_tree_cfg.json";
    const string Out = @"D:/unity_profile_inspector/runs/_probe/exp_tree.json";

    public static string Main()
    {
        int n = 5;
        try
        {
            string j = File.ReadAllText(Cfg);
            int p = j.IndexOf("\"frames\"");
            if (p >= 0)
            {
                int c = j.IndexOf(':', p), k = c + 1;
                while (k < j.Length && !char.IsDigit(j[k])) k++;
                int e = k;
                while (e < j.Length && char.IsDigit(j[e])) e++;
                n = int.Parse(j.Substring(k, e - k));
            }
        }
        catch (Exception) { }
        int last = ProfilerDriver.lastFrameIndex;
        int first = Math.Max(ProfilerDriver.firstFrameIndex, last - Math.Max(1, n) + 1);
        var sb = new StringBuilder();
        sb.Append("{\"last_frame\":").Append(last).Append(",\"samples\":[");
        bool firstRow = true;
        for (int frame = first; frame <= last; frame++)
        {
            using (var view = ProfilerDriver.GetRawFrameDataView(frame, 0))
            {
                if (!view.valid) continue;
                var stack = new List<KeyValuePair<int, string>>();
                int count = view.sampleCount;
                for (int i = 0; i < count; i++)
                {
                    while (stack.Count > 0 && i > stack[stack.Count - 1].Key)
                        stack.RemoveAt(stack.Count - 1);
                    string parent = stack.Count > 0 ? stack[stack.Count - 1].Value : "";
                    string name = view.GetSampleName(i);
                    int child = view.GetSampleChildrenCountRecursive(i);
                    stack.Add(new KeyValuePair<int, string>(i + child, name));
                    if (!(name.Contains("GC.Alloc") || name.Contains("GC.")
                          || name.Contains("ChromaGame") || name.Contains("BoardModel")
                          || name.Contains("Alloc"))) continue;
                    long allocBytes = 0;
                    try
                    {
                        if (name.Contains("GC.Alloc") &&
                            view.GetSampleMetadataCount(i) > 0)
                            allocBytes = view.GetSampleMetadataAsLong(i, 0);
                    }
                    catch (Exception) { }
                    if (!firstRow) sb.Append(',');
                    firstRow = false;
                    var path = new StringBuilder();
                    for (int s = 0; s < stack.Count; s++)
                    {
                        if (s > 0) path.Append(">");
                        path.Append(stack[s].Value);
                    }
                    sb.Append("{\"frame\":").Append(frame)
                      .Append(",\"name\":").Append(Q(name))
                      .Append(",\"parent\":").Append(Q(parent))
                      .Append(",\"path\":").Append(Q(path.ToString()))
                      .Append(",\"depth\":").Append(stack.Count - 1)
                      .Append(",\"children\":").Append(child)
                      .Append(",\"bytes\":").Append(allocBytes)
                      .Append(",\"time_ns\":").Append((long)view.GetSampleTimeNs(i))
                      .Append('}');
                }
            }
        }
        sb.Append("]}");
        Directory.CreateDirectory(Path.GetDirectoryName(Out));
        File.WriteAllText(Out, sb.ToString(), new UTF8Encoding(false));
        return "frames=" + (last - first + 1);
    }

    static string Q(string s) =>
        "\"" + (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"")
            .Replace("\n", "\\n") + "\"";
}
