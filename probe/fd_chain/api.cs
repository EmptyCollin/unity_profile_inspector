using System;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

// 目标：把 FrameDebuggerUtility 的完整能力面摊开，回答两件事
//   一 9 条是不是顶层树节点、子级绘制项怎么取（limit 是否可放大）
//   二 为何只有最后一条能解析出数据
var sb = new StringBuilder();
const BindingFlags SF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
const BindingFlags IF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;

Type util = null, dataType = null, evtType = null;
foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
{
    Type[] ts; try { ts = a.GetTypes(); } catch (Exception) { continue; }
    util = util ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerUtility");
    dataType = dataType ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerEventData");
    evtType = evtType ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerEvent" && x.FullName.Contains("FrameDebugger"));
}

sb.Append("== util 全部静态属性（含可写性）==").Append(Environment.NewLine);
foreach (var p in util.GetProperties(SF))
    sb.Append("  ").Append(p.PropertyType.Name).Append(" ").Append(p.Name)
      .Append(p.CanRead ? " r" : " -").Append(p.CanWrite ? " w" : " -").Append(Environment.NewLine);

sb.Append("== util 全部静态方法 ==").Append(Environment.NewLine);
foreach (var m in util.GetMethods(SF).Where(x => !x.IsSpecialName))
    sb.Append("  ").Append(m.ReturnType.Name).Append(" ").Append(m.Name).Append("(")
      .Append(string.Join(", ", m.GetParameters().Select(p =>
          (p.IsOut ? "out " : p.ParameterType.IsByRef ? "ref " : "") + p.ParameterType.Name + " " + p.Name)))
      .Append(")").Append(Environment.NewLine);

sb.Append("== limit 可写性实测 ==").Append(Environment.NewLine);
var lim = util.GetProperty("limit", SF);
sb.Append("  CanWrite=").Append(lim.CanWrite).Append(" 当前=").Append(Convert.ToString(lim.GetValue(null, null))).Append(Environment.NewLine);
if (lim.CanWrite)
{
    try { lim.SetValue(null, 4000, null); sb.Append("  已设为 4000 -> 回读 ").Append(Convert.ToString(lim.GetValue(null, null))).Append(Environment.NewLine); }
    catch (Exception e) { sb.Append("  赋值异常 ").Append((e.InnerException ?? e).Message).Append(Environment.NewLine); }
}

// FrameDebuggerEventData 是 struct 还是 class？决定反射 out 参数怎么取回
sb.Append("== 数据类型 ==").Append(Environment.NewLine);
sb.Append("  FrameDebuggerEventData IsValueType=").Append(dataType.IsValueType)
  .Append("  FrameDebuggerEvent IsValueType=").Append(evtType.IsValueType).Append(Environment.NewLine);
sb.Append("  EventData 字段: ").Append(string.Join(", ", dataType.GetFields(IF).Select(f => f.Name))).Append(Environment.NewLine);

// 事件结构体的字段构成（m_Obj 类型决定能否拿到 GameObject）
sb.Append("  Event 字段类型: ").Append(string.Join(", ", evtType.GetFields(IF).Select(f => f.Name + ":" + f.FieldType.Name))).Append(Environment.NewLine);

sb.Append("== 重开抓取（SetEnabled 唯一入口）==").Append(Environment.NewLine);
try { util.GetMethod("SetEnabled", SF).Invoke(null, new object[] { true, 0 }); sb.Append("  SetEnabled(true,0) 已调用").Append(Environment.NewLine); }
catch (Exception e) { sb.Append("  异常 ").Append((e.InnerException ?? e).Message).Append(Environment.NewLine); }
sb.Append("  同调用 enabled=").Append(typeof(UnityEngine.FrameDebugger).GetProperty("enabled", SF).GetValue(null, null));
sb.Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" count=").Append(Convert.ToString(util.GetProperty("count", SF).GetValue(null, null)));
sb.Append(" limit=").Append(Convert.ToString(lim.GetValue(null, null))).Append(Environment.NewLine);
sb.Append("  frame=").Append(Time.frameCount).Append(" rendered=").Append(Time.renderedFrameCount);
return sb.ToString();
