using System;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;
const BindingFlags SF=BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static;
Type win=null;
foreach(var a in AppDomain.CurrentDomain.GetAssemblies()){Type[] ts;try{ts=a.GetTypes();}catch(Exception){continue;}win=win??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerWindow");}
try{win.GetMethod("OpenWindowAndToggleEnabled",SF).Invoke(null,null);}catch(Exception e){return "toggle 异常: "+(e.InnerException??e).Message;}
return "toggle 已发出; 同调用 enabled=" + typeof(UnityEngine.FrameDebugger).GetProperty("enabled",SF).GetValue(null,null) + " isPaused=" + EditorApplication.isPaused + " frame=" + Time.frameCount;
