using System;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEditor.IMGUI.Controls;
using UnityEngine;

// 最后一问：9 行是顶层分组，子级真实绘制项能否物化出来
// 手段：展开树（ExpAll / expandedIDs）+ 强制重绘（RepaintAll / Repaint），然后重读事件
var sb = new StringBuilder();
const BindingFlags SF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
const BindingFlags ALL = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
Type util = null, win = null, dataType = null, evtType = null;
foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
{
    Type[] ts; try { ts = a.GetTypes(); } catch (Exception) { continue; }
    util = util ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerUtility");
    win = win ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerWindow");
    dataType = dataType ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerEventData");
    evtType = evtType ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerEvent" && x.FullName.Contains("FrameDebugger"));
}
var enProp = typeof(UnityEngine.FrameDebugger).GetProperty("enabled", SF);
var cp = util.GetProperty("count", SF);
var lp = util.GetProperty("limit", SF);
Func<int> cnt = () => { try { return Convert.ToInt32(cp.GetValue(null, null)); } catch (Exception) { return -1; } };

sb.Append("起点 enabled=").Append(enProp.GetValue(null, null)).Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" count=").Append(cnt()).Append(" limit=").Append(Convert.ToString(lp.GetValue(null, null))).Append(Environment.NewLine);

object inst = null;
try { inst = win.GetMethod("OpenWindow", SF).Invoke(null, null); } catch (Exception e) { sb.Append("OpenWindow 异常 ").Append((e.InnerException ?? e).GetType().Name).Append(Environment.NewLine); }
sb.Append("窗口实例=").Append(inst != null ? "有" : "无").Append(Environment.NewLine);

object tree = null;
var tf = win.GetField("m_TreeView", ALL);
if (tf != null && inst != null) tree = tf.GetValue(inst);
sb.Append("TreeView=").Append(tree == null ? "无" : tree.GetType().Name).Append(Environment.NewLine);

if (tree != null)
{
    sb.Append("TreeView 可用展开/重绘入口:").Append(Environment.NewLine);
    foreach (var m in tree.GetType().GetMethods(ALL).Where(x => !x.IsSpecialName
                 && (x.Name.Contains("Exp") || x.Name.Contains("Expand") || x.Name.Contains("Reload")
                     || x.Name.Contains("Build") || x.Name.Contains("Repaint"))))
        sb.Append("    ").Append(m.Name).Append("(")
          .Append(string.Join(",", m.GetParameters().Select(p => p.ParameterType.Name))).Append(")").Append(m.IsStatic ? " [s]" : "").Append(Environment.NewLine);
    // 试 ExpAll / Reload
    foreach (var nm in new[] { "ExpAll", "Reload", "Frame" })
    {
        var m = tree.GetType().GetMethod(nm, ALL);
        if (m == null) { sb.Append("    无 ").Append(nm).Append(Environment.NewLine); continue; }
        try { m.Invoke(tree, m.GetParameters().Length == 0 ? null : new object[m.GetParameters().Length]); sb.Append("    ").Append(nm).Append(" 已调用").Append(Environment.NewLine); }
        catch (Exception e) { sb.Append("    ").Append(nm).Append(" 异常 ").Append((e.InnerException ?? e).GetType().Name).Append(Environment.NewLine); }
    }
    try { sb.Append("    rowObjs=").Append(Convert.ToString(tree.GetType().GetProperty("rowObjs", ALL).GetValue(tree, null))); } catch (Exception) { sb.Append("    rowObjs 不可读").Append(Environment.NewLine); }
}

// 强制重绘（窗口自己那套）
try { win.GetMethod("RepaintAll", SF).Invoke(null, null); sb.Append("RepaintAll 已调用").Append(Environment.NewLine); }
catch (Exception e) { sb.Append("RepaintAll 异常 ").Append((e.InnerException ?? e).GetType().Name).Append(Environment.NewLine); }
try { typeof(EditorWindow).GetMethod("Repaint", new[] { typeof(bool) }).Invoke(inst, new object[] { true }); sb.Append("窗口 Repaint 已调用").Append(Environment.NewLine); }
catch (Exception e) { sb.Append("Repaint 异常 ").Append((e.InnerException ?? e).GetType().Name).Append(Environment.NewLine); }

sb.Append("重绘后 count=").Append(cnt()).Append(" limit=").Append(Convert.ToString(lp.GetValue(null, null))).Append(Environment.NewLine);

// 若 count 仍 9，逐条把 infoName 与类型打全，确认它们是分组还是绘制项
var arr = util.GetMethod("GetFrameEvents", SF).Invoke(null, null) as Array;
int n = arr == null ? 0 : arr.Length;
sb.Append("事件数=").Append(n).Append(Environment.NewLine);
var ged = util.GetMethod("GetFrameEventData", SF);
var gnm = util.GetMethod("GetFrameEventInfoName", SF);
var geo = util.GetMethod("GetFrameEventObject", SF);
var fType = evtType.GetField("m_Type", ALL);
var fDc = dataType.GetField("m_DrawCallCount", ALL);
for (int i = 0; i < n; i++)
{
    bool ok = false; object di = Activator.CreateInstance(dataType);
    try { ok = (bool)ged.Invoke(null, new object[] { i, di }); } catch (Exception) { }
    object go = null; try { go = geo.Invoke(null, new object[] { i }); } catch (Exception) { }
    string objn = go is GameObject g ? g.name : (go as Component)?.name ?? ((go == null || go is string) ? "-" : go.GetType().Name);
    sb.Append("  #").Append(i).Append(" type=").Append(Convert.ToString(fType.GetValue(arr.GetValue(i))))
      .Append(" 解析=").Append(ok).Append(" objType=").Append(objn)
      .Append(ok ? " dc=" + Convert.ToInt32(fDc.GetValue(di) ?? 0) : "")
      .Append(" name=").Append(Cut(Convert.ToString(gnm.Invoke(null, new object[] { i })), 46)).Append(Environment.NewLine);
}
return sb.ToString();

static string Cut(string s, int n) { if (string.IsNullOrEmpty(s)) return "-"; s = s.Replace("\n", " "); return s.Length > n ? s.Substring(0, n) : s; }
