using System;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

// 三件事：
// 一 enabled 属性是否有写入访问器，直接赋值能否真正开启抓取
// 二 那 9 条事件到底是什么，为什么只有最后一条能解析
// 三 按用户的做法，开启后解除暂停，让游戏继续出帧
var sb = new StringBuilder();
const BindingFlags SF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
const BindingFlags IF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;

Type util = null, win = null, dataType = null, evtType = null;
foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
{
    Type[] ts; try { ts = a.GetTypes(); } catch (Exception) { continue; }
    util = util ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerUtility");
    win = win ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerWindow");
    dataType = dataType ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerEventData");
    evtType = evtType ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerEvent" && x.FullName.Contains("FrameDebugger"));
}
var en = typeof(UnityEngine.FrameDebugger).GetProperty("enabled", SF);

sb.Append("== 一 enabled 访问器 ==").Append(Environment.NewLine);
sb.Append("  CanRead=").Append(en.CanRead).Append(" CanWrite=").Append(en.CanWrite);
sb.Append(" getter=").Append(en.GetMethod != null ? en.GetMethod.Name : "NULL");
sb.Append(" setter=").Append(en.SetMethod != null ? en.SetMethod.Name : "NULL").Append(Environment.NewLine);
sb.Append("  当前值=").Append(en.GetValue(null, null));
sb.Append(" isPaused=").Append(EditorApplication.isPaused).Append(" frame=").Append(Time.frameCount).Append(Environment.NewLine);

// 找同名属性的其他可见性变体，确认没有第二个入口
foreach (var p in typeof(UnityEngine.FrameDebugger).GetProperties(SF))
    sb.Append("  静态属性 ").Append(p.Name).Append(" CanWrite=").Append(p.CanWrite).Append(Environment.NewLine);

sb.Append("== 二 直接赋值 ==").Append(Environment.NewLine);
try
{
    en.SetValue(null, true, null);
    sb.Append("  赋值 true 未抛异常").Append(Environment.NewLine);
}
catch (Exception e)
{
    var inner = e.InnerException ?? e;
    sb.Append("  赋值异常 ").Append(inner.GetType().Name).Append(": ").Append(inner.Message).Append(Environment.NewLine);
}
sb.Append("  同调用回读 enabled=").Append(en.GetValue(null, null));
sb.Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" count=").Append(Convert.ToString(util.GetProperty("count", SF).GetValue(null, null))).Append(Environment.NewLine);

// 若赋值失败，回落到 SetEnabled 方法，并把方法完整签名打出来
if (!(bool)en.GetValue(null, null))
{
    foreach (var m in util.GetMethods(SF).Where(x => x.Name.Contains("SetEnabled") || x.Name.Contains("Enabled")))
        sb.Append("  候选方法 ").Append(m.Name).Append("(")
          .Append(string.Join(", ", m.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name))).Append(")")
          .Append(Environment.NewLine);
}

// 三 9 条事件的真实构成
sb.Append("== 三 事件构成 ==").Append(Environment.NewLine);
int count = 0;
try { count = Convert.ToInt32(util.GetProperty("count", SF).GetValue(null, null)); } catch (Exception) { }
sb.Append("  count=").Append(count);
try { sb.Append(" limit=").Append(Convert.ToString(util.GetProperty("limit", SF).GetValue(null, null))); } catch (Exception) { }
sb.Append(Environment.NewLine);
var arr = util.GetMethod("GetFrameEvents", SF).Invoke(null, null) as Array;
sb.Append("  arr=").Append(arr == null ? -1 : arr.Length).Append(Environment.NewLine);
if (arr != null)
{
    var ged = util.GetMethod("GetFrameEventData", SF);
    var fType = evtType.GetField("m_Type", IF);
    var fObj = evtType.GetField("m_Obj", IF);
    var fName = evtType.GetMethod("GetName", IF);
    var gnm = util.GetMethod("GetFrameEventInfoName", SF);
    var fDc = dataType.GetField("m_DrawCallCount", IF);
    for (int i = 0; i < arr.Length; i++)
    {
        object ev = arr.GetValue(i);
        string evName = "-", typeS = "-", objS = "-";
        try { if (fName != null) evName = Convert.ToString(fName.Invoke(ev, null)); } catch (Exception) { }
        try { if (fType != null) typeS = Convert.ToString(fType.GetValue(ev)); } catch (Exception) { }
        try { if (fObj != null) objS = (fObj.GetValue(ev) ?? "null").GetType().Name; } catch (Exception) { }
        bool ok = false;
        object inst = Activator.CreateInstance(dataType);
        try { ok = (bool)ged.Invoke(null, new object[] { i, inst }); } catch (Exception) { }
        int dc = ok ? Convert.ToInt32(fDc.GetValue(inst) ?? 0) : -1;
        string infoName = "-";
        try { infoName = Convert.ToString(gnm.Invoke(null, new object[] { i })); } catch (Exception) { }
        sb.Append("    #").Append(i).Append(" type=").Append(typeS).Append(" evName=").Append(Cut(evName, 30))
          .Append(" obj=").Append(objS).Append(" infoName=").Append(Cut(infoName, 34))
          .Append(" 解析=").Append(ok).Append(" dc=").Append(dc).Append(Environment.NewLine);
    }
}

sb.Append("== 四 按用户做法解除暂停 ==").Append(Environment.NewLine);
try { EditorApplication.isPaused = false; sb.Append("  isPaused=false 已执行").Append(Environment.NewLine); }
catch (Exception e) { sb.Append("  异常 ").Append(e.Message).Append(Environment.NewLine); }
sb.Append("  isPaused=").Append(EditorApplication.isPaused).Append(" enabled=").Append(en.GetValue(null, null))
  .Append(" frame=").Append(Time.frameCount);
return sb.ToString();

static string Cut(string s, int n)
{
    if (string.IsNullOrEmpty(s)) return "-";
    s = s.Replace("\n", " ").Replace("\r", " ");
    return s.Length > n ? s.Substring(0, n) : s;
}
