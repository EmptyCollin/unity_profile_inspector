// probe/eval_smoke.cs —— 采集层可行性冒烟（M0'，非最终采样器）
// 目的：1) 确认 script eval_file 能用 Unity.Profiling.ProfilerRecorder 取计数器
//       2) 实测各计数器名是否存在（不猜名字，符合 I1/I8）
using System;
using System.Text;
using UnityEngine;
using Unity.Profiling;

var sb = new StringBuilder();
sb.Append($"unity={Application.unityVersion} isPlaying={Application.isPlaying} gpu={SystemInfo.graphicsDeviceName}\n");

var candidates = new (ProfilerCategory cat, string name)[] {
    (ProfilerCategory.Render, "Draw Calls"),
    (ProfilerCategory.Render, "SetPass Calls"),
    (ProfilerCategory.Render, "Batches"),
    (ProfilerCategory.Render, "Instancing Batches"),
    (ProfilerCategory.Render, "Triangles"),
    (ProfilerCategory.Render, "Vertices"),
    (ProfilerCategory.Memory, "Total Used Memory"),
    (ProfilerCategory.Memory, "Total Reserved Memory"),
    (ProfilerCategory.Memory, "Mono Used Memory"),
    (ProfilerCategory.Memory, "Mono Heap"),
    (ProfilerCategory.Scripts, "Update Time"),
    (ProfilerCategory.Scripts, "Fixed Update Time"),
    (ProfilerCategory.Loading, "Asset Loading Time"),
    (ProfilerCategory.Animation, "Animation Update Time"),
    (ProfilerCategory.Physics, "Physics Time"),
};

var good = new System.Collections.Generic.List<(string cat, string name, ProfilerRecorder rec)>();
foreach (var c in candidates)
{
    try
    {
        var r = new ProfilerRecorder(c.cat, c.name, 5);
        r.Start();
        good.Add((c.cat.ToString(), c.name, r));
        sb.Append($"OK   {c.cat}/{c.name}\n");
    }
    catch (Exception e)
    {
        sb.Append($"FAIL {c.cat}/{c.name} :: {e.GetType().Name}\n");
    }
}

// 采样窗口：sleep 只是让主线程出帧，真实帧序列由常驻采样器落盘（§2.1 A 档）
for (int i = 0; i < 20; i++) { System.Threading.Thread.Sleep(16); }
foreach (var (cat, name, r) in good)
{
    // 实测 API：GetSample(i) 返回 ProfilerRecorderSample，取其 .Value；无 Available 属性
    var vals = new System.Collections.Generic.List<double>();
    for (int i = 0; i < r.Count; i++) vals.Add((double)r.GetSample(i).Value);
    vals.Sort();
    string med = vals.Count > 0 ? vals[vals.Count / 2].ToString("F2") : "n/a";
    string mx = vals.Count > 0 ? vals[vals.Count - 1].ToString("F2") : "n/a";
    sb.Append($"   -> {cat}/{name}: samples={vals.Count} last={r.LastValue} median={med} max={mx}\n");
    r.Dispose();
}
sb.Append($"alive_recorded={good.Count}/{candidates.Length}");
return sb.ToString();
