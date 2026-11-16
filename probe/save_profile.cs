using System;
using System.IO;
using UnityEditorInternal;

var cfg = @"D:/unity_profile_inspector/runs/_probe/profile_cfg.txt";
var path = File.ReadAllText(cfg).Trim();
int first = ProfilerDriver.firstFrameIndex;
int last = ProfilerDriver.lastFrameIndex;
ProfilerDriver.SaveProfile(path);
return "first=" + first + ";last=" + last + ";exists=" + File.Exists(path);
