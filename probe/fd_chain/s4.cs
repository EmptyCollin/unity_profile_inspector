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
try{util.GetMethod("SetEnabled",SF).Invoke(null,new object[]{false,0});}catch(Exception e){sb.Append("关闭异常 ").Append((e.InnerException??e).Message).Append(Environment.NewLine);}
sb.Append("enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" frame=").Append(Time.frameCount).Append(" pos=");
try{sb.Append(GameObject.Find("Mover").transform.position.x.ToString("F3"));}catch(Exception){sb.Append("-");}
return sb.ToString();
