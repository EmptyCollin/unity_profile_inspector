using System;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;
var sb=new StringBuilder();
const BindingFlags SF=BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static;
Type util=null,win=null;
foreach(var a in AppDomain.CurrentDomain.GetAssemblies()){Type[] ts;try{ts=a.GetTypes();}catch(Exception){continue;}
 util=util??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerUtility");
 win=win??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerWindow");}
var en=typeof(UnityEngine.FrameDebugger).GetProperty("enabled",SF);
var lp=util.GetProperty("limit",SF);
sb.Append("起点 enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" count=").Append(Convert.ToString(util.GetProperty("count",SF).GetValue(null,null)));
sb.Append(" limit=").Append(Convert.ToString(lp.GetValue(null,null))).Append(" frame=").Append(Time.frameCount).Append(Environment.NewLine);
lp.SetValue(null,8000,null);
sb.Append("1 limit->8000").Append(Environment.NewLine);
try{win.GetMethod("OpenWindow",SF).Invoke(null,null);sb.Append("2 OpenWindow ok").Append(Environment.NewLine);}
catch(Exception e){sb.Append("2 OpenWindow 异常 ").Append((e.InnerException??e).GetType().Name).Append(Environment.NewLine);}
try{util.GetMethod("SetEnabled",SF).Invoke(null,new object[]{true,0});sb.Append("3 SetEnabled ok").Append(Environment.NewLine);}
catch(Exception e){sb.Append("3 SetEnabled 异常 ").Append((e.InnerException??e).Message).Append(Environment.NewLine);}
sb.Append("同调用末态 enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" count=").Append(Convert.ToString(util.GetProperty("count",SF).GetValue(null,null)));
sb.Append(" limit=").Append(Convert.ToString(lp.GetValue(null,null)));
return sb.ToString();
