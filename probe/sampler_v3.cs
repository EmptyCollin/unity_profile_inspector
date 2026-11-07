// probe/sampler_v3.cs —— 常驻采样器 v3（M0' 验收版）
// 实测决定的四条实现规则（DESIGN §2.2.1）：
//   1. 回调跨 eval 存活，静态字段不跨存活 ⇒ 幂等不能用静态 bool，也不能用 SessionState
//      （SessionState 跨域重载存活，而回调注册不跨域重载存活 ⇒ 会留下"标记已装、实际没人采样"的死状态）
//      ⇒ 改用**心跳文件新鲜度**作为"是否有采样器活着"的唯一可观测判据
//   2. 先建全部计数器、后注册回调 ⇒ 中途异常不会泄漏幽灵回调
//   3. 每个计数器独立 try/catch ⇒ 名称不存在只跳过该项并记录
//   4. 按 Time.frameCount 门控 ⇒ update 频率(~2100/s)不是帧率
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;
using Unity.Profiling;

UpiSamplerV3.Main();
return UpiSamplerV3.LastLine;

public static class UpiSamplerV3
{
    const string Dir = @"D:/unity_profile_inspector/runs/_probe";
    const string Heartbeat = Dir + "/heartbeat.txt";
    public static string LastLine = "";

    static readonly Dictionary<string, ProfilerRecorder> Rec = new Dictionary<string, ProfilerRecorder>();
    static readonly Dictionary<string, List<long>> Samples = new Dictionary<string, List<long>>();
    static readonly List<string> Skipped = new List<string>();

    static readonly string[][] Targets = {
        new[] { "Render", "Draw Calls" }, new[] { "Render", "SetPass Calls" },
        new[] { "Render", "Batches" },   new[] { "Render", "Frame Time" },
        new[] { "Memory", "Total Used Memory" }, new[] { "Memory", "Mono Used Memory" },
        new[] { "Scripts", "Update Time" },
    };

    static bool _recording, _subscribed;
    static int _windowLen = 300, _written, _lastFrame = -1;

    static ProfilerCategory CatOf(string name)
    {
        // 实测：ProfilerCategory 是 struct，不能用 Enum.Parse；用已验证可编译的静态字段
        switch (name)
        {
            case "Render": return ProfilerCategory.Render;
            case "Memory": return ProfilerCategory.Memory;
            case "Scripts": return ProfilerCategory.Scripts;
            case "Loading": return ProfilerCategory.Loading;
            case "Animation": return ProfilerCategory.Animation;
            case "Physics": return ProfilerCategory.Physics;
            default: throw new ArgumentException("未知类目: " + name);
        }
    }

    static bool Alive()
    {
        try
        {
            if (!File.Exists(Heartbeat)) return false;
            var t = long.Parse(File.ReadAllText(Heartbeat));
            var age = (DateTime.UtcNow - new DateTime(t, DateTimeKind.Utc)).TotalSeconds;
            return age < 5.0;
        }
        catch (Exception) { return false; }
    }

    public static void Main()
    {
        Directory.CreateDirectory(Dir);
        if (!Alive())
        {
            int built = BuildRecorders();
            if (built == 0) { LastLine = "install_failed skipped=[" + string.Join(",", Skipped) + "]"; return; }
            EditorApplication.update -= Tick;   // 清掉本程序集可能重复的注册
            EditorApplication.update += Tick;
            _subscribed = true;
            Beat();
            LastLine = $"installed recorders={built}/{Targets.Length} skipped=[{string.Join(",", Skipped)}]";
            return;
        }
        LastLine = $"alive play={Application.isPlaying} frame={Time.frameCount} recording={_recording} written={_written}";
    }

    static int BuildRecorders()
    {
        Rec.Clear(); Samples.Clear(); Skipped.Clear(); _written = 0; _recording = false;
        foreach (var t in Targets)
        {
            string key = t[0] + "/" + t[1];
            try
            {
                var r = new ProfilerRecorder(CatOf(t[0]), t[1], 3000);
                r.Start();
                Rec[key] = r;
                Samples[key] = new List<long>();
            }
            catch (Exception) { Skipped.Add(key); }
        }
        return Rec.Count;
    }

    static void Tick()
    {
        string cmd = ReadCmd();
        if (cmd.StartsWith("start"))
        {
            if (!_recording)
            {
                var parts = cmd.Split(':');
                if (parts.Length > 1) int.TryParse(parts[1], out _windowLen);
                if (_windowLen <= 0) _windowLen = 300;
                foreach (var l in Samples.Values) l.Clear();
                _written = 0; _lastFrame = Time.frameCount; _recording = true;
                WriteStatus("started");
            }
            ClearCmd();
        }
        else if (cmd == "stop")
        {
            if (_recording) { _recording = false; WriteData("stop"); ClearCmd(); }
        }

        if (_recording && Time.frameCount != _lastFrame)
        {
            _lastFrame = Time.frameCount;
            foreach (var k in Rec.Keys) Samples[k].Add((long)Rec[k].LastValue);
            _written++;
            if (_written % 20 == 0) Beat();
            if (_written >= _windowLen) { _recording = false; WriteData("window_done"); }
        }
        else if (_written == 0 && Time.frameCount % 30 == 0) { Beat(); }
    }

    static void Beat()
    {
        try { SafeWrite(Heartbeat, DateTime.UtcNow.Ticks.ToString()); } catch (Exception) { }
    }

    static string ReadCmd()
    {
        try { return File.ReadAllText(Dir + "/cmd.txt").Trim(); } catch (Exception) { return ""; }
    }
    static void ClearCmd() { try { File.Delete(Dir + "/cmd.txt"); } catch (Exception) { } }

    static void WriteStatus(string state)
    {
        SafeWrite(Dir + "/status.json",
            $"{{\"state\":\"{state}\",\"play\":{(Application.isPlaying ? "true" : "false")},\"frame\":{Time.frameCount},\"written\":{_written},\"skipped\":[{JoinQuoted(Skipped)}]}}");
    }

    static string JoinQuoted(List<string> l)
    {
        var sb = new StringBuilder();
        for (int i = 0; i < l.Count; i++) sb.Append('"').Append(l[i]).Append('"').Append(i < l.Count - 1 ? "," : "");
        return sb.ToString();
    }

    static void WriteData(string why)
    {
        var sb = new StringBuilder();
        sb.Append("{\"why\":\"").Append(why).Append("\",\"play\":")
          .Append(Application.isPlaying ? "true" : "false")
          .Append(",\"frame\":").Append(Time.frameCount)
          .Append(",\"n\":").Append(_written).Append(",\"skipped\":[").Append(JoinQuoted(Skipped))
          .Append("],\"series\":{");
        bool first = true;
        foreach (var k in Samples.Keys)
        {
            if (!first) sb.Append(",");
            first = false;
            sb.Append('"').Append(k).Append("\":[");
            var l = Samples[k];
            for (int i = 0; i < l.Count; i++) sb.Append(l[i]).Append(i < l.Count - 1 ? "," : "");
            sb.Append("]");
        }
        sb.Append("}}");
        SafeWrite(Dir + "/data.json", sb.ToString());
    }

    static void SafeWrite(string path, string content)
    {
        // 原子替换：直写会被轮询方读到半截，且并发写抛 IOException（实测 EBUSY）
        try
        {
            string tmp = path + ".tmp." + Guid.NewGuid().ToString("N").Substring(0, 6);
            File.WriteAllText(tmp, content);
            if (File.Exists(path)) File.Delete(path);
            File.Move(tmp, path);
        }
        catch (Exception) { }
    }
}
