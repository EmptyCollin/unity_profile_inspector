// probe/exp1_offline_data.cs —— 实验1：离线解析 .data 逐帧取计数器；实验2：探测分配 recorder 类型名
// 原则：不猜 API 名（AGENTS I8/I1），取不到就如实报异常类型。
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEditorInternal;
using UnityEngine;

var sb = new StringBuilder();
string capDir = @"D:\unity_profile_inspector\fixtures\upi-fixture\ProfilerCaptures";
string dataPath = "";
if (System.IO.Directory.Exists(capDir))
{
    // 取最新一个（按写入时间），避免解析到旧录制
    var files = System.IO.Directory.GetFiles(capDir, "*.data");
    foreach (var f in files)
    {
        var w = System.IO.File.GetLastWriteTimeUtc(f);
        if (dataPath == "" || w > System.IO.File.GetLastWriteTimeUtc(dataPath)) dataPath = f;
    }
}

sb.Append("== 实验1：ProfilerDriver.LoadProfile 离线解析 ==");
sb.Append(Environment.NewLine);
sb.Append("  文件: " + (dataPath == "" ? "无 .data" : System.IO.Path.GetFileName(dataPath)));
sb.Append(Environment.NewLine);

if (dataPath != "")
{
    bool loaded = false;
    try { loaded = ProfilerDriver.LoadProfile(dataPath, false); }
    catch (Exception e) { sb.Append("  LoadProfile 抛异常: " + e.GetType().Name); sb.Append(Environment.NewLine); }
    sb.Append("  LoadProfile -> " + loaded);
    sb.Append(Environment.NewLine);

    int first = ProfilerDriver.firstFrameIndex;
    int last = ProfilerDriver.lastFrameIndex;
    sb.Append("  帧区间 " + first + ".." + last);
    sb.Append(Environment.NewLine);

    int n = last >= first ? Math.Min(30, last - first + 1) : 0;
    if (n > 0)
    {
        var targets = new List<string[]>();
        targets.Add(new[] { "Render", "Draw Calls" });
        targets.Add(new[] { "Render", "SetPass Calls" });
        targets.Add(new[] { "Render", "Batches" });
        targets.Add(new[] { "Memory", "Total Used Memory" });
        targets.Add(new[] { "Memory", "GC Allocated In Frame" });
        targets.Add(new[] { "Scripts", "Update Time" });
        targets.Add(new[] { "Rendering", "Frame Time" });

        foreach (var t in targets)
        {
            string line = "  [" + t[0] + "/" + t[1] + "] ";
            try
            {
                var buf = new float[n];
                float mx = 0f;
                ProfilerDriver.GetCounterValuesBatch(t[0], t[1], first, 1f, buf, out mx);
                int nonzero = buf.Count(x => x != 0f);
                line += "max=" + mx.ToString("F1") + " 非零=" + nonzero + "/" + n;
                if (nonzero > 0)
                {
                    string head = string.Join(",", buf.Take(6).Select(x => x.ToString("F1")));
                    line += " 前6=" + head;
                }
            }
            catch (Exception e)
            {
                line += "异常 " + e.GetType().Name;
            }
            sb.Append(line);
            sb.Append(Environment.NewLine);
        }
    }
}

sb.Append("== 实验2：分配 recorder 类型探测（反射，不猜名字）==");
sb.Append(Environment.NewLine);
int hits = 0;
foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
{
    Type[] types;
    try { types = asm.GetTypes(); }
    catch (Exception) { continue; }

    foreach (var t in types)
    {
        if (t == null || !t.IsPublic) continue;
        string tn = t.Name;
        if (!(tn.Contains("GCAlloc") || tn.Contains("AllocRecorder") || tn.Contains("Recorder"))) continue;

        hits++;
        sb.Append("  " + t.FullName + "  [" + asm.GetName().Name + "]");
        sb.Append(Environment.NewLine);

        foreach (var m in t.GetMethods(BindingFlags.Public | BindingFlags.Static))
        {
            if (m.IsSpecialName) continue;
            if (!m.Name.StartsWith("Get")) continue;
            string ps = string.Join(",", m.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name));
            sb.Append("     static " + m.ReturnType.Name + " " + m.Name + "(" + ps + ")");
            sb.Append(Environment.NewLine);
        }
        var ctor = t.GetConstructors().FirstOrDefault();
        if (ctor != null)
        {
            string cps = string.Join(",", ctor.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name));
            sb.Append("     ctor(" + cps + ")");
            sb.Append(Environment.NewLine);
        }
    }
    if (hits > 12) break;
}
if (hits == 0) { sb.Append("  未命中 Recorder 类型"); sb.Append(Environment.NewLine); }

sb.Append("== 对照：当前会话（非离线）批量读数 ==");
sb.Append(Environment.NewLine);
int curLast = ProfilerDriver.lastFrameIndex;
int curFirst = ProfilerDriver.firstFrameIndex;
sb.Append("  区间 " + curFirst + ".." + curLast);
sb.Append(Environment.NewLine);
if (curLast >= curFirst)
{
    int m2 = Math.Min(20, curLast - curFirst + 1);
    var cmp = new List<string[]>();
    cmp.Add(new[] { "Render", "Draw Calls" });
    cmp.Add(new[] { "Memory", "GC Allocated In Frame" });
    foreach (var t in cmp)
    {
        string line = "  [" + t[0] + "/" + t[1] + "] ";
        try
        {
            var buf = new float[m2];
            float mx = 0f;
            ProfilerDriver.GetCounterValuesBatch(t[0], t[1], curFirst, 1f, buf, out mx);
            line += "max=" + mx.ToString("F1") + " 非零=" + buf.Count(x => x != 0f) + "/" + m2;
        }
        catch (Exception e) { line += "异常 " + e.GetType().Name; }
        sb.Append(line);
        sb.Append(Environment.NewLine);
    }
}
return sb.ToString();
