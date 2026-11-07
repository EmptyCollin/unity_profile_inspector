using System;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

// 找"完整一帧的事件数组"到底存在哪里
// 线索：DetailsView.Initialize(Int32, FrameDebuggerEvent[]) 与 DrawEventDetails(Rect, FrameDebuggerEvent[], Boolean)
//      说明 UI 持有的是一个数组；窗口字段只看到 m_FrameEventsHash，没看到数组本体
var sb = new StringBuilder();
const BindingFlags SF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
const BindingFlags ALL = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;

Type win = null, util = null, detailsT = null, treeT = null, itemT = null, dsT = null;
foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
{
    Type[] ts; try { ts = a.GetTypes(); } catch (Exception) { continue; }
    win = win ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerWindow");
    util = util ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerUtility");
    detailsT = detailsT ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerEventDetailsView");
    treeT = treeT ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerTreeView");
    itemT = itemT ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerTreeViewItem");
    dsT = dsT ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerTreeViewDataSource");
}

// 所有名字含 FrameDebugger 的类型 —— 数据本体可能住在别的类里
sb.Append("== 含 FrameDebugger 的类型 ==").Append(Environment.NewLine);
foreach (var t in AppDomain.CurrentDomain.GetAssemblies().SelectMany(a => { try { return a.GetTypes(); } catch (Exception) { return Type.EmptyTypes; } })
             .Where(t => t.Name.Contains("FrameDebugger")).OrderBy(t => t.FullName))
{
    int arrFields = t.GetFields(SF).Count(f => typeof(Array).IsAssignableFrom(f.FieldType));
    sb.Append("  ").Append(t.FullName).Append("  静态数组字段=").Append(arrFields)
      .Append(t.IsAbstract ? " [abstract]" : "").Append(Environment.NewLine);
    foreach (var f in t.GetFields(SF).Where(f => typeof(Array).IsAssignableFrom(f.FieldType)))
    {
        int len = -1;
        try { var v = f.GetValue(null); if (v is Array av) len = av.Length; } catch (Exception) { }
        sb.Append("      static ").Append(f.Name).Append(" : ").Append(f.FieldType.Name).Append(" len=").Append(len).Append(Environment.NewLine);
    }
}

// 实例字段逐个描述（数组报长度）
sb.Append("== 实例字段实况 ==").Append(Environment.NewLine);
object inst = null;
try { inst = win.GetMethod("OpenWindow", SF).Invoke(null, null); } catch (Exception e) { sb.Append("OpenWindow 异常").Append(Environment.NewLine); }
Report(sb, "窗口", win, inst, ALL);

object tree = null, details = null;
try { tree = win.GetField("m_TreeView", ALL).GetValue(inst); } catch (Exception) { }
try { details = win.GetField("m_EventDetailsView", ALL).GetValue(inst); } catch (Exception) { }
if (tree != null)
{
    Report(sb, "TreeView", treeT, tree, ALL);
    object ds = null;
    try { ds = treeT.GetField("m_DataSource", ALL).GetValue(tree); } catch (Exception) { }
    if (ds != null)
    {
        Report(sb, "DataSource", ds.GetType(), ds, ALL);
        sb.Append("  DataSource 方法:").Append(Environment.NewLine);
        foreach (var m in ds.GetType().GetMethods(ALL).Where(x => !x.IsSpecialName && !x.Name.EndsWith("_Injected") && x.DeclaringType == ds.GetType()))
            sb.Append("    ").Append(m.Name).Append("(").Append(string.Join(",", m.GetParameters().Select(p => p.ParameterType.Name))).Append(") -> ").Append(m.ReturnType.Name).Append(Environment.NewLine);
    }
    object item = null;
    try { item = treeT.GetMethod("GetSelectedTreeViewItem", ALL).Invoke(tree, null); } catch (Exception) { }
    if (item != null) Report(sb, "选中树项", item.GetType(), item, ALL);
    else sb.Append("  无选中树项（SetSelection 后再取）").Append(Environment.NewLine);
}
if (details != null) Report(sb, "DetailsView", detailsT, details, ALL);

// DetailsView.Initialize(int, FrameDebuggerEvent[]) —— 传我们手上那个数组，让它内部把明细建起来
var arr = util.GetMethod("GetFrameEvents", SF).Invoke(null, null) as Array;
sb.Append("== 尝试驱动 DetailsView ==").Append(Environment.NewLine);
try
{
    var init = detailsT.GetMethod("Initialize", ALL);
    init.Invoke(details, new object[] { 0, arr });
    sb.Append("  Initialize(0, events) 成功").Append(Environment.NewLine);
    Report(sb, "DetailsView(驱动后)", detailsT, details, ALL);
}
catch (Exception e) { sb.Append("  Initialize 异常 ").Append((e.InnerException ?? e).GetType().Name).Append(": ").Append(Cut((e.InnerException ?? e).Message, 80)).Append(Environment.NewLine); }

return sb.ToString();

static void Report(StringBuilder sb, string label, Type t, object inst, BindingFlags b)
{
    if (t == null || inst == null) { sb.Append("  ").Append(label).Append(": 无法取得").Append(Environment.NewLine); return; }
    sb.Append("  -- ").Append(label).Append(" (").Append(t.Name).Append(") --").Append(Environment.NewLine);
    foreach (var f in t.GetFields(b).Where(x => !x.IsLiteral))
    {
        object v;
        try { v = f.GetValue(f.IsStatic ? null : inst); } catch (Exception) { continue; }
        if (v == null) continue;
        string desc;
        if (v is Array av) desc = f.FieldType.Name + " len=" + av.Length;
        else if (v is string sv) desc = "str \"" + Cut(sv, 24) + "\"";
        else if (v is int || v is long || v is bool || v is float || v is double) desc = f.FieldType.Name + " " + v;
        else desc = f.FieldType.Name;
        if (!(desc.Contains("len=") || v is int || v is bool || v is string || v is long || v is float || v is double)) continue;
        sb.Append("      ").Append(f.Name).Append(" = ").Append(desc).Append(Environment.NewLine);
    }
}

static string Cut(string s, int n)
{
    if (string.IsNullOrEmpty(s)) return "-";
    s = s.Replace("\n", " ").Replace("\r", " ");
    return s.Length > n ? s.Substring(0, n) : s;
}
