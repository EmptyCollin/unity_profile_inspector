using System;
using UnityEditor;
using UnityEngine;
EditorApplication.isPaused=false;
return "resume: isPaused=" + EditorApplication.isPaused + " frame=" + Time.frameCount + " pos=" + (GameObject.Find("Mover")!=null ? GameObject.Find("Mover").transform.position.x.ToString("F3") : "-");
