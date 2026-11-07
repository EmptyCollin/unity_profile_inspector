using System;
using System.Collections;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

// 目的：搞清 9 行 SetPass 分组下面的逐笔绘制项，能否经树结构取得
// children 是 TreeViewItem 的公开字段（不是属性），这里属性与字段两种都查
var sb = new StringBuilder();
const BindingFlags SF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
const BindingFlags IF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
const BindingFlags PA = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;

Type util = null, win = null, treeT = null, itemT = null;
foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
{
    Type[] ts; try { ts = a.GetTypes(); } catch (Exception) { continue; }
    util = util ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerUtility");
    win = win ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerWindow");
    treeT = treeT ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerTreeView");
    itemT = itemT ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerTreeViewItem");
}
sb.Append("enabled=").Append(typeof(UnityEngine.FrameDebugger).GetProperty("enabled", SF).GetValue(null, null));
sb.Append(" count=").Append(Convert.ToString(util.GetProperty("count", SF).GetValue(null, null))).Append(Environment.NewLine);

object w = Resources.FindObjectsOfTypeAll(win)[0];
object tree = win.GetField("m_TreeView", IF).GetValue(w);
object ds = treeT.GetField("m_DataSource", IF).GetValue(tree);

sb.Append("== TreeViewItem 字段 ==").Append(Environment.NewLine);
foreach (var f in itemT.GetFields(PA).Where(x => !x.IsLiteral))
    sb.Append("  ").Append(f.Name).Append(" : ").Append(f.FieldType.Name).Append(Environment.NewLine);

try { ds.GetType().GetMethod("FetchData", IF)?.Invoke(ds, null); sb.Append("FetchData 已调用").Append(Environment.NewLine); }
catch (Exception e) { sb.Append("FetchData 异常 ").Append((e.InnerException ?? e).GetType().Name).Append(Environment.NewLine); }

object root = null;
try { root = ds.GetType().GetField("m_RootItem", IF).GetValue(ds); } catch (Exception) { }
sb.Append("root=").Append(root == null ? "NULL" : root.GetType().Name).Append(Environment.NewLine);
if (root == null) return sb.ToString();

// 取子项集合：先查属性再查字段，逐级往基类找
Func<object, IList> kids = it =>
{
    for (Type t = it.GetType(); t != null; t = t.BaseType)
    {
        var p = t.GetProperty("children", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
        if (p != null) { try { return p.GetValue(it, null) as IList; } catch (Exception) { } }
        var f = t.GetField("children", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
        if (f != null) { try { return f.GetValue(it) as IList; } catch (Exception) { } }
    }
    return null;
};

Func<object, string> dump = it => string.Join(" ", it.GetType()
    .GetFields(PA).Where(x => !x.IsLiteral).Select(f =>
    {
        object v = null; try { v = f.GetValue(it); } catch (Exception) { }
        if (v == null) return f.Name + "=null";
        if (v is Array av) return f.Name + "=[" + av.Length + "]";
        if (f.Name == "children") return null;
        return f.Name + "=" + Cut(v.ToString(), 22);
    }).Where(x => x != null).Take(7));

var lvl1 = kids(root);
sb.Append("根子项数=").Append(lvl1 == null ? -1 : lvl1.Count).Append("（对应 SetPass 分组）").Append(Environment.NewLine);
if (lvl1 == null) return sb.ToString();

int totalLeaves = 0;
for (int i = 0; i < lvl1.Count; i++)
{
    object k = lvl1[i];
    var lvl2 = kids(k);
    int c2 = lvl2 == null ? 0 : lvl2.Count;
    totalLeaves += c2;
    sb.Append("  组#").Append(i).Append(" 子项=").Append(c2).Append("  ").Append(dump(k)).Append(Environment.NewLine);
    for (int j = 0; j < c2 && j < 3; j++)
    {
        object leaf = lvl2[j];
        var lvl3 = kids(leaf);
        sb.Append("      子#").Append(j).Append(" 更深=").Append(lvl3 == null ? 0 : lvl3.Count)
          .Append("  ").Append(dump(leaf)).Append(Environment.NewLine);
    }
}
sb.Append("== 汇总 == 分组=").Append(lvl1.Count).Append(" 第二层节点合计=").Append(totalLeaves).Append(Environment.NewLine);
sb.Append("对照：get_performance 的 setPassCalls=9 / drawCalls=51");
return sb.ToString();

static string Cut(string s, int n)
{
    if (string.IsNullOrEmpty(s)) return "-";
    s = s.Replace("\n", " ").Replace("\r", " ");
    return s.Length > n ? s.Substring(0, n) : s;
}
