// 帧号对齐测量：同一帧同时记录 Time.frameCount 与 ProfilerDriver 帧号，
// 看两者差值是否稳定（这是“探针 ring + profiler 窗口拼接”的前提）。
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditorInternal;
using UnityEngine;
using UnityEngine.Profiling;

UpiFrameAlign.Main();
return UpiFrameAlign.LastLine;

public static class UpiFrameAlign
{
    const string Out = @"D:/unity_profile_inspector/runs/_probe/frame_align.json";
    public static string LastLine = "";
    static readonly List<string> _rows = new List<string>();
    static int _lastFrame = -1;
    static bool _weEnabled;
    static bool _prevProfileEditor;

    public static string Main()
    {
        _rows.Clear();
        _lastFrame = -1;
        try { _prevProfileEditor = ProfilerDriver.profileEditor; } catch (Exception) { }
        try
        {
            if (!Profiler.enabled) { Profiler.enabled = true; _weEnabled = true; }
            if (!ProfilerDriver.enabled) ProfilerDriver.enabled = true;
            ProfilerDriver.profileEditor = true;
        }
        catch (Exception) { }
        EditorApplication.update += Tick;
        LastLine = "installed";
        return LastLine;
    }

    static void Tick()
    {
        int f = Time.frameCount;
        if (f == _lastFrame) return;
        _lastFrame = f;
        _rows.Add("{\"game_frame\":" + f
                  + ",\"profiler_last\":" + ProfilerDriver.lastFrameIndex
                  + ",\"profiler_first\":" + ProfilerDriver.firstFrameIndex + "}");
        if (_rows.Count >= 120) Finish();
    }

    static void Finish()
    {
        EditorApplication.update -= Tick;
        var sb = new StringBuilder();
        sb.Append("{\"rows\":[").Append(string.Join(",", _rows.ToArray())).Append("]}");
        try { File.WriteAllText(Out, sb.ToString(), new UTF8Encoding(false)); } catch (Exception) { }
        try { if (_weEnabled) Profiler.enabled = false; } catch (Exception) { }
        try { ProfilerDriver.profileEditor = _prevProfileEditor; } catch (Exception) { }
        try { ProfilerDriver.ClearAllFrames(); } catch (Exception) { }
        LastLine = "rows=" + _rows.Count;
    }
}
