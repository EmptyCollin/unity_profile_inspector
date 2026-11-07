using System;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;
var sb=new StringBuilder();
const BindingFlags SF=BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static;
Type win=null,util=null;
foreach(var a in AppDomain.CurrentDomain.GetAssemblies()){Type[] ts;try{ts=a.GetTypes();}catch(Exception){continue;}
 win=win??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerWindow");
 util=util??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerUtility");}
var en=typeof(UnityEngine.FrameDebugger).GetProperty("enabled",SF);
sb.Append("开启前 enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" frame=").Append(Time.frameCount).Append(" count=").Append(Convert.ToString(util.GetProperty("count",SF).GetValue(null,null))).Append(Environment.NewLine);
try{win.GetMethod("OpenWindowAndToggleEnabled",SF).Invoke(null,null);}catch(Exception e){sb.Append("异常 ").Append((e.InnerException??e).GetType().Name).Append(Environment.NewLine);}
sb.Append("同调用内 enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused);
return sb.ToString();
