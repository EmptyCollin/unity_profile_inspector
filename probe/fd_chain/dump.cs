using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;
var sb=new StringBuilder();
const BindingFlags SF=BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static;
const BindingFlags IF=BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance;
Type util=null,dataType=null,evtType=null;
foreach(var a in AppDomain.CurrentDomain.GetAssemblies()){Type[] ts;try{ts=a.GetTypes();}catch(Exception){continue;}
 util=util??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerUtility");
 dataType=dataType??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerEventData");
 evtType=evtType??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerEvent"&&x.FullName.Contains("FrameDebugger"));}
var en=typeof(UnityEngine.FrameDebugger).GetProperty("enabled",SF);
int count=Convert.ToInt32(util.GetProperty("count",SF).GetValue(null,null));
var arr=util.GetMethod("GetFrameEvents",SF).Invoke(null,null) as Array;
sb.Append("enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" frame=").Append(Time.frameCount).Append(" count=").Append(count).Append(" arr=").Append(arr.Length);
try{sb.Append(" Mover=").Append(GameObject.Find("Mover").transform.position.x.ToString("F2"));}catch(Exception){}
sb.Append(Environment.NewLine);
var ged=util.GetMethod("GetFrameEventData",SF);
var geo=util.GetMethod("GetFrameEventObject",SF);
var gnm=util.GetMethod("GetFrameEventInfoName",SF);
var fType=evtType.GetField("m_Type",IF);
var fDc=dataType.GetField("m_DrawCallCount",IF);
var fVc=dataType.GetField("m_VertexCount",IF);
var fSh=dataType.GetField("m_RealShaderName",IF);
var fPass=dataType.GetField("m_PassName",IF);
var fMesh=dataType.GetField("m_Mesh",IF);
var fCause=dataType.GetField("m_BatchBreakCause",IF);
var types=new Dictionary<string,int>();
int nOk=0,nObj=0,nMesh=0,nDraw=0,totalDc=0,firstFail=-1; string failMsg="-";
var samples=new List<string>();
for(int i=0;i<arr.Length;i++)
{
  object ev=arr.GetValue(i);
  string ty=Convert.ToString(fType.GetValue(ev));
  types[ty]=types.ContainsKey(ty)?types[ty]+1:1;
  object inst=Activator.CreateInstance(dataType);
  bool ok=false;
  try{ok=(bool)ged.Invoke(null,new object[]{i,inst});}
  catch(Exception e){if(firstFail<0){firstFail=i;failMsg=(e.InnerException??e).GetType().Name+":"+Cut((e.InnerException??e).Message,60);}continue;}
  if(!ok){if(firstFail<0){firstFail=i;failMsg="GetFrameEventData 返回 false（疑为分组节点）";}continue;}
  nOk++;
  int dc=Convert.ToInt32(fDc.GetValue(inst)??0);
  if(dc>0){nDraw++;totalDc+=dc;}
  object go=null; try{go=geo.Invoke(null,new object[]{i});}catch(Exception){}
  string objn="-";
  if(go is GameObject g){objn=g.name;nObj++;}
  else if(go is Component c){objn=c.name;nObj++;}
  object me=fMesh.GetValue(inst);
  if(me is Mesh) nMesh++;
  if(samples.Count<14 && (objn!="-" || (me is Mesh)))
    samples.Add("  #"+i+" type="+ty+" obj="+Cut(objn,20)+" mesh="+(me is Mesh ? Cut(((Mesh)me).name,18) : "-")
      +" dc="+dc+" verts="+Convert.ToInt32(fVc.GetValue(inst)??0)
      +" shader="+Cut((string)fSh.GetValue(inst),30)+" pass="+Cut((string)fPass.GetValue(inst),12)
      +" cause="+Cut(Convert.ToString(fCause.GetValue(inst)??""),22));
}
sb.Append("== 类型分布 ==").Append(Environment.NewLine);
foreach(var kv in types.OrderByDescending(x=>x.Value)) sb.Append("  ").Append(kv.Key).Append(" = ").Append(kv.Value).Append(Environment.NewLine);
sb.Append("== 汇总 ==").Append(Environment.NewLine);
sb.Append("  事件=").Append(arr.Length).Append(" 可解析=").Append(nOk).Append(" 有绘制=").Append(nDraw)
  .Append(" 累计drawCall=").Append(totalDc).Append(" 可定位物体=").Append(nObj).Append(" 带Mesh=").Append(nMesh).Append(Environment.NewLine);
sb.Append("  首个不可解析 #").Append(firstFail).Append(" -> ").Append(failMsg).Append(Environment.NewLine);
sb.Append("== 归因样本 ==").Append(Environment.NewLine);
foreach(var s in samples) sb.Append(s).Append(Environment.NewLine);
return sb.ToString();
static string Cut(string s,int n){if(string.IsNullOrEmpty(s))return "-";s=s.Replace("\n"," ").Replace("\r"," ");return s.Length>n?s.Substring(0,n):s;}
