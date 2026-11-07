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
sb.Append("起点 enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" frame=").Append(Time.frameCount).Append(Environment.NewLine);
try{win.GetMethod("OpenWindowAndToggleEnabled",SF).Invoke(null,null);}catch(Exception e){sb.Append("toggle 异常 ").Append((e.InnerException??e).GetType().Name).Append(Environment.NewLine);}
sb.Append("发出后 enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" count=").Append(Convert.ToString(util.GetProperty("count",SF).GetValue(null,null)));
sb.Append(" limit=").Append(Convert.ToString(util.GetProperty("limit",SF).GetValue(null,null)));
return sb.ToString();
