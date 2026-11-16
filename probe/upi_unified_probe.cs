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
    static string _triggerPath = @"D:/unity_profile_inspector/runs/_probe/unified_trigger.txt";
    static string _stopPath = @"D:/unity_profile_inspector/runs/_probe/unified_stop.txt";
    static string _triggerDir = @"D:/unity_profile_inspector/runs/_probe/triggers";
    static string _windowDir = @"D:/unity_profile_inspector/runs/_probe/windows";
    static string _ackDir = @"D:/unity_profile_inspector/runs/_probe/acks";
    static string _heartbeatPath = @"D:/unity_profile_inspector/runs/_probe/heartbeat.json";
    static string _leasePath = @"D:/unity_profile_inspector/runs/_probe/lease.txt";
    static long _leaseMs = 10000;
    static int _preN = 8;
    static int _postN = 8;
    static bool _triggered;
    static int _triggerFrame = -1;
    static string _triggerTag = "";
    static int _seq;
    static int _lastLeaseCheck;
    static int _hbFrame = -1;

    public static string Main()
    {
        LoadCfg();
        if (_mode == "resident")
        {
            try
            {
                Directory.CreateDirectory(_triggerDir);
                Directory.CreateDirectory(_windowDir);
                Directory.CreateDirectory(_ackDir);
                File.WriteAllText(_heartbeatPath,
                    "{\"frame\":" + ProfilerDriver.lastFrameIndex + "}");
            }
            catch (Exception) { }
        }
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
        int cfgStart = (int)ReadNum(ReadCfg(), "start_frame", -1);
        if (cfgStart >= 0) _startFrame = cfgStart;
        _lastFrame = _startFrame - 1;
        _startUtc = DateTime.UtcNow;
        _done = false;
        EditorApplication.update += Tick;
        return "installed:" + _mode + ":rec=" + _rec.Count;
    }

    static string ReadCfg()
    {
        try { return File.Exists(CfgPath) ? File.ReadAllText(CfgPath) : ""; }
        catch (Exception) { return ""; }
    }

    static void LoadCfg()
    {
        try
        {
            if (!File.Exists(CfgPath)) return;
            string j = File.ReadAllText(CfgPath);
            _mode = ReadStr(j, "mode", "shallow");
            _triggerPath = ReadStr(j, "trigger_file", _triggerPath);
            _stopPath = ReadStr(j, "stop_file", _stopPath);
            _triggerDir = ReadStr(j, "trigger_dir", _triggerDir);
            _windowDir = ReadStr(j, "window_dir", _windowDir);
            _ackDir = ReadStr(j, "ack_dir", _ackDir);
            _heartbeatPath = ReadStr(j, "heartbeat", _heartbeatPath);
            _leasePath = ReadStr(j, "lease", _leasePath);
            _leaseMs = (long)Math.Min(60000, Math.Max(1000, ReadNum(j, "lease_ms", 10000)));
            _preN = (int)Math.Min(600, Math.Max(1, ReadNum(j, "pre_frames", 8)));
            _postN = (int)Math.Min(64, Math.Max(1, ReadNum(j, "post_frames", 8)));
            _frames = (int)Math.Min(600, Math.Max(1, ReadNum(j, "frames", 30)));
            _budgetMs = (long)Math.Min(600000, Math.Max(500, ReadNum(j, "budget_ms", 5000)));
        }
        catch (Exception) { }
    }

    static void Tick()
    {
        if (_done) return;
        int now = ProfilerDriver.lastFrameIndex;
        if (_mode == "resident")
        {
            if (now != _hbFrame)
            {
                _hbFrame = now;
                try { File.WriteAllText(_heartbeatPath,
                    "{\"frame\":" + now + ",\"utc\":\"" +
                    DateTime.UtcNow.ToString("o") + "\"}"); } catch (Exception) { }
            }
            if (now - _lastLeaseCheck >= 30)
            {
                _lastLeaseCheck = now;
                try
                {
                    if (File.Exists(_leasePath) &&
                        (DateTime.UtcNow - File.GetLastWriteTimeUtc(_leasePath)).TotalMilliseconds > _leaseMs)
                    {
                        StopSelf();
                        return;
                    }
                }
                catch (Exception) { }
            }
        }
        if (File.Exists(_stopPath))
        {
            StopSelf();
            return;
        }
        if (now != _lastFrame)
        {
            _lastFrame = now;
            _counterRows.Add(CounterRow(now));
        }
        if (_mode == "resident" && !_triggered)
        {
            string[] pending = new string[0];
            try { pending = Directory.GetFiles(_triggerDir, "*.json"); } catch (Exception) { }
            Array.Sort(pending, StringComparer.Ordinal);
            if (pending.Length > 0)
            {
                string tf = pending[0];
                try { _triggerTag = Path.GetFileNameWithoutExtension(tf); } catch (Exception) { }
                if (string.IsNullOrEmpty(_triggerTag)) _triggerTag = "w" + (++_seq);
                try { File.Delete(tf); } catch (Exception) { }
                _triggered = true;
                _triggerFrame = now;
                _funcs.Clear();
                ProfilerDriver.deepProfiling = true;
                Profiler.enableAllocationCallstacks = true;
                return;
            }
        }
        bool capture = _mode == "resident"
            ? (_triggered && now - _triggerFrame >= _postN)
            : (now - _startFrame >= _frames);
        bool timeout = (DateTime.UtcNow - _startUtc).TotalMilliseconds > _budgetMs;
        if (!capture && !timeout) return;
        if (_mode == "resident" && !_triggered && timeout)
        {
            _done = true;
            EditorApplication.update -= Tick;
            foreach (var r in _rec) { try { r.Dispose(); } catch (Exception) { } }
            _rec.Clear(); _names.Clear();
            return;
        }
        if (_mode != "resident") _done = true;
        try
        {
            int first = _mode == "resident" && _triggered
                ? Math.Max(_startFrame, _triggerFrame - _preN)
                : _startFrame;
            CaptureFunctions(first, now);
            string path = Out;
            if (_mode == "resident" && !string.IsNullOrEmpty(_triggerTag))
            {
                try
                {
                    Directory.CreateDirectory(_windowDir);
                    Directory.CreateDirectory(_ackDir);
                }
                catch (Exception) { }
                path = Path.Combine(_windowDir, _triggerTag + ".json");
            }
            Write(path);
            if (_mode == "resident" && !string.IsNullOrEmpty(_triggerTag))
            {
                try { File.WriteAllText(Path.Combine(_ackDir, _triggerTag + ".json"),
                    "{\"tag\":\"" + Esc(_triggerTag) + "\",\"frame\":" + now + "}"); }
                catch (Exception) { }
            }
        }
        catch (Exception e)
        {
            try { File.WriteAllText(Out + ".err", e.ToString()); } catch (Exception) { }
        }
        if (_mode == "resident")
        {
            _triggered = false;
            _triggerFrame = -1;
            _triggerTag = "";
            ProfilerDriver.deepProfiling = false;
            Profiler.enableAllocationCallstacks = false;
            _lastFrame = now;
            return;
        }
        EditorApplication.update -= Tick;
        foreach (var r in _rec) { try { r.Dispose(); } catch (Exception) { } }
        _rec.Clear(); _names.Clear();
        ProfilerDriver.deepProfiling = false;
        Profiler.enableAllocationCallstacks = false;
    }

    static void StopSelf()
    {
        _done = true;
        EditorApplication.update -= Tick;
        foreach (var r in _rec) { try { r.Dispose(); } catch (Exception) { } }
        _rec.Clear(); _names.Clear();
        ProfilerDriver.deepProfiling = false;
        Profiler.enableAllocationCallstacks = false;
        try { File.Delete(_heartbeatPath); } catch (Exception) { }
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

    static void CaptureFunctions(int firstArg, int lastArg)
    {
        int last = lastArg;
        int first = Math.Max(ProfilerDriver.firstFrameIndex, firstArg);
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

    static void Write(string path)
    {
        var sb = new StringBuilder();
        sb.Append("{\"mode\":\"").Append(Esc(_mode))
          .Append("\",\"start_frame\":").Append(_startFrame)
          .Append(",\"trigger_frame\":").Append(_triggerFrame)
          .Append(",\"pre_frames\":").Append(_preN)
          .Append(",\"post_frames\":").Append(_postN)
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
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        File.WriteAllText(path, sb.ToString(), new UTF8Encoding(false));
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
