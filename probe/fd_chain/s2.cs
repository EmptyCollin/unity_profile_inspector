using System;
using System.Text;
using UnityEditor;
using UnityEngine;
var sb=new StringBuilder();
sb.Append("改前 isPaused=").Append(EditorApplication.isPaused).Append(" frame=").Append(Time.frameCount).Append(Environment.NewLine);
try{EditorApplication.isPaused=false;sb.Append("isPaused=false 已执行").Append(Environment.NewLine);}
catch(Exception e){sb.Append("异常 ").Append(e.Message).Append(Environment.NewLine);}
sb.Append("改后 isPaused=").Append(EditorApplication.isPaused).Append(" frame=").Append(Time.frameCount).Append(" pos=");
try{sb.Append(GameObject.Find("Mover").transform.position.x.ToString("F3"));}catch(Exception){sb.Append("-");}
return sb.ToString();
