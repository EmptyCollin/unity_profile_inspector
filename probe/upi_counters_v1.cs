// probe/upi_counters_v1.cs — 通用 ProfilerRecorder 计数采集（I13b）
// 只采 Unity 已注册的通用计数器；不注入被测工程、不按 action 分支。
// 输出：runs/_probe/counters_v1.json
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditorInternal;
using UnityEngine;
using Unity.Profiling;

var __r = UpiCountersV1.Main();
return __r;

public static class UpiCountersV1
{
    const string Out = @"D:/unity_profile_inspector/runs/_probe/counters_v1.json";
    const int Capacity = 128;

    static readonly (ProfilerCategory cat, string name)[] Specs = {
        (ProfilerCategory.Memory, "GC Allocated In Frame"),
        (ProfilerCategory.Memory, "Total Used Memory"),
        (ProfilerCategory.Memory, "GC Used Memory"),
        (ProfilerCategory.Render, "CPU Main Thread Frame Time"),
        (ProfilerCategory.Render, "Draw Calls Count"),
        (ProfilerCategory.Render, "SetPass Calls Count"),
        (ProfilerCategory.Render, "Triangles Count"),
        (ProfilerCategory.Render, "Vertices Count"),
        (ProfilerCategory.Render, "Dynamic Batches Count"),
        (ProfilerCategory.Render, "Instanced Batches Count"),
        (ProfilerCategory.Render, "Static Batches Count"),
    };

    static readonly List<ProfilerRecorder> _rec = new List<ProfilerRecorder>();
    static readonly List<string> _names = new List<string>();
    static int _startFrame;
    static DateTime _startUtc;
    static bool _done;

    public static string Main()
    {
        foreach (var s in Specs)
        {
            try
            {
                var r = ProfilerRecorder.StartNew(s.cat, s.name, Capacity);
                if (r.Valid) { _rec.Add(r); _names.Add(s.name); }
            }
            catch (Exception) { }
        }
        _startFrame = ProfilerDriver.lastFrameIndex;
        _startUtc = DateTime.UtcNow;
        _done = false;
        EditorApplication.update += Tick;
        return "installed:recorders=" + _rec.Count;
    }

    static void Tick()
    {
        if (_done) return;
        int now = ProfilerDriver.lastFrameIndex;
        bool enough = now - _startFrame >= 8;
        bool timeout = (DateTime.UtcNow - _startUtc).TotalSeconds > 4.0;
        if (!enough && !timeout) return;
        _done = true;
        EditorApplication.update -= Tick;
        try { Write(); }
        catch (Exception e)
        {
            try { File.WriteAllText(Out + ".err", e.ToString()); } catch (Exception) { }
        }
        finally
        {
            foreach (var r in _rec) { try { r.Dispose(); } catch (Exception) { } }
            _rec.Clear(); _names.Clear();
        }
    }

    static void Write()
    {
        var sb = new StringBuilder();
        sb.Append("{\"captured_at\":\"").Append(DateTime.UtcNow.ToString("o"))
          .Append("\",\"frame_last\":").Append(ProfilerDriver.lastFrameIndex)
          .Append(",\"counters\":{");
        for (int idx = 0; idx < _rec.Count; idx++)
        {
            var r = _rec[idx];
            if (idx > 0) sb.Append(',');
            string name = _names[idx];
            sb.Append('"').Append(Esc(name)).Append("\":{");
            sb.Append("\"series\":[");
            int n = r.Count;
            for (int k = 0; k < n; k++)
            {
                if (k > 0) sb.Append(',');
                sb.Append(r.GetSample(k).Value);
            }
            sb.Append("]}");
        }
        sb.Append("}}");
        Directory.CreateDirectory(Path.GetDirectoryName(Out));
        File.WriteAllText(Out, sb.ToString(), new UTF8Encoding(false));
    }

    static string Esc(string s) =>
        (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");
}
