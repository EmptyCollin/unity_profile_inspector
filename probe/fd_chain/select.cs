using System;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;
var sb=new StringBuilder();
const BindingFlags SF=BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static;
const BindingFlags ALL=BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance;
Type util=null,win=null,dataType=null,evtType=null;
foreach(var a in AppDomain.CurrentDomain.GetAssemblies()){Type[] ts;try{ts=a.GetTypes();}catch(Exception){continue;}
 util=util??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerUtility");
 win=win??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerWindow");
 dataType=dataType??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerEventData");
 evtType=evtType??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerEvent"&&x.FullName.Contains("FrameDebugger"));}
var en=typeof(UnityEngine.FrameDebugger).GetProperty("enabled",SF);
sb.Append("起点 enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" count=").Append(Convert.ToString(util.GetProperty("count",SF).GetValue(null,null))).Append(" frame=").Append(Time.frameCount).Append(Environment.NewLine);
// 抓取没开着就先开
if(!(bool)en.GetValue(null,null)){try{win.GetMethod("OpenWindowAndToggleEnabled",SF).Invoke(null,null);}catch(Exception){}
  sb.Append("已发出 toggle（需等落地，本次仅布防）").Append(Environment.NewLine);
  sb.Append("enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused);return sb.ToString();}
// 抓取开着：逐行选中后读明细
object tw=null,tree=null;
try{tw=win.GetMethod("OpenWindow",SF).Invoke(null,null);tree=win.GetField("m_TreeView",ALL).GetValue(tw);}catch(Exception e){sb.Append("取树异常 ").Append((e.InnerException??e).GetType().Name).Append(Environment.NewLine);}
var ged=util.GetMethod("GetFrameEventData",SF); var geo=util.GetMethod("GetFrameEventObject",SF);
var fDc=dataType.GetField("m_DrawCallCount",ALL); var fSh=dataType.GetField("m_RealShaderName",ALL);
var fVc=dataType.GetField("m_VertexCount",ALL); var fMesh=dataType.GetField("m_Mesh",ALL);
var fType=evtType.GetField("m_Type",ALL); var fCause=dataType.GetField("m_BatchBreakCause",ALL);
var arr=util.GetMethod("GetFrameEvents",SF).Invoke(null,null) as Array;
int n=arr==null?0:arr.Length;
sb.Append("事件行数=").Append(n).Append(Environment.NewLine);
MethodInfo mSel = null, mSelIdx = null;
if(tree!=null){ mSel=tree.GetType().GetMethod("SetSelection",ALL); mSelIdx=tree.GetType().GetMethod("SelectFrameEventIndex",ALL); }
int okNoSel=0, okWithSel=0, objFound=0, meshFound=0;
for(int i=0;i<n;i++)
{
  object inst=Activator.CreateInstance(dataType); bool ok0=false;
  try{ok0=(bool)ged.Invoke(null,new object[]{i,inst});}catch(Exception){}
  if(mSel!=null){ try{mSel.Invoke(tree,new object[]{i});}catch(Exception){} }
  if(mSelIdx!=null){ try{mSelIdx.Invoke(tree,new object[]{i});}catch(Exception){} }
  if(tw!=null){ try{typeof(EditorWindow).GetProperty("hasFocus")?.SetValue(tw,true,null);}catch(Exception){} try{tw.GetType().GetMethod("OnNewFrameEventSelected",ALL)?.Invoke(tw,null);}catch(Exception){} }
  object inst2=Activator.CreateInstance(dataType); bool ok1=false;
  try{ok1=(bool)ged.Invoke(null,new object[]{i,inst2});}catch(Exception){}
  if(ok0)okNoSel++;
  if(ok1){okWithSel++;
    object me=fMesh.GetValue(inst2); if(me is Mesh)meshFound++;
    object go=null; try{go=geo.Invoke(null,new object[]{i});}catch(Exception){}
    string objn = go is GameObject g?g.name:(go as Component)?.name ?? "-";
    if(objn!="-")objFound++;
    sb.Append("  #").Append(i).Append(" 选前=").Append(ok0).Append(" 选后=").Append(ok1)
      .Append(" type=").Append(Convert.ToString(fType.GetValue(arr.GetValue(i))))
      .Append(" dc=").Append(Convert.ToInt32(fDc.GetValue(inst2)??0))
      .Append(" verts=").Append(Convert.ToInt32(fVc.GetValue(inst2)??0))
      .Append(" mesh=").Append(me is Mesh?Cut(((Mesh)me).name,14):"-")
      .Append(" obj=").Append(Cut(objn,14))
      .Append(" shader=").Append(Cut((string)fSh.GetValue(inst2),32))
      .Append(" cause=").Append(Cut(Convert.ToString(fCause.GetValue(inst2)??""),16)).Append(Environment.NewLine);
  } else {
    sb.Append("  #").Append(i).Append(" 选前=").Append(ok0).Append(" 选后=false type=").Append(Convert.ToString(fType.GetValue(arr.GetValue(i)))).Append(Environment.NewLine);
  }
}
sb.Append("== 汇总 == 选前可解析=").Append(okNoSel).Append(" 选后可解析=").Append(okWithSel);
sb.Append(" 定位到物体=").Append(objFound).Append(" 带Mesh=").Append(meshFound).Append(Environment.NewLine);
sb.Append(" 末态 enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused).Append(" count=").Append(Convert.ToString(util.GetProperty("count",SF).GetValue(null,null)));
return sb.ToString();
static string Cut(string s,int n){if(string.IsNullOrEmpty(s))return "-";s=s.Replace("\n"," ");return s.Length>n?s.Substring(0,n):s;}
