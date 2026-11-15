// 通用 marker 取证：不自触发动作，等 2 帧后扫最近 N 帧，只保留 ChromaGame/BoardModel 样本。
// 用于商店等“由外部动作触发”的窗口（board 窗口用 upi_board_evidence.cs，自带 resolve）。
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditorInternal;
using UnityEditor.Profiling;

UpiMarkerEvidence.Main();
return UpiMarkerEvidence.LastLine;

public static class UpiMarkerEvidence
{
    const string Out = @"D:/unity_profile_inspector/runs/_probe/marker_evidence.json";
    const string CfgPath = @"D:/unity_profile_inspector/runs/_probe/capture_cfg.json";
    public static string LastLine = "";
    static int _startFrame;
    static DateTime _startUtc;
    static bool _done;
    static int _maxThreads = 8;
    static int _framesBack = 8;
    static long _budgetMs = 5000;
    static string _action = "none";
    static int _windowStart = -1;

    public static string Main()
    {
        LoadCfg();
        // shop 动作：在探针内部触发，避免 eval 往返把 OpenShop 那一帧挤出窗口。
        if (_action == "shop")
        {
            var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();
            if (g != null)
            {
                var m = g.GetType().GetMethod("OpenShop",
                    System.Reflection.BindingFlags.NonPublic
                    | System.Reflection.BindingFlags.Instance);
                if (m != null) m.Invoke(g, null);
            }
        }
        _startFrame = ProfilerDriver.lastFrameIndex;
        _startUtc = DateTime.UtcNow;
        _done = false;
        EditorApplication.update += Tick;
        LastLine = "installed";
        return LastLine;
    }

    static void LoadCfg()
    {
        try
        {
            if (!File.Exists(CfgPath)) return;
            string j = File.ReadAllText(CfgPath);
            _maxThreads = (int)Math.Min(64, Math.Max(1, ReadNum(j, "board_threads", 8)));
            _framesBack = (int)Math.Min(600, Math.Max(1, ReadNum(j, "marker_frames",
                ReadNum(j, "board_frames", 8))));
            double budget = ReadNum(j, "marker_budget_ms", 5000);
            _budgetMs = (long)Math.Min(30000.0, Math.Max(500.0, budget));
            _action = ReadStr(j, "marker_action", "none");
            _windowStart = (int)ReadNum(j, "marker_start_frame", -1);
        }
        catch (Exception) { }
    }

    static string ReadStr(string json, string key, string dflt)
    {
        int i = json.IndexOf("\"" + key + "\"");
        if (i < 0) return dflt;
        int c = json.IndexOf(':', i);
        if (c < 0) return dflt;
        int q1 = json.IndexOf('"', c + 1);
        if (q1 < 0) return dflt;
        int q2 = json.IndexOf('"', q1 + 1);
        if (q2 < 0) return dflt;
        return json.Substring(q1 + 1, q2 - q1 - 1);
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
        if (k > j && double.TryParse(json.Substring(j, k - j),
                System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out v)) return v;
        return dflt;
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
        int first = Math.Max(ProfilerDriver.firstFrameIndex, last - (_framesBack - 1));
        if (_windowStart >= 0 && _windowStart > first)
            first = Math.Min(last, _windowStart);
        var funcs = new Dictionary<string, long[]>();
        var frames = new List<int>();
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
                    int n = view.sampleCount;
                    for (int i = 0; i < n; i++)
                    {
                        if (budget.ElapsedMilliseconds > _budgetMs) { truncated = true; break; }
                        string name = view.GetSampleName(i);
                        if (string.IsNullOrEmpty(name)) continue;
                        if (!(name.Contains("ChromaGame") || name.Contains("BoardModel")
                              || name.Contains("DevSim"))) continue;
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
        sb.Append("{\"run_id\":\"marker\",\"project_id\":\"\",\"scene\":\"\"")
          .Append(",\"protocol_fingerprint\":\"\"")
          .Append(",\"frames\":[").Append(string.Join(",", frames.ConvertAll(x => x.ToString()).ToArray())).Append("]")
          .Append(",\"callstacks\":[]")
          .Append(",\"functions\":[").Append(string.Join(",", fs.ToArray())).Append("]")
          .Append(",\"render_objects\":[]")
          .Append(",\"allocations\":[]")
          .Append(",\"blindspots\":[],\"truncated\":").Append(truncated ? "true" : "false").Append("}");
        try { File.WriteAllText(Out, sb.ToString(), new UTF8Encoding(false)); } catch (Exception) { }
        LastLine = "frames=" + frames.Count + " funcs=" + fs.Count;
        return;
    }

    static string Q(string s)
    {
        if (s == null) return "\"\"";
        return "\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"")
            .Replace("\n", "\\n").Replace("\r", "") + "\"";
    }
}
