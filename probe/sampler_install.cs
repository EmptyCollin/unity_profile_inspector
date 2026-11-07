// probe/sampler_install.cs —— I9 假设验证：常驻回调能否跨 eval 存活并累积帧数据
// 调用：unity command "script eval_file" --file probe/sampler_install.cs
// 实测修正：CallbackFunction 无 IsRegistered；ProfilerRecorder 是 struct（不可赋 null）
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;
using Unity.Profiling;

UpiSamplerV1.Install();
return UpiSamplerV1.Status();

public static class UpiSamplerV1
{
    const string Out = @"D:/unity_profile_inspector/runs/_sampler_probe.json";
    public static int Frames;                                   // 静态字段跨 eval 是否存活 = I9 的验证对象
    public static bool RenderMsOk;
    public static readonly List<long> DrawCalls = new List<long>();
    public static readonly List<double> FrameMs = new List<double>();
    static ProfilerRecorder _dc;
    static ProfilerRecorder _renderMs;
    static double _lastRealtime;
    static bool _installed;

    public static void Install()
    {
        // 幂等守卫：_installed 若在下次 eval 中仍为 true，即证明静态状态跨 eval 存活（I9 的验证对象）
        if (_installed) return;
        EditorApplication.update -= OnUpdate;
        _dc = new ProfilerRecorder(ProfilerCategory.Render, "Draw Calls", 2000);
        _renderMs = new ProfilerRecorder(ProfilerCategory.Render, "Frame Time", 2000);
        _dc.Start();
        RenderMsOk = TryStart(ref _renderMs);
        Frames = 0; DrawCalls.Clear(); FrameMs.Clear();
        _lastRealtime = Time.realtimeSinceStartupAsDouble;
        EditorApplication.update += OnUpdate;
        _installed = true;
    }

    static bool TryStart(ref ProfilerRecorder r)
    {
        try { r.Start(); return true; } catch (Exception) { return false; }
    }

    static void OnUpdate()
    {
        Frames++;
        DrawCalls.Add((long)_dc.LastValue);
        FrameMs.Add(RenderMsOk ? (double)_renderMs.LastValue
                               : (Time.realtimeSinceStartupAsDouble - _lastRealtime) * 1000.0);
        _lastRealtime = Time.realtimeSinceStartupAsDouble;
        if (Frames % 20 == 0) Dump("ticking");
    }

    public static string Status()
    {
        Dump("status");
        return $"frames={Frames} n={DrawCalls.Count} renderMsCounter={RenderMsOk} isPlaying={Application.isPlaying} file={Out}";
    }

    static void Dump(string state)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Out));
        long mx = -1; foreach (var v in DrawCalls) if (v > mx) mx = v;
        double fmx = -1; foreach (var v in FrameMs) if (v > fmx) fmx = v;
        int nShown = Math.Min(DrawCalls.Count, 50);
        var sb = new StringBuilder("{");
        sb.Append($"\"state\":\"{state}\",\"frames\":{Frames},");
        sb.Append($"\"isPlaying\":{(Application.isPlaying ? "true" : "false")},");
        sb.Append($"\"renderMsCounter\":{(RenderMsOk ? "true" : "false")},");
        sb.Append($"\"n\":{DrawCalls.Count},");
        sb.Append($"\"last\":{(DrawCalls.Count > 0 ? DrawCalls[DrawCalls.Count - 1] : -1)},");
        sb.Append($"\"max\":{mx},\"frameMsMax\":{fmx:F3},\"raw\":[");
        for (int i = 0; i < nShown; i++)
            sb.Append(DrawCalls[i]).Append(i < nShown - 1 ? "," : "");
        sb.Append("]}");
        File.WriteAllText(Out, sb.ToString());
    }
}
