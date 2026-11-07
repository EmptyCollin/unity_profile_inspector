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
sb.Append(" count=").Append(Convert.ToString(cp.GetValue(null,null))).Append(" limit=").Append(Convert.ToString(lp.GetValue(null,null))).Append(" frame=").Append(Time.frameCount).Append(Environment.NewLine);
if((bool)en.GetValue(null,null)){
  try{lp.SetValue(null,5000,null);}catch(Exception e){sb.Append("  limit 赋值异常 ").Append((e.InnerException??e).Message).Append(Environment.NewLine);}
  EditorApplication.isPaused=false;
  sb.Append("  limit=").Append(Convert.ToString(lp.GetValue(null,null))).Append(" 已 resume isPaused=").Append(EditorApplication.isPaused);
}else sb.Append("  抓取没生效，本步不做处理");
sb.Append(" count=").Append(Convert.ToString(cp.GetValue(null,null)));
return sb.ToString();
