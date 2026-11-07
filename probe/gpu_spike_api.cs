using System;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEngine;

// D13 候选 1/2 的接口存在性验证（不猜签名，反射列真值）
var sb = new StringBuilder();
sb.Append("graphics dev=" + SystemInfo.graphicsDeviceName + " api=" + SystemInfo.graphicsDeviceType);
sb.Append(" isPlaying=" + Application.isPlaying).Append(Environment.NewLine);

var ftm = Type.GetType("UnityEngine.FrameTimingManager, UnityEngine.CoreModule");
var ft = Type.GetType("UnityEngine.FrameTiming, UnityEngine.CoreModule");
sb.Append("FrameTimingManager=" + (ftm == null ? "NULL" : ftm.FullName)).Append(Environment.NewLine);
if (ftm != null)
{
    foreach (var m in ftm.GetMethods(BindingFlags.Public | BindingFlags.Static).Where(x => !x.IsSpecialName))
        sb.Append("  M ").Append(m.Name).Append("(")
          .Append(string.Join(",", m.GetParameters().Select(p => (p.IsOut ? "out " : "") + p.ParameterType.Name))).Append(")")
          .Append(Environment.NewLine);
}
if (ft != null)
    sb.Append("  FrameTiming 属性: ").Append(string.Join(", ", ft.GetProperties(BindingFlags.Public | BindingFlags.Instance).Select(p => p.Name))).Append(Environment.NewLine);

var ge = Type.GetType("UnityEngine.Rendering.GraphicsEvents, UnityEngine.CoreModule");
var grs = Type.GetType("UnityEngine.Rendering.GraphicsRenderStats, UnityEngine.CoreModule");
sb.Append("GraphicsEvents=" + (ge == null ? "NULL" : "有"));
sb.Append(" GraphicsRenderStats=" + (grs == null ? "NULL" : "有")).Append(Environment.NewLine);
if (ge != null)
{
    var names = new[] { "drawCall", "invalidateState", "present", "swapBuffers" };
    foreach (var n in names)
    {
        var e = ge.GetEvent(n, BindingFlags.Public | BindingFlags.Static);
        sb.Append("  event ").Append(n).Append(" = ").Append(e != null ? "存在" : "无").Append(Environment.NewLine);
    }
}
if (grs != null)
    sb.Append("  RenderStats 字段: ").Append(string.Join(", ", grs.GetFields(BindingFlags.Public | BindingFlags.Instance).Select(f => f.Name))).Append(Environment.NewLine);

// 立即做一次只读调用：抓一帧计时看数值形态（延迟 4 帧，这里只求"有无真值"）
if (ftm != null && ft != null)
{
    try
    {
        var cap = ftm.GetMethod("CaptureFrameTimings");
        var get = ftm.GetMethod("GetLatestTimings");
        cap.Invoke(null, null);
        Array arr = Array.CreateInstance(ft, 8);
        uint got = (uint)get.Invoke(null, new object[] { 8u, arr });
        sb.Append("CaptureFrameTimings+GetLatestTimings 成功, 返回帧数=").Append(got).Append(Environment.NewLine);
        var pGpu = ft.GetProperty("gpuFrameTime");
        var pCpu = ft.GetProperty("cpuFrameTime");
        var pComp = ft.GetProperty("cpuTimeFrameComplete");
        for (uint i = 0; i < got && i < 4; i++)
            sb.Append("  帧" + i + " gpuFrameTime=" + (double)pGpu.GetValue(arr.GetValue(i)) +
                      " cpuFrameTime=" + (double)pCpu.GetValue(arr.GetValue(i)) +
                      " complete=" + (long)pComp.GetValue(arr.GetValue(i))).Append(Environment.NewLine);
        var freqG = ftm.GetMethod("GetGpuTimerFrequency");
        var freqC = ftm.GetMethod("GetCpuTimerFrequency");
        sb.Append("  gpuFreq=").Append(Convert.ToString(freqG.Invoke(null, null)))
          .Append(" cpuFreq=").Append(Convert.ToString(freqC.Invoke(null, null)));
    }
    catch (Exception ex)
    {
        var inner = ex.InnerException ?? ex;
        sb.Append("计时器调用异常 ").Append(inner.GetType().Name).Append(": ").Append(inner.Message);
    }
}
return sb.ToString();
