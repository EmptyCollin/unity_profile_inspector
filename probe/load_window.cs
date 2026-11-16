using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditorInternal;
using UnityEditor.Profiling;

var cfg = @"D:/unity_profile_inspector/runs/_probe/profile_window_cfg.txt";
var path = File.ReadAllText(cfg).Trim();
ProfilerDriver.LoadProfile(path, false);
int first = ProfilerDriver.firstFrameIndex;
int last = ProfilerDriver.lastFrameIndex;
var sb = new StringBuilder();
sb.Append("{\"first\":").Append(first).Append(",\"last\":").Append(last).Append(",\"samples\":[");
bool firstRow = true;
for (int frame = first; frame <= last; frame++)
{
    using (var view = ProfilerDriver.GetRawFrameDataView(frame, 0))
    {
        if (!view.valid) continue;
        var stack = new List<KeyValuePair<int, string>>();
        for (int i = 0; i < view.sampleCount; i++)
        {
            while (stack.Count > 0 && i > stack[stack.Count - 1].Key) stack.RemoveAt(stack.Count - 1);
            string name = view.GetSampleName(i);
            int child = view.GetSampleChildrenCountRecursive(i);
            var ps = new StringBuilder();
            for (int s = 0; s < stack.Count; s++) { if (s > 0) ps.Append('>'); ps.Append(stack[s].Value); }
            string p = ps.ToString();
            stack.Add(new KeyValuePair<int, string>(i + child, name));
            bool game = p.Contains("ChromaGame") || p.Contains("BoardModel") || p.Contains("DevSim");
            if (!game || !(name.Contains("GC.Alloc") || name.Contains("ChromaGame") || name.Contains("BoardModel"))) continue;
            long bytes = 0;
            try { if (name.Contains("GC.Alloc") && view.GetSampleMetadataCount(i) > 0) bytes = view.GetSampleMetadataAsLong(i, 0); } catch (Exception) { }
            if (!firstRow) sb.Append(',');
            firstRow = false;
            sb.Append("{\"frame\":").Append(frame).Append(",\"name\":").Append(Q(name))
              .Append(",\"path\":").Append(Q(p)).Append(",\"bytes\":").Append(bytes)
              .Append(",\"time_ns\":").Append((long)view.GetSampleTimeNs(i)).Append('}');
        }
    }
}
sb.Append("]}");
File.WriteAllText(@"D:/unity_profile_inspector/runs/_probe/loaded_window.json", sb.ToString());
return "ok";

static string Q(string s) => "\"" + (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
