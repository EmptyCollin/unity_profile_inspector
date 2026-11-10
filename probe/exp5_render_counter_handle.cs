// probe/exp5_render_counter_handle.cs —— 决定性第三轮（exp5 正式件，保留复现）
// 三轮演进（2026-11-10，全部实测）：
//   R1 按文档短名 "Draw Calls" 全词匹配 + 宽正则 ⇒ 匹配到的全是 Render 类目**方法名**
//      （SpriteRenderer.Render 等），统计计数器一个没装 ⇒ 全 0。
//   R2 全量句柄名清单 grep 出真实注册名：**带 " Count" 后缀**
//      （"Draw Calls Count"/"SetPass Calls Count"/"Batches Count"/"Triangles Count"），
//      短名不在句柄表里 ⇒ exp4 时代"注入读渲染 counter=0"的根因很可能一直是**名字错**，
//      不是后端不产数（exp4 的 cat-Render/"Draw Calls" 用的正是短名）。
//      R2 读数：handle 构造下 SetPass/Triangles 非零、Draw Calls 为 0——但同名句柄
//      有 13 个，定位取到的是 **[UI Toolkit] Draw Calls Count**（枚举顺序遮蔽）⇒ 0 是
//      挂错句柄，不是构造失效。Batches 全 0 待验（CLEAN 场景 batches 本来就可能=0）。
//   R3（本件）三处修正后做终局判定（2026-11-10 实测结论）：
//      ① (名字 ∈ 四个 Count 名, 类目 == Render) **双条件**定位，拒绝别的类目同名句柄；
//      ② 场景换 F-GPU-01（有真实绘制活动，batches 非零可对表）；
//      ③ 每行带 UTC 时间戳 + 1Hz 桶写，驱动侧并行轮询 get_performance 后按
//         最近时间对齐（比值 0.2~5 记同数量级）。
//      **结果**：StartImmediately + Render 类目句柄 ⇒ Draw/SetPass/Triangles
//      与外部轮询 60/60 同数量级（可替代）；Default 构造有 2/60 冷启动零值帧。
//      "注入读渲染 counter 恒 0"的完整根因 = ①计数名错（短名不在句柄表）
//      ②同名句柄枚举顺序遮蔽（UI Toolkit 先注册）——两者都修掉后注入通路是活的。
//      Batches 本轮不可判（两侧同为 0，该场景 SRP 下 batches 恒 0，零对零无信息量）。
// 构造对照保留 handle-Default / handle-StartImmediately（UUM-127894 绕法）两组。
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;
using Unity.Profiling;
using Unity.Profiling.LowLevel.Unsafe;

var sb = new StringBuilder();
const BindingFlags NP = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
const BindingFlags NS = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
string dir = @"D:/unity_profile_inspector/runs/_probe";
string outFile = dir + "/exp5_probe.jsonl";
if (File.Exists(outFile)) File.Delete(outFile);

sb.AppendLine("play=" + UnityEngine.Application.isPlaying
              + " tfr=" + UnityEngine.Application.targetFrameRate);

string[] WANT = { "Draw Calls Count", "SetPass Calls Count", "Batches Count",
                  "Triangles Count" };
var handles = new List<ProfilerRecorderHandle>();
ProfilerRecorderHandle.GetAvailable(handles);
var getDesc = typeof(ProfilerRecorderHandle).GetMethod("GetDescription", NS);
var dt = getDesc.ReturnType;
var nameProp = dt.GetProperty("Name", NP);
var catField = dt.GetField("category", NP);
var catNameProp = catField?.FieldType.GetProperty("Name", NP);

// ① 双条件定位：名字命中 **且** 类目 == Render（R2 的 UI Toolkit 遮蔽教训）
var located = new Dictionary<string, ProfilerRecorderHandle>();
var shadow = new Dictionary<string, int>();   // 同名但别的类目，计数供留档
foreach (var h in handles)
{
    object d; try { d = getDesc.Invoke(null, new object[] { h }); } catch { continue; }
    string n; try { n = (string)nameProp.GetValue(d, null); } catch { continue; }
    string cn = "?";
    try { cn = (string)catNameProp.GetValue(catField.GetValue(d), null); } catch { }
    foreach (var w in WANT)
    {
        if (n != w) continue;
        if (cn == "Render") { if (!located.ContainsKey(w)) located[w] = h; }
        else { shadow[w] = (shadow.ContainsKey(w) ? shadow[w] : 0) + 1; }
    }
}
foreach (var w in WANT)
    sb.AppendLine("定位 [" + w + "] Render 类目句柄: " + (located.ContainsKey(w) ? "有" : "无")
                  + "；同名他类目遮蔽 " + (shadow.ContainsKey(w) ? shadow[w] : 0) + " 个");

var sets = new List<UpiExp5Set>();
foreach (var w in WANT)
{
    if (!located.ContainsKey(w)) continue;
    try
    {
        var ra = new ProfilerRecorder(located[w], 64, ProfilerRecorderOptions.Default);
        ra.Start();
        sets.Add(new UpiExp5Set { label = w, mode = "Default", rec = ra });
    }
    catch (Exception e) { sb.AppendLine("Default 构造失败 " + w + " " + e.GetType().Name); }
    try
    {
        var rb = new ProfilerRecorder(located[w], 64, ProfilerRecorderOptions.StartImmediately);
        sets.Add(new UpiExp5Set { label = w, mode = "StartImmediately", rec = rb });
    }
    catch (Exception e) { sb.AppendLine("StartImmediately 构造失败 " + w + " " + e.GetType().Name); }
}
// CONTROL：GC Alloc（A2 已验证可读，证明回调活着；不入对齐表）
foreach (var h in handles)
{
    object d; try { d = getDesc.Invoke(null, new object[] { h }); } catch { continue; }
    string n; try { n = (string)nameProp.GetValue(d, null); } catch { continue; }
    if (n == "GC Allocated In Frame")
    {
        var rgc = new ProfilerRecorder(h, 64, ProfilerRecorderOptions.Default);
        rgc.Start();
        sets.Add(new UpiExp5Set { label = "CONTROL_GC", mode = "Default", rec = rgc });
        break;
    }
}

UpiExp5State.Sets = sets;
UpiExp5State.N = 0; UpiExp5State.Last = -1; UpiExp5State.OutFile = outFile;
EditorApplication.update -= UpiExp5State.Tick;
EditorApplication.update += UpiExp5State.Tick;
sb.AppendLine("installed sets=" + sets.Count);
var s = sb.ToString();
return s.Length > 1900 ? s.Substring(0, 1900) : s;

class UpiExp5Set { public string label; public string mode; public ProfilerRecorder rec; }

static class UpiExp5State
{
    public static List<UpiExp5Set> Sets;
    public static int N;
    public static int Last = -1;
    public static float LastWrite = -1f;   // 1Hz 桶写门控
    public static string OutFile;

    public static void Tick()
    {
        if (Sets == null || Sets.Count == 0) { EditorApplication.update -= Tick; return; }
        if (Time.frameCount == Last) return;
        Last = Time.frameCount;
        // 1Hz 桶写（与轮询节奏同频，60 个点足够判等价；逐帧写 120 行 × 轮询 1 行
        // 是错配——桶内保留帧数与该帧终值，桶末用 UTC 时间戳，跨语言对齐不再涉时区）
        if (Time.realtimeSinceStartup - LastWrite < 1.0f) return;
        LastWrite = Time.realtimeSinceStartup;
        var parts = new List<string>();
        foreach (var st in Sets)
        {
            long v = -2;
            try { v = (long)st.rec.LastValue; } catch (Exception) { v = -3; }   // 属性，非方法
            parts.Add("\"" + st.mode + "|" + st.label + "\":" + v);
        }
        string line = "{\"ts\":\"" + DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture)
            + "\",\"frame\":" + Time.frameCount + ",\"vals\":{" + string.Join(",", parts.ToArray()) + "}}";
        try { File.AppendAllText(OutFile, line + "\n"); } catch (Exception) { }
        if (++N >= 60)
        {
            EditorApplication.update -= Tick;
            try { File.AppendAllText(OutFile, "DONE sets=" + Sets.Count + " n=" + N + "\n"); }
            catch (Exception) { }
        }
    }
}
