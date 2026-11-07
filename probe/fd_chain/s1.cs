using System;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;
var sb=new StringBuilder();
const BindingFlags SF=BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static;
Type util=null;
foreach(var a in AppDomain.CurrentDomain.GetAssemblies()){Type[] ts;try{ts=a.GetTypes();}catch(Exception){continue;}util=util??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerUtility");}
var en=typeof(UnityEngine.FrameDebugger).GetProperty("enabled",SF);
sb.Append("改前 enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused).Append(" frame=").Append(Time.frameCount).Append(Environment.NewLine);
try{util.GetMethod("SetEnabled",SF).Invoke(null,new object[]{true,0});sb.Append("SetEnabled(true,0) 已执行").Append(Environment.NewLine);}
catch(Exception e){sb.Append("SetEnabled 异常 ").Append((e.InnerException??e).Message).Append(Environment.NewLine);}
sb.Append("改后 enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused).Append(" frame=").Append(Time.frameCount);
sb.Append(" count=").Append(Convert.ToString(util.GetProperty("count",SF).GetValue(null,null)));
return sb.ToString();
