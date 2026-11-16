using System;
using System.Reflection;
using UnityEditor;

var t = Type.GetType("UnityEditor.ProfilerWindow, UnityEditor");
object w = null;
var all = Resources.FindObjectsOfTypeAll(t);
if (all != null && all.Length > 0) w = all[0];
if (w == null) w = EditorWindow.GetWindow(t);
var rec = t.GetMethod("SetRecordingEnabled",
    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
if (rec != null) rec.Invoke(w, new object[] { false });
return "off";
