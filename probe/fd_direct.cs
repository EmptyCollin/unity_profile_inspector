// probe/fd_direct.cs —— 用公开开关 FrameDebugger.enabled 直接验证当帧可取性
// 背景：SetEnabled(true,0) 后 FrameDebugger.enabled 仍为 False ⇒ 之前开关方式错误。
// 本片段每次调用只做一件事（结果写文件，Python 轮询），不依赖跨 eval 内存状态。
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

UpiFdDirect.Run();
return UpiFdDirect.Out;

public static class UpiFdDirect
{
    public static string Out = "";
    const string DIR = @"D:/unity_profile_inspector/runs/_probe";
    const string STATE = DIR + "/fd_direct_state.txt";   // 动作由外部文件下发
    const string RES = DIR + "/fd_direct.json";

    static Type util, dataType;
    static MethodInfo getEvents, getEventData, getEventObj, batchCauses;
    static FieldInfo fDraw, fShader, fPass, fVerts, fIndices, fKw;
    static string action = "";
    static int seq;
    static readonly List<string> notes = new List<string>();

    static Type Find(string name)
    {
        foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
        {
            Type[] ts; try { ts = a.GetTypes(); } catch (Exception) { continue; }
            foreach (var t in ts) if (t != null && t.Name == name) return t;
        }
        return null;
    }

    public static void Run()
    {
        if (util == null)
        {
            util = Find("FrameDebuggerUtility");
            dataType = Find("FrameDebuggerEventData");
            const BindingFlags SF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
            getEvents = util.GetMethod("GetFrameEvents", SF);
            getEventData = util.GetMethod("GetFrameEventData", SF);
            getEventObj = util.GetMethod("GetFrameEventObject", SF);
            batchCauses = util.GetMethod("GetBatchBreakCauseStrings", SF);
            const BindingFlags IF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
            fDraw = dataType.GetField("m_DrawCallCount", IF);
            fShader = dataType.GetField("m_RealShaderName", IF);
            fPass = dataType.GetField("m_PassName", IF);
            fVerts = dataType.GetField("m_VertexCount", IF);
            fIndices = dataType.GetField("m_IndexCount", IF);
            fKw = dataType.GetField("shaderKeywords", IF);
        }

        try { if (File.Exists(STATE)) { action = File.ReadAllText(STATE).Trim(); File.Delete(STATE); } } catch (Exception) { }
        if (action.Length == 0) { Out = "idle frame=" + Time.frameCount; return; }

        var p = typeof(UnityEngine.FrameDebugger).GetProperty("enabled", BindingFlags.Public | BindingFlags.Static);

        if (action == "enable")
        {
            p.SetValue(null, true, null);
            Out = "enabled=" + p.GetValue(null, null) + " frame=" + Time.frameCount;
            return;
        }
        if (action == "disable")
        {
            p.SetValue(null, false, null);
            Out = "enabled=" + p.GetValue(null, null);
            return;
        }
        if (action == "addcubes")
        {
            for (int i = 0; i < 20; i++)
            {
                var c = GameObject.CreatePrimitive(PrimitiveType.Cube);
                c.name = "FDDirect" + (seq++) + "_" + i;
                c.transform.position = new Vector3(-11f - (i % 10) * 0.8f, 6.5f, 3.5f);
            }
            Out = "added 20 frame=" + Time.frameCount;
            return;
        }
        if (action == "read")
        {
            Out = Read();
            return;
        }
        Out = "unknown " + action;
    }

    static string Read()
    {
        char q = (char)34;
        var sb = new StringBuilder();
        sb.Append((char)123);
        sb.Append(q).Append("frame").Append(q).Append((char)58).Append(Time.frameCount).Append((char)44);
        sb.Append(q).Append("rendered").Append(q).Append((char)58).Append(Time.renderedFrameCount).Append((char)44);
        var p = typeof(UnityEngine.FrameDebugger).GetProperty("enabled", BindingFlags.Public | BindingFlags.Static);
        sb.Append(q).Append("fd_enabled").Append(q).Append((char)58).Append((bool)p.GetValue(null, null) ? "true" : "false").Append((char)44);

        try
        {
            var cnt = util.GetProperty("count", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
            var lim = util.GetProperty("limit", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
            var loc = util.GetProperty("locallySupported", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
            sb.Append(q).Append("util_count").Append(q).Append((char)58).Append(cnt == null ? "-1" : Convert.ToString(cnt.GetValue(null, null))).Append((char)44);
            sb.Append(q).Append("util_limit").Append(q).Append((char)58).Append(lim == null ? "-1" : Convert.ToString(lim.GetValue(null, null))).Append((char)44);
            sb.Append(q).Append("locally_supported").Append(q).Append((char)58).Append(loc == null ? "null" : Convert.ToString(loc.GetValue(null, null))).Append((char)44);
        }
        catch (Exception e) { sb.Append(q).Append("prop_err").Append(q).Append((char)58).Append(q).Append(Cut(e.Message)).Append(q).Append((char)44); }

        int total = 0;
        object[] arr = null;
        try
        {
            var raw = getEvents.Invoke(null, null) as Array;
            total = raw == null ? 0 : raw.Length;
            arr = raw == null ? null : CastArray(raw);
        }
        catch (Exception e) { sb.Append(q).Append("getEvents_err").Append(q).Append((char)58).Append(q).Append(Cut((e.InnerException ?? e).Message)).Append(q); }
        sb.Append(q).Append("events").Append(q).Append((char)58).Append(total).Append((char)44);

        int withObj = 0, withShader = 0, drawSum = 0, extraHits = 0;
        var samples = new List<string>();
        var passCount = new Dictionary<string, int>();
        if (arr != null && total > 0)
        {
            for (int i = 0; i < total; i++)
            {
                object inst; try { inst = Activator.CreateInstance(dataType); } catch (Exception) { break; }
                bool ok = false;
                try { ok = (bool)getEventData.Invoke(null, new object[] { arr.GetValue(i), inst }); } catch (Exception) { }
                if (!ok) continue;

                object go = null;
                try { go = getEventObj == null ? null : getEventObj.Invoke(null, new object[] { arr.GetValue(i) }); } catch (Exception) { }
                string objName = "";
                if (go is GameObject g) objName = g.name;
                else if (go is Component co && co != null) objName = co.name;
                if (go != null) withObj++;
                if (objName.IndexOf("FDDirect") >= 0) extraHits++;

                string sh = Str(fShader, inst), pass = Str(fPass, inst);
                if (sh.Length > 0) withShader++;
                if (pass.Length > 0) { if (passCount.ContainsKey(pass)) passCount[pass]++; else passCount[pass] = 1; }
                int dc = 0; try { dc = fDraw == null ? 0 : Convert.ToInt32(fDraw.GetValue(inst)); } catch (Exception) { }
                drawSum += dc;

                if (samples.Count < 6 && (dc > 0 || objName.Length > 0))
                {
                    int vc = 0; try { vc = fVerts == null ? 0 : Convert.ToInt32(fVerts.GetValue(inst)); } catch (Exception) { }
                    string kw = Str(fKw, inst);
                    samples.Add((char)123 + Str2(q + "obj" + q) + (char)58 + Str2(q + Cut(objName) + q) + (char)44
                        + Str2(q + "shader" + q) + (char)58 + Str2(q + Tail(sh) + q) + (char)44
                        + Str2(q + "pass" + q) + (char)58 + Str2(q + Cut(pass) + q) + (char)44
                        + Str2(q + "draw" + q) + (char)58 + dc + (char)44
                        + Str2(q + "verts" + q) + (char)58 + vc + (char)44
                        + Str2(q + "kw" + q) + (char)58 + Str2(q + Cut(kw) + q) + (char)125);
                }
            }
        }
        sb.Append(q).Append("with_obj").Append(q).Append((char)58).Append(withObj).Append((char)44);
        sb.Append(q).Append("with_shader").Append(q).Append((char)58).Append(withShader).Append((char)44);
        sb.Append(q).Append("draw_sum").Append(q).Append((char)58).Append(drawSum).Append((char)44);
        sb.Append(q).Append("extra_hits").Append(q).Append((char)58).Append(extraHits).Append((char)44);

        var pv = new List<string>();
        foreach (var kv in passCount.OrderByDescending(x => x.Value).Take(8))
            pv.Add(Str2(q + Cut(kv.Key) + q) + (char)58 + kv.Value);
        sb.Append(q).Append("passes").Append(q).Append((char)58).Append((char)123).Append(string.Join(((char)44).ToString(), pv.ToArray())).Append((char)125).Append((char)44);

        sb.Append(q).Append("samples").Append(q).Append((char)58).Append((char)91)
          .Append(string.Join(((char)44).ToString(), samples.ToArray())).Append((char)93).Append((char)44);

        // 合批破坏原因：引擎自述，对 F-GPU-01 归因价值最高
        try
        {
            if (batchCauses != null)
            {
                var cs = batchCauses.Invoke(null, null) as string[];
                var list = new List<string>();
                if (cs != null) for (int i = 0; i < cs.Length && i < 6; i++) list.Add(Str2(q + Cut(cs[i]) + q));
                sb.Append(q).Append("batch_break_causes_n").Append(q).Append((char)58)
                  .Append(cs == null ? 0 : cs.Length).Append((char)44);
                sb.Append(q).Append("batch_break_causes").Append(q).Append((char)58).Append((char)91)
                  .Append(string.Join(((char)44).ToString(), list.ToArray())).Append((char)93);
            }
            else sb.Append(q).Append("batch_break_causes_missing").Append(q).Append((char)58).Append("true");
        }
        catch (Exception e) { sb.Append(q).Append("causes_err").Append(q).Append((char)58).Append(q).Append(Cut((e.InnerException ?? e).Message)).Append(q); }

        sb.Append((char)125);
        string outp = sb.ToString();
        try { Directory.CreateDirectory(DIR); File.WriteAllText(RES, outp); } catch (Exception) { }
        Out = outp.Length > 900 ? outp.Substring(0, 900) : outp;
        return Out;
    }

    static object[] CastArray(Array raw)
    {
        var list = new object[raw.Length];
        for (int i = 0; i < raw.Length; i++) list[i] = raw.GetValue(i);
        return list;
    }

    static string Str(FieldInfo f, object inst)
    {
        if (f == null) return "";
        try { var v = f.GetValue(inst); return v == null ? "" : v.ToString(); } catch (Exception) { return ""; }
    }

    static string Str2(string s) { return s; }

    static string Tail(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        var p = s.Split((char)47);
        return p[p.Length - 1];
    }

    static string Cut(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        s = s.Replace("\n", " ").Replace("\r", " ").Replace("\t", " ");
        if (s.Length > 110) s = s.Substring(0, 110);
        return s.Replace(((char)34).ToString(), ((char)39).ToString()).Replace("\\", "/");
    }
}
