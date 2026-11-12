// probe/sampler_v3.cs —— 常驻采样器 v3.1（#27 计数器名修正版）
// 实测决定的实现规则（DESIGN §2.2.1）：
//   1. 回调跨 eval 存活，静态字段不跨存活 ⇒ 幂等不能用静态 bool，也不能用 SessionState
//      （SessionState 跨域重载存活，而回调注册不跨域重载存活 ⇒ 会留下"标记已装、实际没人采样"的死状态）
//      ⇒ 改用**心跳文件新鲜度**作为"是否有采样器活着"的唯一可观测判据
//   2. 先建全部计数器、后注册回调 ⇒ 中途异常不会泄漏幽灵回调
//   3. 按 Time.frameCount 门控 ⇒ update 频率(~2100/s)不是帧率
// v3.1 变更（2026-11-11，#27，全部有实测证据 runs/_probe/counter_cat_full.txt 5298 行 +
// exp5 两侧对表 runs/_probe/exp5_probe.jsonl/exp5_poll.jsonl）：
//   A. 渲染计数真名带 " Count" 后缀（"Draw Calls"/"Batches" 等短名**不在句柄表里**）。
//      `new ProfilerRecorder(类目, 短名)` 不抛异常、构造"成功"、LastValue 恒 0 ⇒
//      静默假数据（v3 的 7 项里渲染 4 项全中此招，skipped=[] 却读数全 0）。
//   B. 同名句柄有 13 个（UI Toolkit 类目先注册遮蔽）⇒ 必须 **名字==目标 且
//      类目==Render** 双条件遍历 GetAvailable 定位（exp5 R3 写法）。
//   C. 句柄表证实**不存在**的目标，v3 在装但永远读 0，本版移除并记入盲区：
//      "Mono Used Memory"（全表无任何 Mono*Used* 句柄 ⇒ 该口径唯一源=轮询
//      get_performance 的 memory.monoUsedBytes）；"Update Time"（Scripts 类目
//      只有逐方法采样句柄，无聚合 ⇒ 帧时间口径走 A4/frameTiming）。
//      ⚠️ 这直接改写 G16/G20 的覆盖面表述，条文改判在 #29 落文。
//   D. **汇总 "Batches Count" 在 Render 类目不存在**，只有三个分项句柄
//      （Dynamic/Instanced/Static Batches Count）⇒ 注入侧 batches 与轮询侧
//      render.batches 不是同名口径；分项已装，但非零对表需真实合批场景
//      （夹具 SRP 默认不合批 ⇒ 分项恒 0 属合法值，等价性验证记未决，G20 续列）。
//   E. 定位不到 = **拒绝安装并如实报 skipped**（fail-loud），不再"装了但读 0"。
//   F. 统一 StartImmediately 构造（UUM-127894 绕法，exp5 实测 Default 构造有
//      2/60 冷启动零值帧）。
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;
using Unity.Profiling;
using Unity.Profiling.LowLevel.Unsafe;

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
    static readonly Dictionary<string, int> Shadow = new Dictionary<string, int>();  // 同名他类目遮蔽计数（诊断用）

    // {类目, 句柄注册真名, series 键} —— 全部经 counter_cat_full.txt 逐行核实存在。
    // 时间类 LastValue 单位 ns（消费方换算 ms 再与 frameTiming 对表）。
    static readonly string[][] Targets = {
        new[] { "Render", "Draw Calls Count",           "Render/DrawCalls" },
        new[] { "Render", "SetPass Calls Count",        "Render/SetPassCalls" },
        new[] { "Render", "Triangles Count",            "Render/Triangles" },
        new[] { "Render", "Vertices Count",             "Render/Vertices" },
        new[] { "Render", "Indices Count",              "Render/Indices" },
        new[] { "Render", "Dynamic Batches Count",      "Render/DynBatches" },
        new[] { "Render", "Instanced Batches Count",    "Render/InstBatches" },
        new[] { "Render", "Static Batches Count",       "Render/StaticBatches" },
        new[] { "Render", "CPU Main Thread Frame Time", "Render/CpuMainThreadFrameNs" },
        new[] { "Render", "CPU Total Frame Time",       "Render/CpuTotalFrameNs" },
        new[] { "Render", "GPU Frame Time",             "Render/GpuFrameNs" },
        new[] { "Memory", "Total Used Memory",          "Memory/TotalUsedBytes" },
        new[] { "Memory", "GC Allocated In Frame",      "Memory/GcAllocInFrameBytes" },
    };

    static readonly string[] WantNames = {
        "Draw Calls Count", "SetPass Calls Count", "Triangles Count",
        "Vertices Count", "Indices Count", "Dynamic Batches Count",
        "Instanced Batches Count", "Static Batches Count",
        "CPU Main Thread Frame Time", "CPU Total Frame Time", "GPU Frame Time",
        "Total Used Memory", "GC Allocated In Frame",
    };

    static bool _recording, _subscribed;
    static int _windowLen = 300, _written, _lastFrame = -1;

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
            if (built == 0) { LastLine = "install_failed skipped=[" + string.Join(",", Skipped.ToArray()) + "]"; return; }
            EditorApplication.update -= Tick;   // 清掉本程序集可能重复的注册
            EditorApplication.update += Tick;
            _subscribed = true;
            Beat();
            LastLine = $"installed recorders={built}/{Targets.Length} skipped=[{string.Join(",", Skipped.ToArray())}]";
            return;
        }
        LastLine = $"alive play={Application.isPlaying} frame={Time.frameCount} recording={_recording} written={_written}";
    }

    static int BuildRecorders()
    {
        Rec.Clear(); Samples.Clear(); Skipped.Clear(); Shadow.Clear();
        _written = 0; _recording = false;

        // ① 全量句柄表按 (真名, 类目) 双条件定位（exp5 R3 已验证写法）
        var located = new Dictionary<string, ProfilerRecorderHandle>();
        var handles = new List<ProfilerRecorderHandle>();
        ProfilerRecorderHandle.GetAvailable(handles);
        var getDesc = typeof(ProfilerRecorderHandle).GetMethod("GetDescription",
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
        var dt = getDesc.ReturnType;
        var nameProp = dt.GetProperty("Name",
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
        var catField = dt.GetField("category",
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
        var catNameProp = catField != null
            ? catField.FieldType.GetProperty("Name",
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            : null;

        foreach (var h in handles)
        {
            object d; try { d = getDesc.Invoke(null, new object[] { h }); } catch { continue; }
            string n; try { n = (string)nameProp.GetValue(d, null); } catch { continue; }
            string cn = "?";
            try { cn = (string)catNameProp.GetValue(catField.GetValue(d), null); } catch { }
            foreach (var w in WantNames)
            {
                if (n != w) continue;
                // 类目按目标表逐条比对：Total Used Memory/GC Allocated In Frame 属 Memory，
                // 其余属 Render——用"该名字在某目标行里要求的类目"做唯一匹配。
                foreach (var t in Targets)
                    if (t[1] == w)
                    {
                        if (cn == t[0]) { if (!located.ContainsKey(w)) located[w] = h; }
                        else { Shadow[w] = (Shadow.ContainsKey(w) ? Shadow[w] : 0) + 1; }
                        break;
                    }
            }
        }

        // ② 定位到才装；定位不到记 skipped（fail-loud，绝不"装了读 0"）
        foreach (var t in Targets)
        {
            string key = t[2];
            ProfilerRecorderHandle h;
            if (!located.TryGetValue(t[1], out h)) { Skipped.Add(key + "(未注册)"); continue; }
            try
            {
                // StartImmediately：不经 Start()（UUM-127894 绕法，exp5 双构造对照）
                var r = new ProfilerRecorder(h, 3000, ProfilerRecorderOptions.StartImmediately);
                Rec[key] = r;
                Samples[key] = new List<long>();
            }
            catch (Exception) { Skipped.Add(key + "(构造失败)"); }
        }
        if (Rec.Count == 0) return 0;
        // 遮蔽留档（诊断：UI Toolkit|Draw Calls Count 这类同名他类目计数）
        var shb = new StringBuilder();
        foreach (var kv in Shadow) shb.Append(kv.Key).Append("=").Append(kv.Value).Append(" ");
        SafeWrite(Dir + "/shadow_counts.txt", shb.ToString());
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
