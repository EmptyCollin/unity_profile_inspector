using System;
using System.Text;
using UnityEditor;
using UnityEngine;
var sb=new StringBuilder();
string pos="-";
try{pos=GameObject.Find("Mover").transform.position.x.ToString("F3");}catch(Exception){}
sb.Append("playMode=").Append(EditorApplication.isPlaying);
sb.Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" runInBg=").Append(Application.runInBackground);
sb.Append(" frame=").Append(Time.frameCount);
sb.Append(" rendered=").Append(Time.renderedFrameCount);
sb.Append(" time=").Append(Time.time.ToString("F3"));
sb.Append(" pos=").Append(pos);
return sb.ToString();
