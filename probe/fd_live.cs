// probe/fd_live.cs —— 决定性实验：探针响的那一刻，能否拿到当帧渲染数据
//
// 只回答四个可判定的问题（全部靠输出对照，不靠推测）：
//   Q1 不开窗口，能否以编程方式开启抓取（SetEnabled 的真实签名）
//   Q2 触发时刻读事件：数量/内容是否对应**当前**场景（陈旧数据检验：先抓一次，加 20 个立方体，再抓一次，比较增量）
//   Q3 事件里能否拿到 GameObject（决定归因能否落到"这笔是谁画的"）
//   Q4 开启 Frame Debugger 自身带来多少帧时间开销（观察者效应，先量后用）
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

UpiFd.Main();
return UpiFd.Line;

public static class UpiFd
{
    public static string Line = "";
    const string DIR = @"D:/unity_profile_inspector/runs/_probe";
    const string CTL = DIR + "/fd_cmd.txt";
    const string RES = DIR + "/fd_res.json";

    static Type util, dataType;
    static MethodInfo setEnabled, getEvents, getEventData, getEventName, getEventObj;
    static FieldInfo fDraw, fShader, fPass;
    static readonly Dictionary<string, string> caps = new Dictionary<string, string>();
    static readonly List<double> dtOn = new List<double>();
    static readonly List<double> dtOff = new List<double>();
    static bool fdOn, sleepOnce;
    static double lastReal;

    static Type Find(string name)
    {
        foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
        {
            Type[] ts; try { ts = a.GetTypes(); } catch (Exception) { continue; }
            foreach (var t in ts) if (t.Name == name) return t;
        }
        return null;
    }

    public static void Main()
    {
        Directory.CreateDirectory(DIR);
        util = Find("FrameDebuggerUtility");
        dataType = Find("FrameDebuggerEventData");
        if (util == null || dataType == null) { Line = "types missing util=" + (util != null) + " data=" + (dataType != null); return; }

        const BindingFlags fl = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
        const BindingFlags fi2 = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
        setEnabled = util.GetMethod("SetEnabled", fl);
        getEvents = util.GetMethod("GetFrameEvents", fl);
        getEventData = util.GetMethod("GetFrameEventData", fl);
        getEventName = util.GetMethod("GetFrameEventInfoName", fl);
        getEventObj = util.GetMethod("GetFrameEventObject", fl);
        fDraw = dataType.GetField("m_DrawCallCount", fi2);
        fShader = dataType.GetField("m_RealShaderName", fi2);
        fPass = dataType.GetField("m_PassName", fi2);

        var ps = setEnabled == null ? "none" : string.Join(",", setEnabled.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name));
        Line = "set=[" + ps + "] getEvents=" + (getEvents != null) + " getEventData=" + (getEventData != null)
             + " obj=" + (getEventObj != null) + " fields=" + (fDraw != null) + (fShader != null) + (fPass != null);
        EditorApplication.update -= Tick;
        EditorApplication.update += Tick;
    }

    static void Toggle(bool on)
    {
        try { setEnabled.Invoke(null, new object[] { on, 0 }); fdOn = on; }  // 第二参数=remotePlayerGUID，0 为本地 Editor
        catch (Exception e) { caps["toggle_err"] = Str((e.InnerException ?? e).Message); }
    }

    static void Tick()
    {
        double now = Time.realtimeSinceStartupAsDouble;
        double dt = (now - lastReal) * 1000.0;
        lastReal = now;
        if (dt > 0 && dt < 1000) { if (fdOn) dtOn.Add(dt); else dtOff.Add(dt); }

        string ctl = "";
        try { if (File.Exists(CTL)) { ctl = File.ReadAllText(CTL).Trim(); File.Delete(CTL); } } catch (Exception) { }

        if (ctl.StartsWith("arm")) { Toggle(true); sleepOnce = true; }
        else if (ctl.StartsWith("on")) { Toggle(true); }
        else if (ctl.StartsWith("off")) { Toggle(false); }
        else if (ctl.StartsWith("spike")) { sleepOnce = true; }          // 人造尖峰：验证"响的那刻"读取
        else if (ctl.StartsWith("addcubes")) { AddCubes(); }
        else if (ctl.StartsWith("cap")) { Capture(ctl); }
        else if (ctl.StartsWith("dump")) { Dump(); }

        if (sleepOnce) { sleepOnce = false; System.Threading.Thread.Sleep(40); }  // 制造一次 >20ms 帧
    }

    static int cubeSeq;
    static void AddCubes()
    {
        for (int i = 0; i < 20; i++)
        {
            var c = GameObject.CreatePrimitive(PrimitiveType.Cube);
            c.name = "FDExtra" + (cubeSeq++) + "_" + i;
            c.transform.position = new UnityEngine.Vector3(-10f - i * 0.6f, 6f, 3f);
        }
        caps["added"] = "20";
    }

    static void Capture(string tag)
    {
        var j = new StringBuilder();
        j.Append(CHR).Append("frame").Append(CHR).Append(":").Append(UnityEngine.Time.frameCount).Append(",");
        j.Append(CHR).Append("rendered").Append(CHR).Append(":").Append(UnityEngine.Time.renderedFrameCount).Append(",");
        j.Append(CHR).Append("fd_on").Append(CHR).Append(":").Append(fdOn ? "true" : "false").Append(",");
        j.Append(CHR).Append("spike_after_last_frame").Append(CHR).Append(":").Append((dtOff.Count > 0 && dtOn.Count > 0) ? "mixed" : "n/a").Append(",");

        object[] arr = null;
        try { arr = (object[])getEvents.Invoke(null, null); }
        catch (Exception e) { caps[tag + "_err"] = Str((e.InnerException ?? e).Message); j.Append(CHR).Append("error").Append(CHR).Append(":").Append(CHR).Append(Str((e.InnerException ?? e).Message)).Append(CHR); Write(j.ToString()); return; }
        int total = arr == null ? 0 : arr.Length;
        j.Append(CHR).Append("events").Append(CHR).Append(":").Append(total).Append(",");

        int withObj = 0, withShader = 0, drawSum = 0;
        long idProbe = 0;
        var samples = new List<string>();
        for (int i = 0; i < total; i++)
        {
            object inst; try { inst = Activator.CreateInstance(dataType); } catch (Exception) { break; }
            bool ok = false;
            try { ok = (bool)getEventData.Invoke(null, new object[] { arr[i], inst }); } catch (Exception) { }
            if (!ok) continue;

            object go = null;
            try { if (getEventObj != null) go = getEventObj.Invoke(null, new object[] { arr[i] }); } catch (Exception) { }
            if (go != null) withObj++;

            int dc = 0;
            try { dc = fDraw == null ? 0 : Convert.ToInt32(fDraw.GetValue(inst)); } catch (Exception) { }
            drawSum += dc;

            string sh = "";
            try { sh = fShader == null ? "" : (string)fShader.GetValue(inst); } catch (Exception) { }
            if (!string.IsNullOrEmpty(sh)) withShader++;

            if (samples.Count < 5 && dc > 0)
            {
                string nm = "null";
                if (go is GameObject g) nm = g.name;
                else if (go is Component co && co != null) nm = co.name + "|" + co.GetType().Name;
                var s = new StringBuilder();
                s.Append("{").Append(CHR).Append("i").Append(CHR).Append(":").Append(i).Append(",");
                s.Append(CHR).Append("obj").Append(CHR).Append(":").Append(CHR).Append(Str(nm)).Append(CHR).Append(",");
                s.Append(CHR).Append("shader").Append(CHR).Append(":").Append(CHR).Append(Str(Trim(sh))).Append(CHR).Append(",");
                string pass = "";
                try { pass = fPass == null ? "" : (string)fPass.GetValue(inst); } catch (Exception) { }
                s.Append(CHR).Append("pass").Append(CHR).Append(":").Append(CHR).Append(Str(Trim(pass))).Append(CHR).Append("}");
                samples.Add(s.ToString());
            }
        }
        j.Append(CHR).Append("with_obj").Append(CHR).Append(":").Append(withObj).Append(",");
        j.Append(CHR).Append("with_shader").Append(CHR).Append(":").Append(withShader).Append(",");
        j.Append(CHR).Append("draw_sum").Append(CHR).Append(":").Append(drawSum).Append(",");
        j.Append(CHR).Append("samples").Append(CHR).Append(":[").Append(string.Join(",", samples.ToArray())).Append("]");
        caps[tag] = j.ToString();
        Line = tag + " events=" + total + " obj=" + withObj + " drawSum=" + drawSum + " idProbe=" + idProbe;
    }

    static void Dump()
    {
        double med(List<double> l) { var a = l.OrderBy(x => x).ToArray(); return a.Length == 0 ? -1 : a[a.Length / 2]; }
        var sb = new StringBuilder();
        sb.Append("{").Append(CHR).Append("captures").Append(CHR).Append(":{");
        bool f = true;
        foreach (var kv in caps)
        {
            if (kv.Key.EndsWith("_err")) continue;
            if (!f) sb.Append(","); f = false;
            sb.Append(CHR).Append(kv.Key).Append(CHR).Append(":").Append(
                (kv.Value.StartsWith("{") ? kv.Value : CHR + Str(kv.Value) + CHR));
        }
        sb.Append("},");
        int nOn = dtOn.Count, nOff = dtOff.Count;
        double mOn = med(dtOn), mOff = med(dtOff);
        sb.Append(CHR).Append("overhead").Append(CHR).Append(":{").Append(CHR).Append("n_off").Append(CHR).Append(":").Append(nOff)
          .Append(",").Append(CHR).Append("median_off_ms").Append(CHR).Append(":").Append(mOff.ToString("F3"))
          .Append(",").Append(CHR).Append("n_on").Append(CHR).Append(":").Append(nOn)
          .Append(",").Append(CHR).Append("median_on_ms").Append(CHR).Append(":").Append(mOn.ToString("F3"))
          .Append(",").Append(CHR).Append("overhead_pct").Append(CHR).Append(":")
          .Append((mOff > 0 && mOn > 0) ? ((mOn - mOff) / mOff * 100.0).ToString("F2") : "null")
          .Append("},");
        sb.Append(CHR).Append("errors").Append(CHR).Append(":{");
        f = true;
        foreach (var kv in caps.Where(x => x.Key.EndsWith("_err")))
        { if (!f) sb.Append(","); f = false; sb.Append(CHR).Append(kv.Key).Append(CHR).Append(":").Append(CHR).Append(Str(kv.Value)).Append(CHR); }
        sb.Append("}}");
        try { File.WriteAllText(RES, sb.ToString()); } catch (Exception e) { Line = "write err " + e.Message; }
        Line = "dumped " + caps.Count + " caps; on=" + nOn + " off=" + nOff + " mOn=" + mOn + " mOff=" + mOff;
    }

    static void Write(string s)
    {
        try { caps["last_err_capture"] = s; } catch (Exception) { }
    }

    static readonly char CHR = (char)34;
    static string Str(string s) { return string.IsNullOrEmpty(s) ? "" : new string(s.Replace("\n", " ").Replace("\r", " ").Take(120).ToArray()).Replace("\\", "/"); }
    static string Trim(string s) { if (string.IsNullOrEmpty(s)) return ""; var p = s.Split('/'); return p[p.Length - 1]; }
}
