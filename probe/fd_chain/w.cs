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
sb.Append("A 只开窗口: enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused).Append(" frame=").Append(Time.frameCount).Append(" count=").Append(Convert.ToString(util.GetProperty("count",SF).GetValue(null,null))).Append(Environment.NewLine);
try{win.GetMethod("OpenWindow",SF).Invoke(null,null);}catch(Exception e){sb.Append("OpenWindow 异常 ").Append((e.InnerException??e).GetType().Name).Append(Environment.NewLine);}
sb.Append("B 开窗口后: enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused).Append(" frame=").Append(Time.frameCount).Append(" count=").Append(Convert.ToString(util.GetProperty("count",SF).GetValue(null,null)));
return sb.ToString();
