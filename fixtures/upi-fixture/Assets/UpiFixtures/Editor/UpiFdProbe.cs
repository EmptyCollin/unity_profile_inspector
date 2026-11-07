// UpiFdProbe.cs —— 常驻帧调试器探针（第三层可行性实验）
//
// 上一版失败根因：eval 临时类型不跨命令存活，arm 与 capture 分属两次 eval，
// 状态必然丢失。本版是真实管线形态：一个常驻回调 + 文件控制，
// 并且"触发即抓取"发生在越线的那同一个 tick 内 —— 这正面回答核心问题。
// 注释内不写花括号与引号，避免宿主按字符配对时误判。
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

[InitializeOnLoad]
public static class UpiFdProbe
{
    const string DIR = @"D:/unity_profile_inspector/runs/_probe";
    const string CTL = DIR + "/fd_cmd.txt";
    const string RES = DIR + "/fd_res.json";

    static Type util, dataType;
    static MethodInfo setEnabled, getEvents, getEventData, getEventObj;
    static FieldInfo fDraw, fShader;
    static bool wired, fdOn, armed, triggered;
    static double lastReal, thresholdDt = 20.0;
    static readonly List<double> dtOff = new List<double>();
    static readonly List<double> dtOn2 = new List<double>();
    static readonly List<GameObject> keep = new List<GameObject>();
    static string lastLine = "init";

    [InitializeOnLoadMethod]
    static void Boot()
    {
        EditorApplication.update += Tick;
        if (Directory.Exists(DIR))
        {
            Write("boot", Time.frameCount.ToString());
        }
    }

    static void WireOnce()
    {
        if (wired) return;
        util = Find("FrameDebuggerUtility");
        dataType = Find("FrameDebuggerEventData");
        if (util == null || dataType == null) return;
        const BindingFlags SF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
        setEnabled = util.GetMethod("SetEnabled", SF);
        getEvents = util.GetMethod("GetFrameEvents", SF);
        getEventData = util.GetMethod("GetFrameEventData", SF);
        getEventObj = util.GetMethod("GetFrameEventObject", SF);
        const BindingFlags IF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
        fDraw = dataType.GetField("m_DrawCallCount", IF);
        fShader = dataType.GetField("m_RealShaderName", IF);
        wired = setEnabled != null && getEvents != null && getEventData != null;
        lastLine = wired ? "wired obj=" + (getEventObj != null) : "wire failed";
    }

    static Type Find(string name)
    {
        foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
        {
            Type[] ts;
            try { ts = a.GetTypes(); } catch (Exception) { continue; }
            foreach (var t in ts) if (t != null && t.Name == name) return t;
        }
        return null;
    }

    static void Tick()
    {
        WireOnce();
        double now = Time.realtimeSinceStartupAsDouble;
        double dt = (now - lastReal) * 1000.0;
        lastReal = now;
        if (dt > 0 && dt < 1000)
        {
            if (fdOn) dtOn2.Add(dt); else dtOff.Add(dt);

            // 核心：越线判定与抓取在同一 tick 完成，不依赖任何后续调用
            if (armed && !triggered && wired && dt > thresholdDt)
            {
                triggered = true;
                Write("triggered", Capture());
                Log("TRIGGER dt=" + dt.ToString("F1") + " " + lastLine);
            }
        }

        string cmd = "";
        try
        {
            if (File.Exists(CTL)) { cmd = File.ReadAllText(CTL).Trim(); File.Delete(CTL); }
        }
        catch (Exception) { }
        if (cmd.Length == 0) return;

        string head = cmd;
        int ci = cmd.IndexOf((char)58);
        if (ci >= 0) head = cmd.Substring(0, ci);
        if (head == "addcubes")
        {
            int n = Num(cmd, "n", 20);
            for (int i = 0; i < n; i++)
            {
                var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
                go.name = "FDExtra" + (keep.Count) + "_" + i;
                go.transform.position = new Vector3(-12f - (i % 10) * 0.7f, 7f, 4f);
                keep.Add(go);
            }
            Write("added", n.ToString());
            Log("added " + n);
        }
        else if (head == "arm")
        {
            thresholdDt = Num(cmd, "dt", 20);
            armed = true; triggered = false;
            dtOff.Clear(); dtOn2.Clear();
            Toggle(true);
            Write("armed", "true");
            Log("armed dt=" + thresholdDt);
        }
        else if (head == "on") { Toggle(true); Log("fd on"); }
        else if (head == "off") { Toggle(false); Log("fd off"); }
        else if (head == "capture")
        {
            string tag = Tag(cmd);
            string cap = Capture();
            Write(tag, cap);
            Log("capture " + tag + " " + lastLine);
        }
        else { Log("unknown_cmd " + Clean(cmd)); }
    }

    static string Q(string s) { return ((char)34).ToString() + s + ((char)34).ToString(); }

    static int Num(string s, string key, int fallback)
    {
        int i = s.IndexOf(key);
        if (i < 0) return fallback;
        int j = s.IndexOf((char)58, i);
        if (j < 0) return fallback;
        string num = "";
        for (int k = j + 1; k < s.Length && char.IsDigit(s[k]); k++) num += s[k];
        int v;
        return int.TryParse(num, out v) ? v : fallback;
    }

    static string Tag(string s)
    {
        int i = s.IndexOf((char)58);
        if (i < 0) return "base";
        string t = s.Substring(i + 1).Trim();
        int k = t.IndexOf((char)44);
        if (k >= 0) t = t.Substring(0, k);
        return t.Length == 0 ? "base" : t;
    }

    static void Log(string s)
    {
        try
        {
            File.AppendAllText(DIR + "/fd_log.txt",
                DateTime.Now.ToString("HH:mm:ss.fff") + " " + s + Environment.NewLine);
        }
        catch (Exception) { }
    }

    static void Toggle(bool on)
    {
        try { setEnabled.Invoke(null, new object[] { on, 0 }); fdOn = on; }
        catch (Exception e) { Log("toggle_err " + Clean((e.InnerException ?? e).Message)); }
    }

    static string Clean(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        s = s.Replace("\n", " ").Replace("\r", " ").Replace("\t", " ");
        if (s.Length > 130) s = s.Substring(0, 130);
        return s.Replace(((char)34).ToString(), ((char)39).ToString());
    }

    static string Capture()
    {
        char c = (char)58, lb = (char)123, rb = (char)125, ls = (char)91, rs = (char)93, cm = (char)44;
        var j = new StringBuilder();
        j.Append(lb);
        j.Append(Q("frame")).Append(c).Append(Time.frameCount).Append(cm);
        j.Append(Q("rendered")).Append(c).Append(Time.renderedFrameCount).Append(cm);
        j.Append(Q("fd_on")).Append(c).Append(fdOn ? "true" : "false").Append(cm);
        j.Append(Q("wired")).Append(c).Append(wired ? "true" : "false").Append(cm);

        if (!wired)
        {
            j.Append(Q("error")).Append(c).Append(Q("not wired")).Append(rb);
            return j.ToString();
        }

        System.Array arr = null;
        int total = 0;
        try
        {
            object raw = getEvents.Invoke(null, null);
            arr = raw as System.Array;
            total = arr == null ? 0 : arr.Length;
        }
        catch (Exception e)
        {
            j.Append(Q("error")).Append(c).Append(Q(Clean((e.InnerException ?? e).Message)));
            j.Append(rb);
            return j.ToString();
        }
        j.Append(Q("events")).Append(c).Append(total).Append(cm);

        int withObj = 0, withShader = 0, drawSum = 0, extraHits = 0;
        var samples = new List<string>();
        for (int i = 0; i < total; i++)
        {
            object inst;
            try { inst = Activator.CreateInstance(dataType); } catch (Exception) { break; }
            bool ok = false;
            try { ok = (bool)getEventData.Invoke(null, new object[] { arr.GetValue(i), inst }); } catch (Exception) { }
            if (!ok) continue;

            object go = null;
            try { if (getEventObj != null) go = getEventObj.Invoke(null, new object[] { arr.GetValue(i) }); } catch (Exception) { }
            string objName = "";
            if (go is GameObject g) objName = g.name;
            else if (go is Component co && co != null) { objName = co.name; withObj++; }
            else if (go != null) withObj++;
            if (objName.IndexOf("FDExtra") >= 0) extraHits++;

            int dc = 0;
            try { if (fDraw != null) dc = Convert.ToInt32(fDraw.GetValue(inst)); } catch (Exception) { }
            drawSum += dc;

            string sh = "";
            try { if (fShader != null) sh = (string)fShader.GetValue(inst); } catch (Exception) { }
            if (!string.IsNullOrEmpty(sh)) withShader++;

            if (samples.Count < 8 && (dc > 0 || objName.Length > 0))
            {
                string one = lb + Q("i") + c + i + cm + Q("obj") + c + Q(Clean(objName))
                           + cm + Q("shader") + c + Q(Clean(Tail(sh))) + cm + Q("draw") + c + dc + rb;
                samples.Add(one);
            }
        }
        j.Append(Q("with_obj")).Append(c).Append(withObj).Append(cm);
        j.Append(Q("with_shader")).Append(c).Append(withShader).Append(cm);
        j.Append(Q("draw_sum")).Append(c).Append(drawSum).Append(cm);
        j.Append(Q("extra_hits")).Append(c).Append(extraHits).Append(cm);
        j.Append(Q("samples")).Append(c).Append(ls).Append(string.Join(cm.ToString(), samples.ToArray())).Append(rs);
        j.Append(rb);
        lastLine = "ev=" + total + " obj=" + withObj + " sh=" + withShader + " draw=" + drawSum + " extra=" + extraHits;
        return j.ToString();
    }

    static string Tail(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        var p = s.Split((char)47);
        return p[p.Length - 1];
    }

    static void Write(string key, string valueJson)
    {
        char c = (char)58, cm = (char)44, lb = (char)123, rb = (char)125;
        var full = new StringBuilder();
        full.Append(lb).Append(Q(key)).Append(c).Append(valueJson).Append(cm);
        full.Append(Q("n_off")).Append(c).Append(dtOff.Count).Append(cm);
        full.Append(Q("med_off_ms")).Append(c).Append(Med(dtOff).ToString("F3")).Append(cm);
        full.Append(Q("n_on")).Append(c).Append(dtOn2.Count).Append(cm);
        full.Append(Q("med_on_ms")).Append(c).Append(Med(dtOn2).ToString("F3")).Append(cm);
        full.Append(Q("fd_on")).Append(c).Append(fdOn ? "true" : "false").Append(cm);
        full.Append(Q("armed")).Append(c).Append(armed ? "true" : "false").Append(cm);
        full.Append(Q("line")).Append(c).Append(Q(Clean(lastLine))).Append(rb);
        // 写失败不再静默：这是第四次被吞异常坑，必须留痕
        try { File.WriteAllText(RES, full.ToString()); }
        catch (Exception e) { Log("write_err " + Clean(e.Message)); }
    }

    static double Med(List<double> l)
    {
        if (l.Count == 0) return -1;
        var a = l.OrderBy(x => x).ToArray();
        return a[a.Length / 2];
    }
}
