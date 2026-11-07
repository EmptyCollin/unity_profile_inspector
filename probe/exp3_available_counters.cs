// probe/exp3_available_counters.cs —— 枚举当前可用 recorder 的真实 (类目, 名称)
// 目的：判定此前 ProfilerRecorder("Render","Draw Calls") 读到 0 是"名称不存在"还是"后端未启用"。
// 说明：ProfilerRecorderDescription 是 internal struct，成员名不靠猜，一律反射读取。
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEngine;
using Unity.Profiling.LowLevel.Unsafe;

var sb = new StringBuilder();
const BindingFlags ALL = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
const BindingFlags ALLS = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;

// 0) ProfilerRecorder 的全部构造函数（上一版只打印了第一个，是缺陷）
sb.Append("== ProfilerRecorder 构造函数全量 ==");
sb.Append(Environment.NewLine);
foreach (var c in typeof(Unity.Profiling.ProfilerRecorder).GetConstructors())
{
    sb.Append("  ctor(" + string.Join(", ", c.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name)) + ")");
    sb.Append(Environment.NewLine);
}

// 1) ProfilerRecorderDescription 的成员形状
var dt = typeof(ProfilerRecorderDescription);
sb.Append("== ProfilerRecorderDescription 成员 ==");
sb.Append(Environment.NewLine);
var strMembers = new List<string>();
foreach (var f in dt.GetFields(ALL))
{
    sb.Append("  field " + f.FieldType.Name + " " + f.Name);
    sb.Append(Environment.NewLine);
    if (f.FieldType == typeof(string)) strMembers.Add(f.Name);
    if (f.FieldType.IsEnum) strMembers.Add(f.Name);
}
foreach (var p in dt.GetProperties(ALL))
{
    sb.Append("  prop " + p.PropertyType.Name + " " + p.Name);
    sb.Append(Environment.NewLine);
    if (p.PropertyType == typeof(string)) strMembers.Add(p.Name);
}

// 2) 枚举可用 recorder
var handles = new List<ProfilerRecorderHandle>();
try { ProfilerRecorderHandle.GetAvailable(handles); }
catch (Exception e)
{
    sb.Append("GetAvailable 异常 " + e.GetType().Name + " :: " + e.Message);
    sb.Append(Environment.NewLine);
    return sb.ToString();
}
sb.Append("== 可用 recorder 数: " + handles.Count + " ==");
sb.Append(Environment.NewLine);

string[] wanted = { "Draw Calls", "DrawObjects", "SetPass", "Batches", "GC Allocated", "Frame Time", "Update Time", "Total Used Memory", "Mono Used" };
var seen = new HashSet<string>();
int renderish = 0;

foreach (var h in handles)
{
    object d;
    try { d = ProfilerRecorderHandle.GetDescription(h); }
    catch (Exception) { continue; }

    var parts = new List<string>();
    foreach (var name in strMembers.Distinct())
    {
        object v = null;
        var f = dt.GetField(name, ALL);
        if (f != null) { try { v = f.GetValue(d); } catch (Exception) { } }
        if (v == null)
        {
            var p = dt.GetProperty(name, ALL);
            if (p != null && p.CanRead) { try { v = p.GetValue(d, null); } catch (Exception) { } }
        }
        if (v != null) parts.Add(name + "=" + v);
    }
    string line = string.Join(" | ", parts);
    if (line == "") continue;
    if (!seen.Add(line)) continue;

    bool hit = wanted.Any(w => line.IndexOf(w, StringComparison.OrdinalIgnoreCase) >= 0);
    bool isRender = line.IndexOf("Render", StringComparison.OrdinalIgnoreCase) >= 0;
    if (hit || isRender)
    {
        if (isRender) renderish++;
        sb.Append("  " + line);
        sb.Append(Environment.NewLine);
    }
}

sb.Append("== 含 Render 字样的条目数: " + renderish + " / 去重后总数: " + seen.Count + " ==");
sb.Append(Environment.NewLine);

// 3) 判定：我用的名字到底存不存在
foreach (var probe in new[] { "Draw Calls", "SetPass Calls", "Batches", "GC Allocated In Frame", "Frame Time", "Update Time", "Mono Used Memory", "Total Used Memory" })
{
    bool exists = seen.Any(s => s.IndexOf("\"" + probe + "\"", StringComparison.OrdinalIgnoreCase) >= 0
                             || s.IndexOf("=" + probe, StringComparison.OrdinalIgnoreCase) >= 0
                             || s.IndexOf(probe, StringComparison.OrdinalIgnoreCase) >= 0);
    sb.Append("  名称 '" + probe + "' 在可用集合中出现? " + exists);
    sb.Append(Environment.NewLine);
}
return sb.ToString();
