// probe/fd_api.cs —— 只读反射探测：Frame Debugger 相关类型的真实成员
// 原则（I8）：不猜 API 名，先枚举；输出截断，避免污染上下文。
using System;
using System.Linq;
using System.Reflection;
using System.Text;

var sb = new StringBuilder();
string[] want = { "FrameDebugger", "FrameCapture", "FrameDebugData" };
int shown = 0;

foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
{
    Type[] ts;
    try { ts = asm.GetTypes(); }
    catch (Exception) { continue; }

    foreach (var t in ts)
    {
        if (t.Name == null) continue;
        if (!want.Any(x => t.Name.IndexOf(x, StringComparison.OrdinalIgnoreCase) >= 0)) continue;
        if (shown >= 8) { sb.Append("(more suppressed)"); break; }
        shown++;

        sb.Append("### ").Append(t.FullName).Append("  [").Append(asm.GetName().Name)
          .Append("] public=").Append(t.IsPublic).Append(" abstract=").Append(t.IsAbstract)
          .Append("\n");

        var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;
        int n = 0;
        foreach (var m in t.GetMembers(flags))
        {
            if (n++ > 34) { sb.Append("   ...(members suppressed)\n"); break; }
            if (m is MethodInfo mi)
            {
                if (mi.IsSpecialName) continue;
                string ps = string.Join(",", mi.GetParameters().Select(p => p.ParameterType.Name));
                sb.Append("   M ").Append(mi.ReturnType.Name).Append(" ").Append(mi.Name)
                  .Append("(").Append(ps).Append(")")
                  .Append(mi.IsPublic ? "" : "  [internal]").Append("\n");
            }
            else if (m is FieldInfo fi)
            {
                sb.Append("   F ").Append(fi.FieldType.Name).Append(" ").Append(fi.Name)
                  .Append(fi.IsPublic ? "" : "  [internal]").Append("\n");
            }
            else if (m is PropertyInfo pi)
            {
                bool pub = pi.GetMethod != null && pi.GetMethod.IsPublic;
                sb.Append("   P ").Append(pi.PropertyType.Name).Append(" ").Append(pi.Name)
                  .Append(pub ? "" : "  [internal]").Append("\n");
            }
            else if (m is EventInfo ei)
            {
                sb.Append("   E ").Append(ei.EventHandlerType == null ? "?" : ei.EventHandlerType.Name)
                  .Append(" ").Append(ei.Name).Append("\n");
            }
        }
        if (sb.Length > 11000) { sb.Append("\n...TRUNC"); break; }
    }
    if (sb.Length > 11000) break;
}

return sb.Length == 0 ? "NO_TYPES_FOUND" : sb.ToString();
