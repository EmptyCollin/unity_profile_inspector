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
var cp=util.GetProperty("count",SF); var lp=util.GetProperty("limit",SF);
sb.Append("落地后 enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" count=").Append(Convert.ToString(cp.GetValue(null,null))).Append(" limit=").Append(Convert.ToString(lp.GetValue(null,null)));
sb.Append(" frame=").Append(Time.frameCount).Append(Environment.NewLine);
try{lp.SetValue(null,20000,null);sb.Append("  limit 放大到 20000 -> 回读 ").Append(Convert.ToString(lp.GetValue(null,null))).Append(Environment.NewLine);}
catch(Exception e){sb.Append("  放大异常 ").Append((e.InnerException??e).Message).Append(Environment.NewLine);}
sb.Append("  复核 count=").Append(Convert.ToString(cp.GetValue(null,null))).Append(" limit=").Append(Convert.ToString(lp.GetValue(null,null)));
return sb.ToString();
