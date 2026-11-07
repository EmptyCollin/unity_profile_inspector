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
sb.Append("起点 enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" count=").Append(Convert.ToString(cp.GetValue(null,null))).Append(" limit=").Append(Convert.ToString(lp.GetValue(null,null))).Append(" frame=").Append(Time.frameCount).Append(Environment.NewLine);
try{lp.SetValue(null,5000,null);}catch(Exception e){sb.Append("limit 赋值异常 ").Append((e.InnerException??e).Message).Append(Environment.NewLine);}
sb.Append("limit 现值=").Append(Convert.ToString(lp.GetValue(null,null))).Append(Environment.NewLine);
if((bool)en.GetValue(null,null)) { EditorApplication.isPaused=false; sb.Append("已 resume（抓取保持开启）").Append(Environment.NewLine); }
else sb.Append("抓取未开启，无法 resume").Append(Environment.NewLine);
sb.Append("末态 isPaused=").Append(EditorApplication.isPaused).Append(" count=").Append(Convert.ToString(cp.GetValue(null,null))).Append(" frame=").Append(Time.frameCount);
return sb.ToString();
