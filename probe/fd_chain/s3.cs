using System;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

// 读取当帧抓取内容：序号参数、不吞异常、逐条报告 ok
var sb = new StringBuilder();
const BindingFlags SF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
const BindingFlags IF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
Type util = null, data = null, evtT = null;
foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
{
    Type[] ts; try { ts = a.GetTypes(); } catch (Exception) { continue; }
    util = util ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerUtility");
    data = data ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerEventData");
    evtT = evtT ?? ts.FirstOrDefault(x => x.Name == "FrameDebuggerEvent" && x.FullName.Contains("FrameDebugger"));
}

int count = Convert.ToInt32(util.GetProperty("count", SF).GetValue(null, null));
var arr = util.GetMethod("GetFrameEvents", SF).Invoke(null, null) as Array;
sb.Append("enabled=").Append(typeof(UnityEngine.FrameDebugger).GetProperty("enabled", BindingFlags.Public | BindingFlags.Static).GetValue(null, null));
sb.Append(" isPlaying=").Append(Application.isPlaying).Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" frame=").Append(Time.frameCount).Append(" count=").Append(count);
sb.Append(" arr=").Append(arr.Length);
try { sb.Append(" Mover=").Append(GameObject.Find("Mover").transform.position.x.ToString("F2")); } catch (Exception) { }
sb.Append(Environment.NewLine);

// GetFrameEvents 返回的结构体数组，元素需按 ref/out 传
var ged = util.GetMethod("GetFrameEventData", SF);
var geo = util.GetMethod("GetFrameEventObject", SF);
var gnm = util.GetMethod("GetFrameEventInfoName", SF);
var fD = data.GetField("m_DrawCallCount", IF);
var fV = data.GetField("m_VertexCount", IF);
var fS = data.GetField("m_RealShaderName", IF);
var fP = data.GetField("m_PassName", IF);
var bcm = util.GetMethod("GetBatchBreakCauseStrings", SF);
string[] causes = null;
if (bcm != null) { try { causes = bcm.Invoke(null, null) as string[]; } catch (Exception) { } }

int nOk = 0, nDraw = 0, nObj = 0, firstFail = -1; string failMsg = "";
for (int i = 0; i < arr.Length; i++)
{
    object inst = Activator.CreateInstance(data);
    bool ok;
    try { ok = (bool)ged.Invoke(null, new object[] { i, inst }); }
    catch (Exception e) { if (firstFail < 0) { firstFail = i; failMsg = (e.InnerException ?? e).GetType().Name + ": " + (e.InnerException ?? e).Message; } continue; }
    if (!ok) { if (firstFail < 0) { firstFail = i; failMsg = "返回 false"; } continue; }
    nOk++;
    int dc = Convert.ToInt32(fD.GetValue(inst) ?? 0);
    if (dc > 0) nDraw++;
    object go = null;
    try { go = geo.Invoke(null, new object[] { i }); } catch (Exception) { }
    if (go != null) nObj++;
    if (i < 12 || (dc > 0 && nDraw <= 6))
    {
        string nm2 = ""; try { nm2 = (string)gnm.Invoke(null, new object[] { i }); } catch (Exception) { }
        string objn = go is GameObject g ? g.name : (go as Component)?.name ?? "";
        sb.Append("  #").Append(i).Append(" name=").Append(Cut(nm2, 32)).Append(" obj=").Append(Cut(objn, 16));
        sb.Append(" dc=").Append(dc).Append(" verts=").Append(Convert.ToInt32(fV.GetValue(inst) ?? 0));
        sb.Append(" shader=").Append(Cut((string)fS.GetValue(inst), 40)).Append(" pass=").Append(Cut((string)fP.GetValue(inst), 14));
        if (causes != null && i < causes.Length) sb.Append(" cause=").Append(Cut(causes[i], 30));
        sb.Append(Environment.NewLine);
    }
}
sb.Append("== 汇总 ==").Append(Environment.NewLine);
sb.Append("  事件=").Append(arr.Length).Append(" 可解析=").Append(nOk).Append(" 有绘制=").Append(nDraw)
  .Append(" 可定位物体=").Append(nObj).Append(Environment.NewLine);
sb.Append("  首个失败 #").Append(firstFail).Append(" -> ").Append(Cut(failMsg, 110)).Append(Environment.NewLine);
sb.Append("  batchBreakCause 条数=").Append(causes == null ? -1 : causes.Length);
return sb.ToString();

static string Cut(string s, int n) { if (string.IsNullOrEmpty(s)) return "-"; s = s.Replace("\n", " ").Replace("\r", " "); return s.Length > n ? s.Substring(0, n) : s; }
