using System;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

var sb = new StringBuilder();
const BindingFlags SF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
const BindingFlags IF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
Type win = null, util = null;
foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
{
    Type[] ts; try { ts = a.GetTypes(); } catch (Exception) { continue; }
    win = win ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerWindow");
    util = util ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerUtility");
}

Func<string, bool> hit = n =>
{
    string l = n.ToLower();
    return l.Contains("pause") || l.Contains("resume") || l.Contains("play") || l.Contains("take")
        || l.Contains("step") || l.Contains("capture") || l.Contains("currentframe");
};

sb.Append("== 当前状态 ==").Append(Environment.NewLine);
sb.Append("  enabled=").Append(typeof(UnityEngine.FrameDebugger).GetProperty("enabled", BindingFlags.Public | BindingFlags.Static).GetValue(null, null));
sb.Append(" isPlaying=").Append(Application.isPlaying);
sb.Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" frame=").Append(Time.frameCount);
sb.Append(" rendered=").Append(Time.renderedFrameCount);
sb.Append(" time=").Append(Time.time.ToString("F3")).Append(Environment.NewLine);
sb.Append("  util.count=").Append(Len(util, "count")).Append(" limit=").Append(Len(util, "limit")).Append(Environment.NewLine);

// 复用已打开的窗口实例，读它的实例字段（上一版把 FieldInfo 当 target 传，是错的）
object inst = null;
try { inst = win.GetMethod("OpenWindow", SF).Invoke(null, null); } catch (Exception e) { sb.Append("  OpenWindow 异常 ").Append((e.InnerException ?? e).GetType().Name).Append(Environment.NewLine); }
if (inst != null)
{
    sb.Append("== 窗口实例中与取帧/暂停相关的成员 ==").Append(Environment.NewLine);
    foreach (var f in win.GetFields(SF | IF).Where(x => hit(x.Name) && !x.IsLiteral))
    {
        string v = "ERR";
        try { v = Convert.ToString(f.GetValue(f.IsStatic ? null : inst)); } catch (Exception) { }
        sb.Append("  字段 ").Append(f.Name).Append(" : ").Append(f.FieldType.Name).Append(" = ").Append(Cut(v, 40));
        sb.Append(f.IsStatic ? " [static]" : " [inst]").Append(Environment.NewLine);
    }
    foreach (var m in win.GetMethods(SF | IF).Where(x => !x.IsSpecialName && hit(x.Name)))
        sb.Append("  方法 ").Append(m.Name).Append("(")
          .Append(string.Join(",", m.GetParameters().Select(p => p.ParameterType.Name))).Append(") -> ")
          .Append(m.ReturnType.Name).Append(m.IsStatic ? " [static]" : " [inst]").Append(Environment.NewLine);
}

// 游戏逻辑是否真在跑：用场景里会动的物体做独立判据，而不是只看帧号
try
{
    var mover = GameObject.Find("Mover");
    sb.Append("  判活 Mover pos=").Append(mover != null ? mover.transform.position.ToString() : "找不到").Append(Environment.NewLine);
}
catch (Exception e) { sb.Append("  判活异常 ").Append(e.GetType().Name).Append(Environment.NewLine); }

// 标准暂停开关：这是"手动继续"在程序侧的等价物
sb.Append("== 解除暂停 ==").Append(Environment.NewLine);
try { EditorApplication.isPaused = false; sb.Append("  isPaused=false 已执行").Append(Environment.NewLine); }
catch (Exception e) { sb.Append("  赋值异常 ").Append(e.GetType().Name).Append(Environment.NewLine); }
sb.Append("  复核 isPaused=").Append(EditorApplication.isPaused).Append(" frame=").Append(Time.frameCount);
sb.Append(" util.count=").Append(Len(util, "count")).Append(Environment.NewLine);
return sb.ToString();

static string Len(Type t, string prop)
{
    try { return Convert.ToString(t.GetProperty(prop, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static).GetValue(null, null)); }
    catch (Exception e) { return "ERR:" + e.GetType().Name; }
}
static string Cut(string s, int n) { if (string.IsNullOrEmpty(s)) return ""; s = s.Replace("\n", " "); return s.Length > n ? s.Substring(0, n) : s; }
