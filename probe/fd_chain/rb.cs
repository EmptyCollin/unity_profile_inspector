using System;
using System.Text;
using UnityEditor;
using UnityEngine;
var sb=new StringBuilder();
sb.Append("改前 runInBackground=").Append(Application.runInBackground).Append(" isPaused=").Append(EditorApplication.isPaused).Append(" frame=").Append(Time.frameCount).Append(Environment.NewLine);
Application.runInBackground=true;
EditorApplication.isPaused=false;
sb.Append("改后 runInBackground=").Append(Application.runInBackground).Append(" isPaused=").Append(EditorApplication.isPaused).Append(" frame=").Append(Time.frameCount).Append(" pos=");
try{sb.Append(GameObject.Find("Mover").transform.position.x.ToString("F3"));}catch(Exception){sb.Append("(场景无物体)");}
return sb.ToString();
