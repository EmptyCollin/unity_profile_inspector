// I26：运行时 ablation 开关。文件驱动，改文件后立即生效（按 mtime 刷新）。
using System;
using System.Collections.Generic;
using System.IO;

public static class UpiAblation
{
    const string Flags = @"D:/unity_profile_inspector/runs/_probe/ablation_flags.txt";
    static DateTime _mtime = DateTime.MinValue;
    static readonly Dictionary<string, bool> _disabled = new Dictionary<string, bool>();

    public static bool Disabled(string siteId)
    {
        try
        {
            if (!File.Exists(Flags)) return false;
            var mt = File.GetLastWriteTimeUtc(Flags);
            if (mt != _mtime)
            {
                _mtime = mt;
                _disabled.Clear();
                foreach (var raw in File.ReadAllLines(Flags))
                {
                    var line = raw.Trim();
                    if (line.Length == 0 || line.StartsWith("#")) continue;
                    int eq = line.IndexOf('=');
                    if (eq <= 0) continue;
                    var k = line.Substring(0, eq).Trim();
                    var v = line.Substring(eq + 1).Trim();
                    _disabled[k] = v == "1";
                }
            }
        }
        catch (Exception) { return false; }
        return _disabled.TryGetValue(siteId, out bool d) && d;
    }
}
