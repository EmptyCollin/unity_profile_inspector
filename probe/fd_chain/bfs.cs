using System;
using System.Collections;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

// 一次问清三件事：
//   一 树有几层、每层几个节点（9 个 SetPass 组下面有没有逐笔绘制项）
//   二 叶子节点上的 m_EventIndex 取值范围（若超过 8，说明事件不止 9 条）
//   三 用这些索引去 GetFrameEventData 能否读到真实绘制明细与 GameObject
var sb = new StringBuilder();
const BindingFlags SF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
const BindingFlags IF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
const BindingFlags PA = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;

Type util = null, win = null, treeT = null, dataType = null;
foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
{
    Type[] ts; try { ts = a.GetTypes(); } catch (Exception) { continue; }
    util = util ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerUtility");
    win = win ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerWindow");
    treeT = treeT ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerTreeView");
    dataType = dataType ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerEventData");
}
sb.Append("enabled=").Append(typeof(UnityEngine.FrameDebugger).GetProperty("enabled", SF).GetValue(null, null));
sb.Append(" count=").Append(Convert.ToString(util.GetProperty("count", SF).GetValue(null, null)));
sb.Append(" limit=").Append(Convert.ToString(util.GetProperty("limit", SF).GetValue(null, null))).Append(Environment.NewLine);

object w = Resources.FindObjectsOfTypeAll(win)[0];
object tree = win.GetField("m_TreeView", IF).GetValue(w);
object ds = treeT.GetField("m_DataSource", IF).GetValue(tree);
try { ds.GetType().GetMethod("FetchData", IF)?.Invoke(ds, null); } catch (Exception) { }
object root = ds.GetType().GetField("m_RootItem", IF).GetValue(ds);

FieldInfo chF = null;
for (Type t = root.GetType(); t != null && chF == null; t = t.BaseType)
    chF = t.GetField("children", BindingFlags.Public | BindingFlags.Instance);
sb.Append("children 字段=").Append(chF != null ? "找到" : "未找到").Append(Environment.NewLine);
Func<object, IList> kids = it =>
{
    for (Type t = it.GetType(); t != null; t = t.BaseType)
    {
        var f = t.GetField("children", BindingFlags.Public | BindingFlags.Instance);
        if (f != null) { try { return f.GetValue(it) as IList; } catch (Exception) { } }
    }
    return null;
};
Func<object, int> evtIdx = it =>
{
    var f = it.GetType().GetField("m_EventIndex", PA);
    if (f == null) return -99;
    try { return Convert.ToInt32(f.GetValue(it)); } catch (Exception) { return -98; }
};
Func<object, int> childCnt = it =>
{
    var f = it.GetType().GetField("m_ChildEventCount", PA);
    if (f == null) return -99;
    try { return Convert.ToInt32(f.GetValue(it)); } catch (Exception) { return -98; }
};

// 广度优先，最多 8 层，逐层报数
var level = new ArrayList { root };
var idxByLevel = new System.Collections.Generic.Dictionary<int, string>();
int maxIdx = -1;
for (int d = 1; d <= 8; d++)
{
    if (level.Count == 0) { sb.Append("第").Append(d).Append("层：无节点（树到此为止）").Append(Environment.NewLine); break; }
    var next = new ArrayList();
    var idxs = new System.Collections.Generic.List<string>();
    foreach (object it in level)
    {
        int ix = evtIdx(it);
        if (ix > maxIdx) maxIdx = ix;
        idxs.Add(ix + "/" + childCnt(it));
        var ks = kids(it);
        if (ks != null) foreach (object c in ks) next.Add(c);
    }
    sb.Append("第").Append(d).Append("层 节点=").Append(level.Count).Append("  idx/childCount=").Append(string.Join(" ", idxs.Take(14)));
    if (idxs.Count > 14) sb.Append(" …");
    sb.Append(Environment.NewLine);
    if (next.Count > 400) { sb.Append("  （子节点过多，截断）").Append(Environment.NewLine); break; }
    level = next;
}
sb.Append("== 树上出现的最大事件索引=").Append(maxIdx).Append("（工具 count=").Append(Convert.ToString(util.GetProperty("count", SF).GetValue(null, null))).Append("）").Append(Environment.NewLine);

// 用树里看到的索引去读明细，看能否拿到真实绘制与 GameObject
var ged = util.GetMethod("GetFrameEventData", SF);
var geo = util.GetMethod("GetFrameEventObject", SF);
var fDc = dataType.GetField("m_DrawCallCount", IF);
var fVc = dataType.GetField("m_VertexCount", IF);
var fSh = dataType.GetField("m_RealShaderName", IF);
var fMesh = dataType.GetField("m_Mesh", IF);
int tryMax = maxIdx < 0 ? 8 : Math.Min(maxIdx, 60);
int okN = 0, objN = 0, meshN = 0; long sumDc = 0;
var shown = new System.Collections.Generic.List<string>();
for (int i = 0; i <= tryMax; i++)
{
    object inst = Activator.CreateInstance(dataType);
    bool ok = false;
    try { ok = (bool)ged.Invoke(null, new object[] { i, inst }); } catch (Exception) { }
    if (!ok) continue;
    okN++;
    int dc = Convert.ToInt32(fDc.GetValue(inst) ?? 0);
    sumDc += dc;
    object me = fMesh.GetValue(inst);
    if (me is Mesh) meshN++;
    object go = null; try { go = geo.Invoke(null, new object[] { i }); } catch (Exception) { }
    string objn = go is GameObject g ? g.name : (go as Component)?.name ?? "-";
    if (objn != "-") objN++;
    if (shown.Count < 10 && (dc > 0 || objn != "-"))
        shown.Add("  #" + i + " dc=" + dc + " verts=" + Convert.ToInt32(fVc.GetValue(inst) ?? 0)
            + " mesh=" + (me is Mesh ? Cut(((Mesh)me).name, 16) : "-") + " obj=" + Cut(objn, 16)
            + " shader=" + Cut((string)fSh.GetValue(inst), 34));
}
sb.Append("== 按索引 0..").Append(tryMax).Append(" 读取 == 可解析=").Append(okN);
sb.Append(" 累计dc=").Append(sumDc).Append(" 带Mesh=").Append(meshN).Append(" 定位物体=").Append(objN).Append(Environment.NewLine);
foreach (var s in shown) sb.Append(s).Append(Environment.NewLine);
return sb.ToString();

static string Cut(string s, int n)
{
    if (string.IsNullOrEmpty(s)) return "-";
    s = s.Replace("\n", " ");
    return s.Length > n ? s.Substring(0, n) : s;
}
