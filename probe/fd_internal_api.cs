// probe/fd_internal_api.cs —— 定向反射：FrameDebuggerInternal 类本体 + 相关 EditorWindow
// 目的：确认"不开窗口"能否抓到并读取当帧事件（决定第三层是自动的还是人工的）。
using System;
using System.Linq;
using System.Reflection;
using System.Text;

var sb = new StringBuilder();
var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;

Type fdi = Type.GetType("UnityEditorInternal.FrameDebuggerInternal, UnityEditor.CoreModule");
sb.Append("FrameDebuggerInternal: " + (fdi == null ? "NULL" : "FOUND") + "\n");
if (fdi != null)
{
    sb.Append("== static methods ==\n");
    int n = 0;
    foreach (var m in fdi.GetMethods(flags).Where(x => x.IsStatic && !x.IsSpecialName).OrderBy(x => x.Name))
    {
        if (m.Name.StartsWith("op_") || m.Name.Length <= 3) continue;
        if (n++ > 60) { sb.Append(" ...suppressed\n"); break; }
        string ps = string.Join(",", m.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name));
        sb.Append("  ").Append(m.ReturnType.Name).Append(" ").Append(m.Name).Append("(").Append(ps).Append(")\n");
    }
    sb.Append("\n== fields ==\n");
    n = 0;
    foreach (var f in fdi.GetFields(flags).Where(x => x.IsStatic))
    {
        if (n++ > 30) { sb.Append(" ...suppressed\n"); break; }
        sb.Append("  ").Append(f.FieldType.Name).Append(" ").Append(f.Name).Append("\n");
    }
    sb.Append("\n== nested types ==\n");
    foreach (var nt in fdi.GetNestedTypes(flags))
        sb.Append("  ").Append(nt.Name).Append("\n");
}

// 事件数据的 getter：找所有名字带 GetEvent / GetFrame 的方法（跨类型）
sb.Append("\n== 事件读取候选（全程序集扫描，限 FrameDebugger 相关类型）==\n");
foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
{
    Type[] ts; try { ts = asm.GetTypes(); } catch (Exception) { continue; }
    foreach (var t in ts)
    {
        if (t == null || t.Name == null) continue;
        if (!t.Name.Contains("FrameDebugger")) continue;
        foreach (var m in t.GetMethods(flags))
        {
            if (m.IsSpecialName) continue;
            if (m.Name.Contains("GetEvent") || m.Name.Contains("GetFrame") || m.Name.Contains("Enable")
                || m.Name.Contains("Capture") || m.Name.Contains("Reload") || m.Name.Contains("Build"))
                sb.Append("  ").Append(t.Name).Append(".").Append(m.Name).Append("(")
                  .Append(string.Join(",", m.GetParameters().Select(p => p.ParameterType.Name)))
                  .Append(") -> ").Append(m.ReturnType.Name).Append("\n");
        }
    }
}

// 窗口类是否存在（存在=可能需要打开它才有数据）
Type win = Type.GetType("UnityEditor.FrameDebuggerWindow, UnityEditor.CoreModule");
sb.Append("\nFrameDebuggerWindow: " + (win == null ? "NULL" : "FOUND"));
if (win != null)
{
    var mi = win.GetMethod("InitFrameData", flags);
    sb.Append("  InitFrameData: " + (mi == null ? "无" : string.Join(",", mi.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name))));
}
return sb.ToString();
