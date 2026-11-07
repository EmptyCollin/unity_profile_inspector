// probe/fd_final.cs —— Frame Debugger 可取性的最后一问
//
// 已排除（本轮实测）：
//   util.SetEnabled(true,0) 调用成功但 FrameDebugger.enabled 仍为 False；帧号正常推进、
//   场景确在渲染（get_performance drawCalls=127），util.count 依然 0
//   ⇒ "播放停摆导致无帧可抓"不成立；开关没有真正生效才是原因。
//   反射给 FrameDebugger.enabled 赋值 ⇒ 该属性只有 getter。
// 本轮只试最后一条路：帧调试窗口自己的启用逻辑（EnableFrameDebugger / 菜单入口）。
// 读取与启用放在同一次调用里，避免跨调用的状态与重载问题（I9）。
using System;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

return UpiFdFinal.Go();

public static class UpiFdFinal
{
    const BindingFlags SF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
    const BindingFlags FI = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;

    public static string Go()
    {
        var sb = new StringBuilder();
        Type util = null, win = null, evt = null;
        foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
        {
            Type[] ts; try { ts = a.GetTypes(); } catch (Exception) { continue; }
            if (util == null) util = ts.FirstOrDefault(x => x.Name == "FrameDebuggerUtility");
            if (win == null) win = ts.FirstOrDefault(x => x.Name == "FrameDebuggerWindow");
            if (evt == null) evt = ts.FirstOrDefault(x => x.Name == "FrameDebuggerEventData");
        }
        sb.Append("util=").Append(util != null).Append(" win=").Append(win != null).Append(" evt=").Append(evt != null);
        sb.Append(Environment.NewLine);
        if (util == null || win == null) return sb.ToString();

        sb.Append("isPlaying=").Append(Application.isPlaying).Append(" frame=").Append(Time.frameCount);
        sb.Append(Environment.NewLine);
        sb.Append("启用前 count=").Append(Count(util)).Append(Environment.NewLine);

        // 路径 A：窗口实例 + 它自己的 EnableFrameDebugger
        bool opened = false;
        try
        {
            object inst = null;
            foreach (var p in win.GetProperties(SF))
            {
                if (!p.Name.Equals("P", StringComparison.Ordinal) || !p.CanRead) continue;
                try { inst = p.GetValue(null, null); } catch (Exception) { }
                if (inst != null) break;
            }
            if (inst == null)
            {
                inst = typeof(EditorWindow).GetMethod("GetWindow", new[] { typeof(Type), typeof(bool), typeof(string), typeof(bool) })
                                           .Invoke(null, new object[] { win, false, "Frame Debugger", true });
                var show = typeof(EditorWindow).GetMethod("Show", FI);
                if (show != null && inst != null) show.Invoke(inst, new object[] { false });
            }
            opened = inst != null;
            sb.Append("A 窗口实例=").Append(opened);
            if (opened)
            {
                var en = win.GetMethod("EnableFrameDebugger", SF);
                if (en == null) sb.Append(" 无静态 EnableFrameDebugger");
                else
                {
                    en.Invoke(null, null);
                    sb.Append(" 已调用 EnableFrameDebugger");
                }
                sb.Append(" 启用后 count=").Append(Count(util)).Append(Environment.NewLine);
            }
        }
        catch (Exception e)
        {
            sb.Append("A 异常 ").Append(e.GetType().Name).Append(": ").Append(Cut(e));
        }
        sb.Append(Environment.NewLine);

        // 读一次当前帧（同调用内，避免跨调用状态问题）
        sb.Append(Read(util, evt));
        return sb.ToString();
    }

    static string Count(Type util)
    {
        try
        {
            var p = util.GetProperty("count", SF);
            return Convert.ToString(p.GetValue(null, null));
        }
        catch (Exception e) { return "ERR:" + e.GetType().Name; }
    }

    static string Read(Type util, Type evt)
    {
        var sb = new StringBuilder();
        int c = 0;
        try { c = Convert.ToInt32(util.GetProperty("count", SF).GetValue(null, null)); } catch (Exception) { }
        sb.Append("读取: count=").Append(c).Append(Environment.NewLine);
        if (c <= 0)
        {
            sb.Append("结论: 缓冲仍为空 —— 无法以程序方式取得当帧渲染数据");
            return sb.ToString();
        }
        try
        {
            var arr = util.GetMethod("GetFrameEvents", SF).Invoke(null, null) as Array;
            sb.Append("events=").Append(arr == null ? -1 : arr.Length).Append(Environment.NewLine);
            var ged = util.GetMethod("GetFrameEventData", SF);
            var geo = util.GetMethod("GetFrameEventObject", SF);
            var fD = evt.GetField("m_DrawCallCount", FI);
            var fS = evt.GetField("m_RealShaderName", FI);
            var fP = evt.GetField("m_PassName", FI);
            string[] causes = null;
            var bcm = util.GetMethod("GetBatchBreakCauseStrings", SF);
            if (bcm != null) { try { causes = bcm.Invoke(null, null) as string[]; } catch (Exception) { } }
            int shown = 0, withObj = 0;
            for (int i = 0; i < arr.Length && shown < 8; i++)
            {
                object inst = Activator.CreateInstance(evt);
                bool ok = (bool)ged.Invoke(null, new object[] { arr.GetValue(i), inst });
                if (!ok) continue;
                int dc = Convert.ToInt32(fD.GetValue(inst) ?? 0);
                if (dc <= 0) continue;
                object go = geo == null ? null : geo.Invoke(null, new object[] { arr.GetValue(i) });
                string nm = go is GameObject g ? g.name : (go as Component)?.name ?? "";
                if (go != null) withObj++;
                string cs = (causes != null && i < causes.Length) ? Cut(causes[i], 40) : "?";
                sb.Append("  #").Append(i).Append(" dc=").Append(dc).Append(" obj=[").Append(nm)
                  .Append("] shader=").Append(Cut((string)fS.GetValue(inst), 40))
                  .Append(" pass=").Append(Cut((string)fP.GetValue(inst), 16))
                  .Append(" cause=").Append(cs).Append(Environment.NewLine);
                shown++;
            }
            sb.Append("结论: 可读。绘制项=").Append(shown).Append(" 可定位物体=").Append(withObj);
        }
        catch (Exception e) { sb.Append("读取异常 ").Append(e.GetType().Name).Append(": ").Append(Cut(e)); }
        return sb.ToString();
    }

    static string Cut(object s, int n = 120)
    {
        string v = Convert.ToString(s) ?? "";
        v = v.Replace("\n", " ").Replace("\r", " ");
        return v.Length > n ? v.Substring(0, n) : v;
    }
}
