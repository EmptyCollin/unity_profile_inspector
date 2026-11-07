// probe/sampler_v2.cs —— 常驻采样器 v2（DESIGN §2.1 A 档 / I9 实测修正版）
//
// 三条实测结论决定了这里的写法：
//   1. EditorApplication.update 回调 **跨 eval 存活**（副作用持续）
//   2. eval 内静态字段 **不跨 eval 存活**（新 eval = 新程序集，读不到旧状态）
//   3. 因此 **无法从新 eval 卸载旧回调**，幂等标志必须放 SessionState，不能用静态 bool
//   => 控制走文件（Python 写 cmd 文件，采样器在 update 里读），数据走文件（采样器写，Python 轮询）
//
// 采集必须在 Play Mode 下：-batchmode -nographics 的 idle 编辑器不渲染，
// DrawCalls/Frame Time 恒为 0，且 update tick 频率 ≠ 帧率。
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;
using Unity.Profiling;

UpiSamplerV2.Main();
return UpiSamplerV2.LastLine;

public static class UpiSamplerV2
{
    const string Dir = @"D:/unity_profile_inspector/runs/_probe";
    const string SessionKey = "upi.sampler.v2.installed";
    public static string LastLine = "";

    static readonly Dictionary<string, ProfilerRecorder> Rec = new Dictionary<string, ProfilerRecorder>();
    static readonly Dictionary<string, List<long>> Samples = new Dictionary<string, List<long>>();
    static readonly string[][] Targets = new[] {
        new[] { "Render", "Draw Calls" }, new[] { "Render", "SetPass Calls" },
        new[] { "Render", "Batches" }, new[] { "Render", "Frame Time" },
        new[] { "Memory", "Total Used Memory" }, new[] { "Memory", "Mono Used Memory" },
        new[] { "Scripts", "Update Time" },
    };

    static int _windowStart = -1, _windowLen, _writtenFrames;
    static bool _recording;
    static int _lastFrameCount = -1;   // 实测：EditorApplication.update 频率(~2100/s)≠帧率，须按 Time.frameCount 去重

    public static void Main()
    {
        Directory.CreateDirectory(Dir);
        if (!SessionState.GetBool(SessionKey, false))
        {
            Install();
            SessionState.SetBool(SessionKey, true);
            LastLine = $"installed play={Application.isPlaying}";
            return;
        }
        Tick();
        LastLine = $"alive play={Application.isPlaying} frame={Time.frameCount} recording={_recording} written={_writtenFrames}";
    }

    static void Install()
    {
        EditorApplication.update += Tick;
        foreach (var t in Targets)
        {
            var cat = (ProfilerCategory)Enum.Parse(typeof(ProfilerCategory), t[0]);
            var r = new ProfilerRecorder(cat, t[1], 3000);
            Rec[t[0] + "/" + t[1]] = r;
            Samples[t[0] + "/" + t[1]] = new List<long>();
        }
        File.WriteAllText($"{Dir}/status.json", "{\"state\":\"installed\"}");
    }

    static void Tick()
    {
        string cmd = ReadCmd();
        if (cmd.StartsWith("start")) { Start(cmd); WriteStatus("started"); }
        else if (cmd == "stop") { Stop(); WriteStatus("stopped"); ClearCmd(); }
        else if (cmd == "dump") { WriteData("dump"); ClearCmd(); }

        if (_recording && Time.frameCount != _lastFrameCount)
        {
            _lastFrameCount = Time.frameCount;
            foreach (var k in Rec.Keys)
                Samples[k].Add((long)Rec[k].LastValue);
            _writtenFrames++;
            if (_writtenFrames >= _windowLen) { Stop(); WriteStatus("window_done"); }
        }
    }

    static void Start(string arg)
    {
        foreach (var r in Rec.Values) r.Start();
        foreach (var l in Samples.Values) l.Clear();
        var parts = arg.Split(':');
        _windowLen = parts.Length > 1 ? int.Parse(parts[1]) : 600;
        _writtenFrames = 0; _recording = true;
    }

    static void Stop()
    {
        _recording = false;
        foreach (var r in Rec.Values) { try { r.Stop(); } catch (Exception) { } }
        WriteData("stop");
    }

    static string ReadCmd()
    {
        try { return File.ReadAllText($"{Dir}/cmd.txt").Trim(); }
        catch (Exception) { return ""; }
    }
    static void ClearCmd() { try { File.Delete($"{Dir}/cmd.txt"); } catch (Exception) { } }

    static void WriteStatus(string state)
    {
        SafeWrite($"{Dir}/status.json",
            $"{{\"state\":\"{state}\",\"play\":{(Application.isPlaying ? "true" : "false")},\"frame\":{Time.frameCount},\"written\":{_writtenFrames}}}");
    }

    static void WriteData(string why)
    {
        var sb = new StringBuilder();
        sb.Append("{").Append($"\"why\":\"{why}\",\"play\":{(Application.isPlaying ? "true" : "false")},\"frame\":{Time.frameCount},\"n\":{_writtenFrames},\"series\":{{");
        bool first = true;
        foreach (var k in Samples.Keys)
        {
            if (!first) sb.Append(",");
            first = false;
            sb.Append("\"").Append(k).Append("\":[");
            var l = Samples[k];
            for (int i = 0; i < l.Count; i++) sb.Append(l[i]).Append(i < l.Count - 1 ? "," : "");
            sb.Append("]");
        }
        sb.Append("}}");
        SafeWrite($"{Dir}/data.json", sb.ToString());
    }

    // 单写者语义：先写临时再替换，避免 Python 读到半截 JSON（实测直接覆盖会 EBUSY）
    static void SafeWrite(string path, string content)
    {
        try
        {
            string tmp = path + ".tmp";
            File.WriteAllText(tmp, content);
            if (File.Exists(path)) File.Delete(path);
            File.Move(tmp, path);
        }
        catch (Exception) { }
    }
}
