// probe/counter_names_dump.cs —— 句柄名×类目权威对照表 dump（#27 前置）
// 为什么需要：sampler_v3 的 7 项目标里，渲染 4 项按 exp5 已有真名（Count 后缀），
// 但 Frame Time / Mono Used Memory / Update Time 的**注册真名与所属类目**无实测
// 记录（render_probe_names.txt 只有名没类目）；字符串构造找不到的名字**不抛异常**
// ⇒ 静默 0（实测 data.json skipped=[] 而渲染四项全 0）。双条件定位要求每条目标
// 的 (类目, 真名) 都有证据，不许按 UI 显示名联想（I1）。
// 输出：全量 "类目|名字" 清单 + 目标关键词命中区，供 sampler_v3 Targets 定稿。
using System;
using System.Collections.Generic;
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

var handles = new List<ProfilerRecorderHandle>();
ProfilerRecorderHandle.GetAvailable(handles);
var getDesc = typeof(ProfilerRecorderHandle).GetMethod("GetDescription", NS);
var dt = getDesc.ReturnType;
var nameProp = dt.GetProperty("Name", NP);
var catField = dt.GetField("category", NP);
var catNameProp = catField?.FieldType.GetProperty("Name", NP);

string[] WANT = { "Draw Calls Count", "SetPass Calls Count", "Batches Count",
                  "Triangles Count", "Vertices Count", "Indices Count",
                  "Frame Time", "Used Memory", "Mono Used Memory",
                  "Update Time", "GC Allocated In Frame", "Coroutines Scheduled",
                  "Coroutines Paused" };
var rows = new List<string>();
foreach (var h in handles)
{
    object d; try { d = getDesc.Invoke(null, new object[] { h }); } catch { continue; }
    string n; try { n = (string)nameProp.GetValue(d, null); } catch { continue; }
    string cn = "?";
    try { cn = (string)catNameProp.GetValue(catField.GetValue(d), null); } catch { }
    rows.Add(cn + "|" + n);
}
rows.Sort();

string dir = @"D:/unity_profile_inspector/runs/_probe";
Directory.CreateDirectory(dir);
File.WriteAllText(dir + "/counter_cat_full.txt", string.Join("\n", rows.ToArray()));
sb.AppendLine("total=" + rows.Count);
int shown = 0;
foreach (var r in rows)
{
    // 全词命中：类目|名 的名部分以 WANT 项结尾或含之（"Mono Used Memory" 这类
    // 目标名可能带前缀变体，宁可多列不许漏；行数封顶防爆返回）
    foreach (var w in WANT)
        if (r.EndsWith(w, StringComparison.Ordinal) || r.EndsWith("|" + w, StringComparison.Ordinal))
        { sb.AppendLine("HIT " + r); shown++; break; }
    if (shown >= 120) { sb.AppendLine("...capped"); break; }
}
var s = sb.ToString();
return s.Length > 3500 ? s.Substring(0, 3500) : s;
