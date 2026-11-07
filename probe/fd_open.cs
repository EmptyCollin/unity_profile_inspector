// probe/fd_open.cs —— 收口测试：抓取关闭、帧推进验证、经官方入口开窗口触发抓取
// 三个已知前提（本轮实测）：
//   1. FrameDebugger.enabled 只有 getter ⇒ 反射设置无效，唯一开关是 util.SetEnabled(bool,int)
//   2. util.GetFrameEvents() 在缓冲为空时会抛异常 ⇒ 必须先查 util.count
//   3. 本会话 frame/rendered 两次读数相同（7625 未推进）⇒ 无新渲染帧就没有可抓的帧
using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

UpiFdOpen.Run();
return UpiFdOpen.Out;

public static class UpiFdOpen
{
    public static string Out = "";
    const string DIR = @"D:/unity_profile_inspector/runs/_probe";
    const string STATE = DIR + "/fd_state.txt";

    public static void Run()
    {
        string action = "";
        try { if (File.Exists(STATE)) { action = File.ReadAllText(STATE).Trim(); File.Delete(STATE); } } catch (Exception) { }

        const BindingFlags SF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
        Type util = null, win = null;
        foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
        {
            Type[] ts; try { ts = a.GetTypes(); } catch (Exception) { continue; }
            util = util ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerUtility");
            win = win ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerWindow");
        }
        if (util == null) { Out = "util NULL"; return; }

        var setEnabled = util.GetMethod("SetEnabled", SF);
        var countProp = util.GetProperty("count", SF);
        var getEvents = util.GetMethod("GetFrameEvents", SF);
        var getEventData = util.GetMethod("GetFrameEventData", SF);
        var getEventObj = util.GetMethod("GetFrameEventObject", SF);
        var causes = util.GetMethod("GetBatchBreakCauseStrings", SF);
        Type dt = null;
        foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
        {
            Type[] ts; try { ts = a.GetTypes(); } catch (Exception) { continue; }
            dt = dt ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerEventData");
        }

        var sb = new StringBuilder();
        sb.Append("act=").Append(action);
        sb.Append(" frame=").Append(Time.frameCount);
        sb.Append(" rendered=").Append(Time.renderedFrameCount);

        if (action == "cleanup")
        {
            try { setEnabled.Invoke(null, new object[] { false, 0 }); sb.Append(" set(false) ok"); }
            catch (Exception e) { sb.Append(" set(false) ERR ").Append(Sh(e)); }
            sb.Append(" count=").Append(Count(countProp));
            Out = sb.ToString(); return;
        }

        if (action == "unpause")
        {
            try { EditorApplication.isPaused = false; sb.Append(" isPaused->").Append(EditorApplication.isPaused); }
            catch (Exception e) { sb.Append(" unpause ERR ").Append(Sh(e)); }
            try { setEnabled.Invoke(null, new object[] { true, 0 }); sb.Append(" set(true) ok"); }
            catch (Exception e) { sb.Append(" set(true) ERR ").Append(Sh(e)); }
            sb.Append(" count=").Append(Count(countProp));
            Out = sb.ToString(); return;
        }

        if (action == "openwin")
        {
            var m = win == null ? null : win.GetMethod("OpenWindowAndToggleEnabled", SF);
            if (m == null) { sb.Append(" OpenWindowAndToggleEnabled 不存在"); Out = sb.ToString(); return; }
            try
            {
                m.Invoke(null, null);
                sb.Append(" 已调用 count=").Append(Count(countProp));
            }
            catch (Exception e) { sb.Append(" 调用 ERR ").Append(Sh(e)); }
            Out = sb.ToString(); return;
        }

        if (action == "read")
        {
            sb.Append(" count=").Append(Count(countProp));
            int c = SafeInt(Count(countProp));
            if (c > 0)
            {
                try
                {
                    var arr = getEvents.Invoke(null, null) as Array;
                    sb.Append(" events=").Append(arr == null ? -1 : arr.Length);
                    int obj = 0, sh = 0, dcSum = 0;
                    const BindingFlags IFi = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
                    var fD = dt.GetField("m_DrawCallCount", IFi);
                    var fS = dt.GetField("m_RealShaderName", IFi);
                    var fP = dt.GetField("m_PassName", IFi);
                    int shown = 0;
                    for (int i = 0; i < arr.Length && shown < 4; i++)
                    {
                        object inst; try { inst = Activator.CreateInstance(dt); } catch (Exception) { break; }
                        bool ok = false;
                        try { ok = (bool)getEventData.Invoke(null, new object[] { arr.GetValue(i), inst }); } catch (Exception) { }
                        if (!ok) continue;
                        object go = null;
                        try { go = getEventObj.Invoke(null, new object[] { arr.GetValue(i) }); } catch (Exception) { }
                        if (go != null) obj++;
                        string nm = go is GameObject g ? g.name : (go as Component)?.name ?? "";
                        string shader = (string)(fS.GetValue(inst) ?? "");
                        string pass = (string)(fP.GetValue(inst) ?? "");
                        int dc = Convert.ToInt32(fD.GetValue(inst) ?? 0);
                        dcSum += dc;
                        if (shader.Length > 0) sh++;
                        if (nm.Contains("FDDirect") || nm.Contains("FDExtra") || dc > 0)
                        {
                            sb.Append(" | #").Append(i).Append(" ").Append(Sh(nm, 22))
                              .Append(" d=").Append(dc).Append(" ").Append(Sh(Tail(shader), 26))
                              .Append("/").Append(Sh(pass, 14));
                            shown++;
                        }
                    }
                    sb.Append(" obj=").Append(obj).Append(" shader=").Append(sh).Append(" drawSum=").Append(dcSum);
                }
                catch (Exception e) { sb.Append(" GetFrameEvents ERR ").Append(Sh(e)); }
            }
            else
            {
                sb.Append(" events=跳过(缓冲空，读取会抛异常)");
            }

            try
            {
                var cs = causes.Invoke(null, null) as string[];
                int nonEmpty = cs == null ? 0 : cs.Count(x => !string.IsNullOrEmpty(x) && x != "Unknown reason.");
                sb.Append(" causes_total=").Append(cs == null ? 0 : cs.Length)
                  .Append(" causes_非通用=").Append(nonEmpty);
                if (cs != null)
                {
                    var top = cs.Where(x => !string.IsNullOrEmpty(x) && x != "Unknown reason.").Take(3).ToArray();
                    foreach (var t in top) sb.Append(" <").Append(Sh(t, 40)).Append(">");
                }
            }
            catch (Exception e) { sb.Append(" causes ERR ").Append(Sh(e)); }

            sb.Append(" isPlaying=").Append(Application.isPlaying)
              .Append(" paused=").Append(EditorApplication.isPaused)
              .Append(" focused=").Append(EditorWindow.focusedWindow == null ? "null" : Sh(EditorWindow.focusedWindow.titleContent.text, 14));
            Out = sb.ToString(); return;
        }
        Out = sb.ToString();
    }

    static object Count(PropertyInfo p)
    {
        try { return p.GetValue(null, null); } catch (Exception e) { return "ERR:" + e.GetType().Name; }
    }

    static int SafeInt(object o) { int v; return int.TryParse(Convert.ToString(o), out v) ? v : 0; }
    static string Tail(string s) { if (string.IsNullOrEmpty(s)) return ""; var p = s.Split('/'); return p[p.Length - 1]; }
    static string Sh(string s, int n = 60)
    {
        if (string.IsNullOrEmpty(s)) return "";
        s = s.Replace("\n", " ").Replace("\r", " ");
        return s.Length > n ? s.Substring(0, n) : s;
    }
    static string Sh(Exception e) { var x = e.InnerException ?? e; return x.GetType().Name + ":" + Sh(x.Message, 70); }
}
