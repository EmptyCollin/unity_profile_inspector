// probe/exp4_enable_modules.cs —— 决定性实验：三组 recorder 构造对照 + 按帧采样读数
// 一次运行同时回答两个悬案：
//   Q1 Profiler 模块启用（照 Profile Analyzer 源码的开关写法）后，渲染 counter 是否注册/可读
//   Q2 ProfilerRecorder 三种构造（静态字段类目 / 字符串类目 / handle）哪种能读到非零值
// 静态 ProfilerCategory 可用字段是源码验证过的：Render/Memory/Scripts/Loading/Animation/Physics
// —— 不用 "Rendering"/"GarbageCollector" 这类我未验证的字符串类目（那是上一版的猜测，已废弃）。
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEditorInternal;
using UnityEngine;
using Unity.Profiling;
using Unity.Profiling.LowLevel.Unsafe;

var sb = new StringBuilder();
const BindingFlags NP = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
const BindingFlags NS = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;

// ① 照源码启用（Profile Analyzer 源码用的就是这两个属性）
try
{
    var pdv = typeof(ProfilerDriver);
    pdv.GetProperty("profileEditor", NS)?.SetValue(null, true);
    pdv.GetProperty("enabled", NS)?.SetValue(null, true);
    sb.Append("① profileEditor=" + (pdv.GetProperty("profileEditor", NS)?.GetValue(null) ?? "?")
              + " enabled=" + (pdv.GetProperty("enabled", NS)?.GetValue(null) ?? "?") + Environment.NewLine);
}
catch (Exception e) { sb.Append("① 开关异常 " + e.GetType().Name + Environment.NewLine); }

// ② 类目名从 recorder 句柄反推（不猜 API：GetCategories 实测不存在）
var catNames = new List<string>();

// ③ 枚举 5023 个 recorder，找关键 counter 属于哪个类目（GC Allocated In Frame / Draw Calls）
var handles = new List<ProfilerRecorderHandle>();
ProfilerRecorderHandle.GetAvailable(handles);
var getDesc = typeof(ProfilerRecorderHandle).GetMethod("GetDescription", NS);
var dt = getDesc.ReturnType;
var nameProp = dt.GetProperty("Name", NP);
var catField = dt.GetField("category", NP);
var catNameProp = catField?.FieldType.GetProperty("Name", NP);

string[] targets = { "Draw Calls", "SetPass Calls", "Batches", "GC Allocated In Frame" };
var located = new Dictionary<string, ProfilerRecorderHandle>();
var locatedCat = new Dictionary<string, string>();
foreach (var h in handles)
{
    object d; try { d = getDesc.Invoke(null, new object[] { h }); } catch (Exception) { continue; }
    string n; try { n = (string)nameProp.GetValue(d, null); } catch (Exception) { continue; }
    if (n == null) continue;

    // 每个句柄都解析类目 ⇒ catNames 覆盖全部 5023 条，而非仅命中项
    string cn = "?";
    try
    {
        var catVal = catField?.GetValue(d);
        if (catVal != null) cn = (string)catNameProp.GetValue(catVal, null) ?? "?";
    }
    catch (Exception) { }
    if (cn != "?" && !catNames.Contains(cn)) catNames.Add(cn);

    foreach (var t in targets)
    {
        if (!string.Equals(n, t, StringComparison.OrdinalIgnoreCase) || located.ContainsKey(t)) continue;
        located[t] = h; locatedCat[t] = cn;
    }
}
sb.Append("② 实测存在的类目名（共 " + catNames.Count + " 个）: "
          + string.Join(",", catNames.Take(24)) + Environment.NewLine);
sb.Append("③ 关键 counter 定位（名称→注册类目）:" + Environment.NewLine);
foreach (var t in targets)
    sb.Append("   " + t + " : " + (located.ContainsKey(t) ? "已注册 类目=" + locatedCat[t] : "仍不存在") + Environment.NewLine);

// ④ 三组构造对照，装按帧采样回调（幂等=结果文件新鲜度 <3s）
string dir = @"D:/unity_profile_inspector/runs/_probe";
string dataFile = dir + "/exp4_data.json";
Directory.CreateDirectory(dir);
bool alive = false;
try { alive = File.Exists(dataFile) && (DateTime.UtcNow - File.GetLastWriteTimeUtc(dataFile)).TotalSeconds < 3.0; }
catch (Exception) { }

if (!alive)
{
    var sets = new List<Exp4Set>();
    // 组1：静态 Render 字段 + "Draw Calls"（此前读 0 的原构造，启用模块后复测）
    if (catNames.Contains("Render"))
    {
        var r = new ProfilerRecorder(ProfilerCategory.Render, "Draw Calls", 1200);
        r.Start();
        sets.Add(new Exp4Set { label = "cat-Render/Draw Calls", rec = r });
    }
    // 组2：handle 构造（用 ③ 定位到的句柄，官方推荐的精确取法）
    if (located.ContainsKey("Draw Calls"))
    {
        try
        {
            var r2 = new ProfilerRecorder(located["Draw Calls"], 1200, ProfilerRecorderOptions.Default);
            r2.Start();
            sets.Add(new Exp4Set { label = "handle/Draw Calls", rec = r2 });
        }
        catch (Exception e) { sb.Append("   handle 构造异常 " + e.GetType().Name + Environment.NewLine); }
    }
    // 组3：GC Allocated In Frame（F-CPU-01 的关键指标，whatever 类目定位到什么就用什么构造）
    if (located.ContainsKey("GC Allocated In Frame"))
    {
        try
        {
            var r3 = new ProfilerRecorder(located["GC Allocated In Frame"], 1200, ProfilerRecorderOptions.Default);
            r3.Start();
            sets.Add(new Exp4Set { label = "handle/GC Alloc", rec = r3 });
        }
        catch (Exception) { }
    }
    // 对照组4：Total Used Memory（此前唯一能读到值的，验证采样回调本身工作）
    if (catNames.Contains("Memory"))
    {
        var r4 = new ProfilerRecorder(ProfilerCategory.Memory, "Total Used Memory", 1200);
        r4.Start();
        sets.Add(new Exp4Set { label = "cat-Memory/TotalUsed", rec = r4 });
    }
    Exp4State.Install(sets);
    sb.Append("④ 采样回调安装完成，组数=" + sets.Count + Environment.NewLine);
}
else sb.Append("④ 回调存活（结果文件新鲜），复用现装组" + Environment.NewLine);

sb.Append("isPlaying=" + Application.isPlaying + " frame=" + Time.frameCount + Environment.NewLine);
return sb.ToString();

class Exp4Set
{
    public string label;
    public ProfilerRecorder rec;
}

static class Exp4State
{
    public static List<Exp4Set> Sets;
    public static Dictionary<string, long> Max = new Dictionary<string, long>();
    public static Dictionary<string, long> Last = new Dictionary<string, long>();
    public static int N;
    static int _lastFrame = -1;

    public static void Install(List<Exp4Set> sets)
    {
        Sets = sets; N = 0;
        Max.Clear(); Last.Clear();
        foreach (var s in sets) { Max[s.label] = 0; Last[s.label] = 0; }
        _lastFrame = -1;
        EditorApplication.update -= Tick;
        EditorApplication.update += Tick;
    }

    public static void Tick()
    {
        if (UnityEngine.Time.frameCount == _lastFrame) return;
        _lastFrame = UnityEngine.Time.frameCount;
        N++;
        if (Sets != null)
        {
            foreach (var s in Sets)
            {
                try
                {
                    long v = s.rec.LastValue;
                    Last[s.label] = v;
                    if (v > Max[s.label]) Max[s.label] = v;
                }
                catch (Exception) { }
            }
        }
        if (N % 30 != 0) return;
        string p = @"D:/unity_profile_inspector/runs/_probe/exp4_data.json";
        try
        {
            var sb = new System.Text.StringBuilder("{");
            sb.Append("\"n\":" + N + ",\"frame\":" + UnityEngine.Time.frameCount
                      + ",\"play\":" + (UnityEngine.Application.isPlaying ? "true" : "false") + ",\"sets\":{");
            bool first = true;
            foreach (var k in Max.Keys)
            {
                if (!first) sb.Append(",");
                first = false;
                sb.Append("\"" + k + "\":{\"last\":" + Last[k] + ",\"max\":" + Max[k] + "}");
            }
            sb.Append("}}");
            string tmp = p + ".tmp";
            System.IO.File.WriteAllText(tmp, sb.ToString());
            if (System.IO.File.Exists(p)) System.IO.File.Delete(p);
            System.IO.File.Move(tmp, p);
        }
        catch (Exception) { }
    }
}
