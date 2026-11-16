// I26-ext: 单一探针。同一次回调内按 Profiler frame 记录 counters + functions。
// shallow/deep 只是模式，不是另一个探针。
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditorInternal;
using UnityEngine;
using UnityEngine.Profiling;
using Unity.Profiling;

var __r = UpiUnifiedProbe.Main();
return __r;

public static class UpiUnifiedProbe
{
    const string CfgPath = @"D:/unity_profile_inspector/runs/_probe/unified_cfg.json";
    const string Out = @"D:/unity_profile_inspector/runs/_probe/unified_probe.json";
    static readonly (ProfilerCategory cat, string name)[] Specs = {
        (ProfilerCategory.Render, "CPU Main Thread Frame Time"),
        (ProfilerCategory.Memory, "GC Allocated In Frame"),
        (ProfilerCategory.Memory, "Total Used Memory"),
        (ProfilerCategory.Render, "Draw Calls Count"),
        (ProfilerCategory.Render, "SetPass Calls Count"),
        (ProfilerCategory.Render, "Triangles Count"),
        (ProfilerCategory.Render, "Vertices Count"),
    };
    static readonly List<ProfilerRecorder> _rec = new List<ProfilerRecorder>();
    static readonly List<string> _names = new List<string>();
    static readonly List<string> _counterRows = new List<string>();
    static readonly Dictionary<string, long[]> _funcs = new Dictionary<string, long[]>();
    static int _startFrame;
    static int _lastFrame;
    static int _frames = 30;
    static long _budgetMs = 5000;
    static DateTime _startUtc;
    static bool _done;
    static string _mode = "shallow";

    public static string Main()
    {
        LoadCfg();
        Profiler.enabled = true;
        ProfilerDriver.enabled = true;
        ProfilerDriver.profileEditor = true;
        ProfilerDriver.deepProfiling = _mode == "deep";
        Profiler.enableAllocationCallstacks = _mode == "deep";
        foreach (var s in Specs)
        {
            try
            {
                var r = ProfilerRecorder.StartNew(s.cat, s.name, 256);
                if (r.Valid) { _rec.Add(r); _names.Add(s.name); }
            }
            catch (Exception) { }
        }
        _startFrame = ProfilerDriver.lastFrameIndex;
        _lastFrame = _startFrame - 1;
        _startUtc = DateTime.UtcNow;
        _done = false;
        EditorApplication.update += Tick;
        return "installed:" + _mode + ":rec=" + _rec.Count;
    }

    static void LoadCfg()
    {
        try
        {
            if (!File.Exists(CfgPath)) return;
            string j = File.ReadAllText(CfgPath);
            _mode = ReadStr(j, "mode", "shallow");
            _frames = (int)Math.Min(600, Math.Max(1, ReadNum(j, "frames", 30)));
            _budgetMs = (long)Math.Min(30000, Math.Max(500, ReadNum(j, "budget_ms", 5000)));
        }
        catch (Exception) { }
    }

    static void Tick()
    {
        if (_done) return;
        int now = ProfilerDriver.lastFrameIndex;
        if (now != _lastFrame)
        {
            _lastFrame = now;
            _counterRows.Add(CounterRow(now));
        }
        bool enough = now - _startFrame >= _frames;
        bool timeout = (DateTime.UtcNow - _startUtc).TotalMilliseconds > _budgetMs;
        if (!enough && !timeout) return;
        _done = true;
        EditorApplication.update -= Tick;
        try { CaptureFunctions(); Write(); }
        catch (Exception e)
        {
            try { File.WriteAllText(Out + ".err", e.ToString()); } catch (Exception) { }
        }
        finally
        {
            foreach (var r in _rec) { try { r.Dispose(); } catch (Exception) { } }
            _rec.Clear(); _names.Clear();
            ProfilerDriver.deepProfiling = false;
            Profiler.enableAllocationCallstacks = false;
        }
    }

    static string CounterRow(int frame)
    {
        var sb = new StringBuilder();
        sb.Append("{\"frame_idx\":").Append(frame).Append(",\"counters\":{");
        for (int i = 0; i < _rec.Count; i++)
        {
            if (i > 0) sb.Append(',');
            var r = _rec[i];
            long v = r.Count > 0 ? r.GetSample(r.Count - 1).Value : 0;
            sb.Append('"').Append(Esc(_names[i])).Append("\":").Append(v);
        }
        sb.Append("}}");
        return sb.ToString();
    }

    static void CaptureFunctions()
    {
        int last = ProfilerDriver.lastFrameIndex;
        int first = Math.Max(ProfilerDriver.firstFrameIndex, _startFrame);
        for (int frame = first; frame <= last; frame++)
        {
            for (int ti = 0; ti < 8; ti++)
            {
                using (var view = ProfilerDriver.GetRawFrameDataView(frame, ti))
                {
                    if (!view.valid) break;
                    int n = view.sampleCount;
                    for (int i = 0; i < n; i++)
                    {
                        string name = view.GetSampleName(i);
                        if (string.IsNullOrEmpty(name)) continue;
                        if (!(name.Contains("ChromaGame") || name.Contains("BoardModel")
                              || name.Contains("DevSim"))) continue;
                        string key = frame + "|" + name;
                        long[] a;
                        if (!_funcs.TryGetValue(key, out a)) { a = new long[2]; _funcs[key] = a; }
                        a[0]++;
                        a[1] += (long)view.GetSampleTimeNs(i);
                    }
                }
            }
        }
    }

    static void Write()
    {
        var sb = new StringBuilder();
        sb.Append("{\"mode\":\"").Append(Esc(_mode))
          .Append("\",\"start_frame\":").Append(_startFrame)
          .Append(",\"last_frame\":").Append(ProfilerDriver.lastFrameIndex)
          .Append(",\"counters\":[").Append(string.Join(",", _counterRows.ToArray())).Append("]")
          .Append(",\"functions\":[");
        bool first = true;
        foreach (var kv in _funcs)
        {
            if (!first) sb.Append(',');
            first = false;
            int sep = kv.Key.IndexOf('|');
            sb.Append("{\"frame_idx\":").Append(kv.Key.Substring(0, sep))
              .Append(",\"symbol\":").Append(Q(kv.Key.Substring(sep + 1)))
              .Append(",\"calls\":").Append(kv.Value[0])
              .Append(",\"total_ms\":").Append((kv.Value[1] / 1e6).ToString("0.000"))
              .Append(",\"time_semantics\":\"inclusive\"}");
        }
        sb.Append("]}");
        Directory.CreateDirectory(Path.GetDirectoryName(Out));
        File.WriteAllText(Out, sb.ToString(), new UTF8Encoding(false));
    }

    static string ReadStr(string json, string key, string dflt)
    {
        int i = json.IndexOf("\"" + key + "\"");
        if (i < 0) return dflt;
        int c = json.IndexOf(':', i);
        int q1 = json.IndexOf('"', c + 1), q2 = json.IndexOf('"', q1 + 1);
        return (q1 < 0 || q2 < 0) ? dflt : json.Substring(q1 + 1, q2 - q1 - 1);
    }

    static double ReadNum(string json, string key, double dflt)
    {
        int i = json.IndexOf("\"" + key + "\"");
        if (i < 0) return dflt;
        int c = json.IndexOf(':', i), j = c + 1;
        while (j < json.Length && (json[j] == ' ' || json[j] == '\t')) j++;
        int k = j;
        while (k < json.Length && (char.IsDigit(json[k]) || json[k] == '.' || json[k] == '-')) k++;
        double v;
        return double.TryParse(json.Substring(j, k - j),
            System.Globalization.NumberStyles.Float,
            System.Globalization.CultureInfo.InvariantCulture, out v) ? v : dflt;
    }

    static string Q(string s) => "\"" + Esc(s).Replace("\n", "\\n") + "\"";
    static string Esc(string s) => (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");
}
