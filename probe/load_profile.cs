using System;
using System.IO;
using UnityEditorInternal;

var cfg = @"D:/unity_profile_inspector/runs/_probe/profile_load_cfg.txt";
var path = File.ReadAllText(cfg).Trim();
ProfilerDriver.LoadProfile(path, false);
return "loaded:" + ProfilerDriver.firstFrameIndex + ":" + ProfilerDriver.lastFrameIndex;
