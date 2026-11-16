using System;
using System.Reflection;
using UnityEditorInternal;

var m = typeof(ProfilerDriver).GetMethod("SetProfilingEnabled",
    BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Static);
if (m != null) m.Invoke(null, new object[] { true });
return "set=" + (m != null)
    + "|first=" + ProfilerDriver.firstFrameIndex
    + "|last=" + ProfilerDriver.lastFrameIndex;
