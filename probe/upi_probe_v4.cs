// probe/upi_probe_v4.cs —— 统一探针（I1b）
//
// 一个回调、一个心跳、一条事件流：breach（确定越界）+ sample（窗口抽检）。
// 迁移期说明：
//  - 旧 `UpiBreachProbe`（Editor 常驻）与 `sampler_v3`（eval 注入）继续保留，
//    但新会话应只装本文件；本文件同时写 `probe_events.jsonl`（统一事件）
//    与 `pb_breach.jsonl`（旧消费方兼容），心跳统一为 `pb_hb.txt`。
//  - 事件格式对齐 `upi/core/probe_events.py` 的 ProbeEvent：
//    breach: kind/metric/value/threshold/ring_*/a4_window/data_tier/replay_seed
//    sample: kind/metric/series/frame_idx/rel_frame/source
//  - sample 默认关（用户/上层用 `sample:on[:window]` 开）；避免默认改变旧行为。
//
// 控制（文件，10Hz 轮询）：probe_cmd.txt
//   status | thr:<ns> | gcthr:<bytes> | seed:<int> | depth:<before>:<after>
//   sample:on[:window] | sample:off | clear | unload
// 结果：probe_res.txt；状态：probe_status.json；心跳：pb_hb.txt
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;
using Unity.Profiling;
using Unity.Profiling.LowLevel.Unsafe;

UpiProbeV4.Main();
return UpiProbeV4.LastLine;

public static class UpiProbeV4
{
    const string Dir = @"D:/unity_profile_inspector/runs/_probe";
    const string Ctl = Dir + "/probe_cmd.txt";
    const string Res = Dir + "/probe_res.txt";
    const string Hb = Dir + "/pb_hb.txt";
    const string Status = Dir + "/probe_status.json";
    const string Events = Dir + "/probe_events.jsonl";
    const string LegacyBreach = Dir + "/pb_breach.jsonl";

    const int CAP = 64;
    const int A4CAP = 128;
    const int SAMPLE_CAP = 600;

    static long _thrNs;
    static long _gcThrBytes;
    static int _seed = -1;
    static int _before = 10;
    static int _after = 14;

    static readonly long[] FtNs = new long[CAP];
    static readonly long[] GcB = new long[CAP];
    static readonly int[] FrFc = new int[CAP];

    static readonly double[] A4Cpu = new double[A4CAP];
    static readonly double[] A4Gpu = new double[A4CAP];
    static readonly long[] A4Ts = new long[A4CAP];
    static readonly int[] A4Fc = new int[A4CAP];
    static readonly FrameTiming[] A4One = new FrameTiming[1];

    static int _lastFc = -1;
    static long _prevTs;
    static long _origin = -1;
    static bool _a4Avail = true;

    sealed class Pending
    {
        public int fc; public long ftNs; public long gcBytes;
        public long origin; public int waitLeft;
        public bool gc;
    }
    static readonly List<Pending> Pend = new List<Pending>();
    static int _cooldown;
    static int _nBreach;

    static DateTime _lastCtl = DateTime.MinValue;
    static DateTime _lastBeat = DateTime.MinValue;
    static bool _inited;

    // ── ProfilerRecorder 采样（从 sampler_v3 合并；双条件定位）──
    static readonly Dictionary<string, ProfilerRecorder> Rec = new Dictionary<string, ProfilerRecorder>();
    static readonly Dictionary<string, long[]> Ring = new Dictionary<string, long[]>();
    static readonly List<string> Skipped = new List<string>();
    static readonly Dictionary<string, int> Shadow = new Dictionary<string, int>();

    static readonly string[][] Targets = {
        new[] { "Render", "Draw Calls Count",           "Render/DrawCalls" },
        new[] { "Render", "SetPass Calls Count",        "Render/SetPassCalls" },
        new[] { "Render", "Triangles Count",            "Render/Triangles" },
        new[] { "Render", "Vertices Count",             "Render/Vertices" },
        new[] { "Render", "Indices Count",              "Render/Indices" },
        new[] { "Render", "Dynamic Batches Count",      "Render/DynBatches" },
        new[] { "Render", "Instanced Batches Count",    "Render/InstBatches" },
        new[] { "Render", "Static Batches Count",       "Render/StaticBatches" },
        new[] { "Render", "CPU Main Thread Frame Time", "Render/CpuMainThreadFrameNs" },
        new[] { "Render", "CPU Total Frame Time",       "Render/CpuTotalFrameNs" },
        new[] { "Render", "GPU Frame Time",             "Render/GpuFrameNs" },
        new[] { "Memory", "Total Used Memory",          "Memory/TotalUsedBytes" },
        new[] { "Memory", "GC Allocated In Frame",      "Memory/GcAllocInFrameBytes" },
    };
    static readonly string[] WantNames = {
        "Draw Calls Count", "SetPass Calls Count", "Triangles Count",
        "Vertices Count", "Indices Count", "Dynamic Batches Count",
        "Instanced Batches Count", "Static Batches Count",
        "CPU Main Thread Frame Time", "CPU Total Frame Time", "GPU Frame Time",
        "Total Used Memory", "GC Allocated In Frame",
    };

    static bool _sampleOn;
    static int _sampleWindow = 300;
    static int _sampleCount;
    static int _sampleFrame = -1;

    public static string LastLine = "";

    static bool Alive()
    {
        try
        {
            if (!File.Exists(Hb)) return false;
            var t = long.Parse(File.ReadAllText(Hb));
            var age = (DateTime.UtcNow - new DateTime(t, DateTimeKind.Utc)).TotalSeconds;
            return age < 5.0;
        }
        catch (Exception) { return false; }
    }

    public static void Main()
    {
        Directory.CreateDirectory(Dir);
        if (Alive())
        {
            LastLine = "alive " + StatusLine();
            return;
        }
        int built = BuildRecorders();
        EditorApplication.update -= Tick;
        EditorApplication.update += Tick;
        _inited = true;
        Beat();
        LastLine = "installed recorders=" + built + "/" + Targets.Length
                   + " skipped=[" + string.Join(",", Skipped.ToArray()) + "]";
    }

    static int BuildRecorders()
    {
        Rec.Clear(); Ring.Clear(); Skipped.Clear(); Shadow.Clear();
        _sampleCount = 0; _sampleFrame = -1;
        try
        {
            var located = new Dictionary<string, ProfilerRecorderHandle>();
            var handles = new List<ProfilerRecorderHandle>();
            ProfilerRecorderHandle.GetAvailable(handles);
            var getDesc = typeof(ProfilerRecorderHandle).GetMethod("GetDescription",
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
            var dt = getDesc.ReturnType;
            var nameProp = dt.GetProperty("Name",
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            var catField = dt.GetField("category",
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            var catNameProp = catField != null
                ? catField.FieldType.GetProperty("Name",
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
                : null;

            foreach (var h in handles)
            {
                object d; try { d = getDesc.Invoke(null, new object[] { h }); } catch { continue; }
                string n; try { n = (string)nameProp.GetValue(d, null); } catch { continue; }
                string cn = "?";
                try { cn = (string)catNameProp.GetValue(catField.GetValue(d), null); } catch { }
                foreach (var w in WantNames)
                {
                    if (n != w) continue;
                    foreach (var t in Targets)
                        if (t[1] == w)
                        {
                            if (cn == t[0]) { if (!located.ContainsKey(w)) located[w] = h; }
                            else { Shadow[w] = (Shadow.ContainsKey(w) ? Shadow[w] : 0) + 1; }
                            break;
                        }
                }
            }

            foreach (var t in Targets)
            {
                string key = t[2];
                ProfilerRecorderHandle h;
                if (!located.TryGetValue(t[1], out h)) { Skipped.Add(key + "(未注册)"); continue; }
                try
                {
                    var r = new ProfilerRecorder(h, 3000, ProfilerRecorderOptions.StartImmediately);
                    Rec[key] = r;
                    Ring[key] = new long[SAMPLE_CAP];
                }
                catch (Exception) { Skipped.Add(key + "(构造失败)"); }
            }
        }
        catch (Exception e)
        {
            LastLine = "install_error " + e.GetType().Name + " " + e.Message;
        }
        return Rec.Count;
    }

    static void Tick()
    {
        int fc = Time.frameCount;
        var now = DateTime.UtcNow;
        if ((now - _lastCtl).TotalMilliseconds >= 100) { _lastCtl = now; PollControl(); }
        if ((now - _lastBeat).TotalSeconds >= 1.0)
        {
            _lastBeat = now;
            Beat();
        }

        if (fc == _lastFc) return;
        _lastFc = fc;

        long tsPrev = _prevTs;
        _prevTs = Stopwatch.GetTimestamp();

        if (Application.isPlaying && _origin < 0)
        {
            Application.runInBackground = true;
            _origin = fc;
            _sampleCount = 0; _sampleFrame = -1;
            LowFreq("origin fc=" + fc + " thr=" + _thrNs + " seed=" + _seed);
        }
        if (!Application.isPlaying && _origin >= 0) _origin = -1;

        if (tsPrev == 0) return;
        long ft = (_prevTs - tsPrev) * 1000000000L / Stopwatch.Frequency;

        int w = fc % CAP;
        FtNs[w] = ft;
        GcB[w] = GcNow();
        FrFc[w] = fc;

        if (_a4Avail && Application.isPlaying)
        {
            try
            {
                FrameTimingManager.CaptureFrameTimings();
                uint got = FrameTimingManager.GetLatestTimings(1u, A4One);
                if (got == 1)
                {
                    FrameTiming t = A4One[0];
                    int a = fc % A4CAP;
                    A4Cpu[a] = t.cpuFrameTime;
                    A4Gpu[a] = t.gpuFrameTime;
                    A4Ts[a] = (long)t.cpuTimeFrameComplete;
                    A4Fc[a] = fc;
                }
            }
            catch (Exception) { _a4Avail = false; }
        }

        if (_cooldown > 0) _cooldown--;
        if (Application.isPlaying && _thrNs > 0 && (fc - (int)_origin) > 2
            && ft > _thrNs && _cooldown == 0 && Pend.Count < 32)
        {
            var p = new Pending
            {
                fc = fc, ftNs = ft, gcBytes = GcB[w],
                origin = _origin, waitLeft = _after + 1
            };
            Pend.Add(p);
            _cooldown = 20;
            _nBreach++;
            LowFreq("breach fc=" + fc + " ns=" + ft);
        }

        // GC 分配越界（gcthr:<bytes>）：最近 30 帧中至少 24 帧超过阈值。
        // 单帧阈值会被修复态的零散 GC 尖峰误触发；窗口化只抓持续分配。
        if (Application.isPlaying && _gcThrBytes > 0 && (fc - (int)_origin) >= 30
            && _cooldown == 0 && Pend.Count < 32)
        {
            int over = 0;
            long[] tmp = new long[30];
            for (int i = 0; i < 30; i++)
            {
                int idx = ((w - i) % CAP + CAP) % CAP;
                tmp[i] = GcB[idx];
                if (GcB[idx] > _gcThrBytes) over++;
            }
            if (over >= 24)
            {
                Array.Sort(tmp);
                var p = new Pending
                {
                    fc = fc, ftNs = ft, gcBytes = tmp[15], gc = true,
                    origin = _origin, waitLeft = _after + 1
                };
                Pend.Add(p);
                _cooldown = 20;
                _nBreach++;
                LowFreq("gc-window-breach fc=" + fc + " over=" + over
                        + " med=" + tmp[15]);
            }
        }

        for (int i = Pend.Count - 1; i >= 0; i--)
        {
            var p = Pend[i];
            p.waitLeft--;
            if (p.waitLeft <= 0)
            {
                FlushBreach(p);
                Pend.RemoveAt(i);
            }
        }

        // 窗口抽检：每帧一个值写进定长环；满窗写一次事件。
        if (_sampleOn && Application.isPlaying && Rec.Count > 0 && fc != _sampleFrame)
        {
            _sampleFrame = fc;
            foreach (var k in Rec.Keys)
            {
                var arr = Ring[k];
                arr[_sampleCount % SAMPLE_CAP] = (long)Rec[k].LastValue;
            }
            _sampleCount++;
            if (_sampleCount >= _sampleWindow)
            {
                FlushSamples(fc);
                _sampleCount = 0;
            }
        }
    }

    static long GcNow()
    {
        ProfilerRecorder r;
        if (Rec.TryGetValue("Memory/GcAllocInFrameBytes", out r))
        {
            try { return (long)r.LastValue; } catch (Exception) { return 0; }
        }
        return 0;
    }

    static void FlushBreach(Pending p)
    {
        try
        {
            if (p.gc)
            {
                Append(Events, GcBreachJson(p));
                LowFreq("flush-gc fc=" + p.fc + " bytes=" + p.gcBytes);
                return;
            }
            bool a4Any;
            // 统一事件带 kind/source/threshold；旧 pb_breach 必须严格保持
            // BreachSample 契约（extra="forbid"），不能混入新字段，否则旧判定
            // 会整行拒收。两条线格式分开构造。
            string unified = BreachJson(p, true, out a4Any);
            string legacy = BreachJson(p, false, out _);
            Append(Events, unified);
            Append(LegacyBreach, legacy);
            LowFreq("flush fc=" + p.fc + " a4first=" + (a4Any ? "ok" : "none"));
        }
        catch (Exception e) { LowFreq("flush-err " + e.GetType().Name + " " + e.Message); }
    }

    static string GcBreachJson(Pending p)
    {
        var sb = new StringBuilder(2048);
        sb.Append('{');
        sb.Append("\"kind\":\"breach\",\"source\":\"unified_probe\",");
        JN(sb, "frame_idx", p.fc, true);
        JN(sb, "rel", p.origin >= 0 ? p.fc - (int)p.origin : -1, true);
        JN(sb, "rel_frame", p.origin >= 0 ? p.fc - (int)p.origin : -1, true);
        sb.Append("\"metric\":\"gc_alloc_bytes\",");
        JN(sb, "value", p.gcBytes, true);
        JN(sb, "threshold", _gcThrBytes, true);
        JN(sb, "threshold_abs", _gcThrBytes, true);
        JF(sb, "deviation_ratio", _gcThrBytes > 0
            ? p.gcBytes / (double)_gcThrBytes : 0.0, true);
        sb.Append("\"ring_before\":[");
        SeriesGc(sb, p.fc - _before, p.fc);
        sb.Append("],\"ring_after\":[");
        SeriesGc(sb, p.fc + 1, p.fc + _after);
        sb.Append(']');
        sb.Append(",\"data_tier\":\"A2\"");
        sb.Append(",\"replay_seed\":" + _seed.ToString(CultureInfo.InvariantCulture));
        sb.Append('}');
        return sb.ToString();
    }

    static string BreachJson(Pending p, bool unified, out bool a4Any)
    {
        var sb = new StringBuilder(4096);
        sb.Append('{');
        if (unified) sb.Append("\"kind\":\"breach\",\"source\":\"unified_probe\",");
        JN(sb, "frame_idx", p.fc, true);
        JN(sb, "rel", p.origin >= 0 ? p.fc - (int)p.origin : -1, true);
        if (unified)
            JN(sb, "rel_frame", p.origin >= 0 ? p.fc - (int)p.origin : -1, true);
        sb.Append("\"metric\":\"frame_time_ms\",");
        JF(sb, "value", p.ftNs / 1e6, true);
        if (unified) JF(sb, "threshold", _thrNs / 1e6, true);
        JF(sb, "threshold_abs", _thrNs / 1e6, true);
        JF(sb, "deviation_ratio", _thrNs > 0 ? p.ftNs / (double)_thrNs : 0.0, true);

        sb.Append("\"ring_before\":[");
        Series(sb, p.fc - _before, p.fc);
        sb.Append("],\"ring_after\":[");
        Series(sb, p.fc + 1, p.fc + _after);
        sb.Append("],\"gc_ring_before\":[");
        SeriesGc(sb, p.fc - _before, p.fc);
        sb.Append("],\"gc_ring_after\":[");
        SeriesGc(sb, p.fc + 1, p.fc + _after);
        sb.Append(']');

        sb.Append(",\"a4_window\":[");
        a4Any = false;
        for (int f = p.fc - 2; f <= p.fc + _after; f++)
        {
            int a = ((f % A4CAP) + A4CAP) % A4CAP;
            if (A4Fc[a] != f) continue;   // 槽位不属于这帧 = 没采到
            if (a4Any) sb.Append(',');
            a4Any = true;
            sb.Append('{');
            JN(sb, "frame_idx", f, true);
            JF(sb, "cpu_frame_time_ms", A4Cpu[a], true);
            bool unfilled = A4Gpu[a] == 0.0 && A4Ts[a] == 0L;
            sb.Append("\"gpu_frame_time_ms\":");
            if (unfilled) sb.Append("null");
            else sb.Append(A4Gpu[a].ToString("R", CultureInfo.InvariantCulture));
            sb.Append(',');
            JN(sb, "complete_ts", A4Ts[a], false);
            sb.Append('}');
        }
        sb.Append(']');

        sb.Append(",\"data_tier\":\"A4\"");
        sb.Append(",\"replay_seed\":" + _seed.ToString(CultureInfo.InvariantCulture));
        sb.Append('}');
        return sb.ToString();
    }

    static void FlushSamples(int fc)
    {
        try
        {
            int n = _sampleCount;
            int rel = _origin >= 0 ? fc - (int)_origin : -1;
            foreach (var k in Rec.Keys)
            {
                var arr = Ring[k];
                var sb = new StringBuilder(4096);
                sb.Append("{\"kind\":\"sample\",\"source\":\"unified_probe\",");
                sb.Append("\"data_tier\":\"A2\",");
                JN(sb, "frame_idx", fc, true);
                JN(sb, "rel_frame", rel, true);
                sb.Append("\"metric\":\"").Append(k).Append("\",");
                sb.Append("\"series\":[");
                for (int i = 0; i < n; i++)
                {
                    if (i > 0) sb.Append(',');
                    sb.Append(arr[i % SAMPLE_CAP].ToString(CultureInfo.InvariantCulture));
                }
                sb.Append("],\"stats\":{\"n\":").Append(n).Append("}}");
                Append(Events, sb.ToString());
            }
            LowFreq("sample fc=" + fc + " n=" + n + " metrics=" + Rec.Count);
        }
        catch (Exception e) { LowFreq("sample-err " + e.GetType().Name + " " + e.Message); }
    }

    static void Series(StringBuilder sb, int from, int to)
    {
        bool first = true;
        for (int f = from; f <= to; f++)
        {
            int s = ((f % CAP) + CAP) % CAP;
            if (FrFc[s] != f) continue;
            if (!first) sb.Append(',');
            first = false;
            sb.Append((FtNs[s] / 1e6).ToString("R", CultureInfo.InvariantCulture));
        }
    }

    static void SeriesGc(StringBuilder sb, int from, int to)
    {
        bool first = true;
        for (int f = from; f <= to; f++)
        {
            int s = ((f % CAP) + CAP) % CAP;
            if (FrFc[s] != f) continue;
            if (!first) sb.Append(',');
            first = false;
            sb.Append(GcB[s].ToString(CultureInfo.InvariantCulture));
        }
    }

    static void JN(StringBuilder sb, string k, long v, bool comma)
    {
        sb.Append('"').Append(k).Append("\":").Append(v.ToString(CultureInfo.InvariantCulture));
        if (comma) sb.Append(',');
    }

    static void JF(StringBuilder sb, string k, double v, bool comma)
    {
        sb.Append('"').Append(k).Append("\":").Append(v.ToString("R", CultureInfo.InvariantCulture));
        if (comma) sb.Append(',');
    }

    static void Append(string path, string line)
    {
        try
        {
            // 事件文件是只追加的；长跑会涨。超过 32MB 时滚动一份 .1，
            // 避免 Web/统计每次读全文件越来越慢（I1b 的轻量约束）。
            var fi = new FileInfo(path);
            if (fi.Exists && fi.Length > 32L * 1024 * 1024)
            {
                string old = path + ".1";
                try { if (File.Exists(old)) File.Delete(old); } catch (Exception) { }
                try { File.Move(path, old); } catch (Exception) { }
            }
            File.AppendAllText(path, line + "\n");
        }
        catch (Exception) { }
    }

    static void LowFreq(string line)
    {
        try { File.AppendAllText(Dir + "/probe_events.txt",
            DateTime.Now.ToString("HH:mm:ss") + " " + line + "\n"); }
        catch (Exception) { }
    }

    static void PollControl()
    {
        string cmd = null;
        try { if (File.Exists(Ctl)) cmd = File.ReadAllText(Ctl).Trim(); }
        catch (Exception) { }
        if (string.IsNullOrEmpty(cmd)) return;
        try { File.Delete(Ctl); } catch (Exception) { }
        try { Handle(cmd); } catch (Exception e) { Write(e.ToString()); }
    }

    public static string Direct(string cmd)
    {
        // 不在每次 Direct 里重挂回调：旧程序集的 Tick 卸不掉，重复挂会变成
        // 多个回调争抢同一文件（I9 实测）。Main() 只在心跳不新鲜时安装一次。
        if (!_inited) Main();
        try { Handle(cmd); } catch (Exception e) { return "EX " + e.Message; }
        try { return File.Exists(Res) ? File.ReadAllText(Res).Trim() : "ERR no-res"; }
        catch (Exception e) { return "ERR read " + e.Message; }
    }

    static void Handle(string cmd)
    {
        int c = cmd.IndexOf(':');
        string head = c < 0 ? cmd : cmd.Substring(0, c);
        string arg = c < 0 ? "" : cmd.Substring(c + 1);
        switch (head)
        {
            case "status":
                Write(StatusLine());
                break;
            case "thr":
                long ns;
                if (long.TryParse(arg, NumberStyles.Integer, CultureInfo.InvariantCulture, out ns))
                { _thrNs = ns; Write("thr=" + ns); }
                else Write("ERR thr " + arg);
                break;
            case "gcthr":
                long gc;
                if (long.TryParse(arg, NumberStyles.Integer, CultureInfo.InvariantCulture, out gc))
                { _gcThrBytes = gc; Write("gcthr=" + gc); }
                else Write("ERR gcthr " + arg);
                break;
            case "seed":
                int sd;
                if (int.TryParse(arg, NumberStyles.Integer, CultureInfo.InvariantCulture, out sd))
                { _seed = sd; Write("seed=" + sd); }
                else Write("ERR seed " + arg);
                break;
            case "depth":
                var ps = arg.Split(':');
                if (ps.Length >= 2)
                {
                    int b, a;
                    if (int.TryParse(ps[0], out b) && int.TryParse(ps[1], out a))
                    {
                        _before = Math.Max(1, Math.Min(32, b));
                        _after = Math.Max(14, Math.Min(40, a));
                        Write("depth=" + _before + "/" + _after);
                    }
                    else Write("ERR depth " + arg);
                }
                break;
            case "sample":
                if (arg.StartsWith("on"))
                {
                    var xs = arg.Split(':');
                    int win;
                    if (xs.Length > 1 && int.TryParse(xs[1], out win))
                        _sampleWindow = Math.Max(30, Math.Min(SAMPLE_CAP, win));
                    _sampleOn = true; _sampleCount = 0; _sampleFrame = -1;
                    Write("sample=on window=" + _sampleWindow);
                }
                else
                {
                    _sampleOn = false; _sampleCount = 0;
                    Write("sample=off");
                }
                break;
            case "clear":
                try { if (File.Exists(Events)) File.Delete(Events); } catch (Exception) { }
                try { if (File.Exists(LegacyBreach)) File.Delete(LegacyBreach); } catch (Exception) { }
                _nBreach = 0;
                _gcThrBytes = 0;
                Write("cleared");
                break;
            case "unload":
                EditorApplication.update -= Tick;
                Write("unloaded");
                break;
            default:
                Write("ERR cmd " + cmd);
                break;
        }
    }

    static string StatusLine()
    {
        return "playing=" + Application.isPlaying
            + " fc=" + Time.frameCount
            + " origin=" + _origin
            + " thrNs=" + _thrNs
            + " gcThr=" + _gcThrBytes
            + " gcWin=24/30"
            + " seed=" + _seed
            + " depth=" + _before + "/" + _after
            + " rec=" + Rec.Count
            + " skipped=" + Skipped.Count
            + " sample=" + (_sampleOn ? "on" : "off")
            + " sw=" + _sampleWindow
            + " sc=" + _sampleCount
            + " a4=" + (_a4Avail ? 1 : 0)
            + " pend=" + Pend.Count
            + " breach=" + _nBreach;
    }

    static void Write(string s)
    {
        try { File.WriteAllText(Res, s); } catch (Exception) { }
        try
        {
            SafeWrite(Status, "{\"state\":\"" + (s.StartsWith("ERR") ? "error" : "ok")
                + "\",\"line\":\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"")
                + "\",\"time\":\"" + DateTime.UtcNow.ToString("o") + "\"}");
        }
        catch (Exception) { }
    }

    static void Beat()
    {
        try { File.WriteAllText(Hb, DateTime.UtcNow.Ticks.ToString(CultureInfo.InvariantCulture)); }
        catch (Exception) { }
    }

    static void SafeWrite(string path, string content)
    {
        try
        {
            string tmp = path + ".tmp." + Guid.NewGuid().ToString("N").Substring(0, 6);
            File.WriteAllText(tmp, content);
            if (File.Exists(path)) File.Delete(path);
            File.Move(tmp, path);
        }
        catch (Exception) { }
    }
}
