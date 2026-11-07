using System;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;
const BindingFlags SF=BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static;
Type util=null;
foreach(var a in AppDomain.CurrentDomain.GetAssemblies()){Type[] ts;try{ts=a.GetTypes();}catch(Exception){continue;}util=util??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerUtility");}
try{util.GetMethod("SetEnabled",SF).Invoke(null,new object[]{true,0});}catch(Exception e){return "SetEnabled 异常: "+(e.InnerException??e).Message;}
return "SetEnabled(true) 已发出; 同调用 enabled=" + typeof(UnityEngine.FrameDebugger).GetProperty("enabled",SF).GetValue(null,null) + " frame=" + Time.frameCount;
