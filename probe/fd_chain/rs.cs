using System;
using UnityEditor;
using UnityEngine;
return "resume: isPaused " + EditorApplication.isPaused + " -> " + (EditorApplication.isPaused=false, EditorApplication.isPaused).Item2 + " frame=" + Time.frameCount;
