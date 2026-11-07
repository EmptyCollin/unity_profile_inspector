using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

// 关键假设：GetFrameEvents() 只给"折叠树行"，而 GetFrameEventData(index) 按全局事件序号取数
// 若成立，则 index 从 9 往后仍有数据 —— 那样就能拿到完整一帧的所有绘制项
var sb = new StringBuilder();
const BindingFlags SF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
const BindingFlags ALL = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
Type util = null, dataType = null, evtType = null, details = null, treeT = null;
foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
{
    Type[] ts; try { ts = a.GetTypes(); } catch (Exception) { continue; }
    util = util ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerUtility");
    dataType = dataType ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerEventData");
    evtType = evtType ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerEvent" && x.FullName.Contains("FrameDebugger"));
    details = details ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerEventDetailsView");
    treeT = treeT ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerTreeView");
}
sb.Append("enabled=").Append(typeof(UnityEngine.FrameDebugger).GetProperty("enabled", SF).GetValue(null, null));
sb.Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" count=").Append(Convert.ToString(util.GetProperty("count", SF).GetValue(null, null)));
sb.Append(" limit=").Append(Convert.ToString(util.GetProperty("limit", SF).GetValue(null, null))).Append(Environment.NewLine);

var ged = util.GetMethod("GetFrameEventData", SF);
var fIdx = dataType.GetField("m_FrameEventIndex", ALL);
var fDc = dataType.GetField("m_DrawCallCount", ALL);
var fVc = dataType.GetField("m_VertexCount", ALL);
var fSh = dataType.GetField("m_RealShaderName", ALL);
var fPass = dataType.GetField("m_PassName", ALL);
var fMesh = dataType.GetField("m_Mesh", ALL);
var fCause = dataType.GetField("m_BatchBreakCause", ALL);
var fRT = dataType.GetField("m_RenderTargetName", ALL);

// 扫 index 0..599，统计有多少条真能解析出数据
int okN = 0, drawN = 0, meshN = 0; long sumDc = 0, sumVerts = 0;
int maxOk = -1;
var shaderHits = new Dictionary<string, int>();
var samples = new List<string>();
for (int i = 0; i < 600; i++)
{
    object inst = Activator.CreateInstance(dataType);
    bool ok = false;
    try { ok = (bool)ged.Invoke(null, new object[] { i, inst }); } catch (Exception) { continue; }
    if (!ok) continue;
    okN++;
    if (i > maxOk) maxOk = i;
    int dc = Convert.ToInt32(fDc.GetValue(inst) ?? 0);
    int vc = Convert.ToInt32(fVc.GetValue(inst) ?? 0);
    if (dc > 0) { drawN++; sumDc += dc; }
    sumVerts += vc;
    object me = fMesh.GetValue(inst);
    if (me is Mesh msh) { meshN++; }
    string shn = (string)fSh.GetValue(inst) ?? "-";
    shaderHits[shn] = shaderHits.ContainsKey(shn) ? shaderHits[shn] + 1 : 1;
    if (samples.Count < 10 && (me is Mesh || dc > 0))
        samples.Add("  #" + i + " idx=" + Convert.ToString(fIdx.GetValue(inst)) + " dc=" + dc + " verts=" + vc
            + " mesh=" + (me is Mesh ? Cut(((Mesh)me).name, 16) : "-")
            + " shader=" + Cut(shn, 34) + " pass=" + Cut((string)fPass.GetValue(inst), 12)
            + " rt=" + Cut((string)fRT.GetValue(inst), 14)
            + " cause=" + Cut(Convert.ToString(fCause.GetValue(inst) ?? ""), 18));
}
sb.Append("== 全局序号扫描 0..599 ==").Append(Environment.NewLine);
sb.Append("  可解析=").Append(okN).Append(" 最大成功序号=").Append(maxOk);
sb.Append(" 有绘制=").Append(drawN).Append(" 累计dc=").Append(sumDc);
sb.Append(" 累计顶点=").Append(sumVerts).Append(" 带Mesh=").Append(meshN).Append(Environment.NewLine);
sb.Append(" 着色器种类=").Append(shaderHits.Count).Append(Environment.NewLine);
foreach (var kv in shaderHits.OrderByDescending(x => x.Value).Take(8))
    sb.Append("    ").Append(Cut(kv.Key, 46)).Append(" x").Append(kv.Value).Append(Environment.NewLine);
foreach (var s in samples) sb.Append(s).Append(Environment.NewLine);

// 顺带把 UI 明细面板的入口列出来（GUI 里能看到的就是它渲染的）
if (details != null)
{
    sb.Append("== DetailsView 方法 ==").Append(Environment.NewLine);
    foreach (var m in details.GetMethods(ALL).Where(x => !x.IsSpecialName && !x.Name.EndsWith("_Injected") && x.DeclaringType == details))
        sb.Append("    ").Append(m.Name).Append("(").Append(string.Join(",", m.GetParameters().Select(p => p.ParameterType.Name))).Append(")").Append(Environment.NewLine);
}
if (treeT != null)
{
    sb.Append("== TreeView 自身方法 ==").Append(Environment.NewLine);
    foreach (var m in treeT.GetMethods(ALL).Where(x => !x.IsSpecialName && !x.Name.EndsWith("_Injected") && x.DeclaringType == treeT))
        sb.Append("    ").Append(m.Name).Append("(").Append(string.Join(",", m.GetParameters().Select(p => p.ParameterType.Name))).Append(") -> ").Append(m.ReturnType.Name).Append(Environment.NewLine);
    foreach (var f in treeT.GetFields(ALL).Where(x => !x.IsLiteral && x.DeclaringType == treeT))
        sb.Append("    字段 ").Append(f.Name).Append(" : ").Append(f.FieldType.Name).Append(Environment.NewLine);
}
return sb.ToString();

static string Cut(string s, int n) { if (string.IsNullOrEmpty(s)) return "-"; s = s.Replace("\n", " ").Replace("\r", " "); return s.Length > n ? s.Substring(0, n) : s; }
