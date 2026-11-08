// UPI T1 常驻越界探针（M2a 核心交付件）
// 每帧读标量 + 绝对线判越 + 越界前后环形缓冲（D17: before=10 after>=14）。
//
// 设计纪律（M1' 观察者污染教训，DESIGN §12）：
//   判定路径零分配——不 new、不装箱、不 ToString、不拼字符串，只写预建数组；
//   控制轮询/心跳/越界落盘（低频）才允许分配。
//   GC recorder 的 LastValue 每帧读一次（结构体返回，无分配），
//   作为 frameTime 的伴随序列一并入环形缓冲。
//   A4 直接类型引用（Editor 程序集可引用 UnityEngine.CoreModule，无需反射——
//   反射 object[] 参数每帧装箱，本身违反零分配纪律）。
//
// 越线 = 外部锚点（帧预算线，ns），Python 侧 thr 命令注入；未设阈值永不误报。
// 文件协议沿用 I9：心跳文件新鲜度判活，控制命令文件 10Hz 轮询（编辑态帧号
// 不推进 ⇒ 控制与心跳必须墙钟节律，T2 实测教训同款）。
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

namespace Upi.Fixtures.EditorTools
{
    [InitializeOnLoad]
    public static class UpiBreachProbe
    {
        const string DIR = @"D:/unity_profile_inspector/runs/_probe";
        const string CTL = DIR + "/pb_cmd.txt";
        const string RES = DIR + "/pb_res.txt";
        const string HB = DIR + "/pb_hb.txt";
        const string EVENTS = DIR + "/pb_events.txt";
        const string BREACH = DIR + "/pb_breach.jsonl";

        const int CAP = 64;      // 主环形缓冲容量（frame 序列）
        const int A4CAP = 128;   // A4 观测缓存容量

        static long _thrNs;              // 帧预算线（ns）；0 = 不设防，永不误报
        static int _seed = -1;
        static int _before = 10;         // D17 默认
        static int _after = 14;          // D17：覆盖 A4 固定 4 帧延迟 + 头部

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
        static long _origin = -1;        // 本次 Play 第一帧（rel 的锚）
        static bool _gcOk;
        static ProfilerRecorder _gcRec;
        static bool _a4Avail = true;

        sealed class Pending
        {
            public int fc; public long ftNs; public long gcBytes;
            public long origin; public int waitLeft;
        }
        static readonly List<Pending> Pend = new List<Pending>();
        static int _cooldown;
        static int _nBreach;

        static DateTime _lastCtl = DateTime.MinValue;
        static DateTime _lastBeat = DateTime.MinValue;

        static bool _inited;

        static UpiBreachProbe()
        {
            EnsureInit();
        }

        static void EnsureInit()
        {
            if (_inited) return;
            _inited = true;
            // InitializeOnLoad 每域恰跑一次 ⇒ 无条件注册（-=: 去重）。
            // 曾用"心跳新鲜则跳过"判活（eval 注入时代的 I9 规则），实测咬人：
            // 心跳文件跨域重载存活而回调注册不存活 ⇒ 域重载后跳过注册 = 死状态。
            Directory.CreateDirectory(DIR);
            EditorApplication.update -= Tick;
            EditorApplication.update += Tick;
            InitGcRecorder();
        }

        // 句柄查找只在装载时跑一次（低频区，反射允许）
        static void InitGcRecorder()
        {
            try
            {
                var hs = new List<ProfilerRecorderHandle>();
                ProfilerRecorderHandle.GetAvailable(hs);
                var dt = typeof(ProfilerRecorderHandle)
                    .GetMethod("GetDescription",
                        BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
                    .ReturnType;
                var np = dt.GetProperty("Name",
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                foreach (var h in hs)
                {
                    object d;
                    try { d = ProfilerRecorderHandle.GetDescription(h); }
                    catch (Exception) { continue; }
                    string n;
                    try { n = (string)np.GetValue(d, null); }
                    catch (Exception) { continue; }
                    if (n == "GC Allocated In Frame")
                    {
                        _gcRec = new ProfilerRecorder(h, 1, ProfilerRecorderOptions.Default);
                        _gcRec.Start();
                        _gcOk = true;
                        return;
                    }
                }
            }
            catch (Exception) { _gcOk = false; }
        }

        static void Tick()
        {
            // ---- 墙钟节流区（低频，允许分配）----
            int fc = Time.frameCount;
            var now = DateTime.UtcNow;
            if ((now - _lastCtl).TotalMilliseconds >= 100) { _lastCtl = now; PollControl(); }
            if ((now - _lastBeat).TotalSeconds >= 1.0)
            {
                _lastBeat = now;
                try { File.WriteAllText(HB, fc.ToString(CultureInfo.InvariantCulture)); }
                catch (Exception) { }
            }

            // ---- 帧门控区（每帧一次，零分配纪律）----
            if (fc == _lastFc) return;
            _lastFc = fc;

            long tsPrev = _prevTs;
            _prevTs = Stopwatch.GetTimestamp();

            if (Application.isPlaying && _origin < 0)
            {
                // T2 实测教训同款：runInBackground 每次域重载被重置，不压上则
                // CLI 驱动下失焦停帧，探针永远等不到发作。
                Application.runInBackground = true;
                _origin = fc;
                LowFreqEvent("origin fc=" + fc + " thr=" + _thrNs + " seed=" + _seed);
            }
            if (!Application.isPlaying && _origin >= 0) _origin = -1;

            if (tsPrev == 0) return;
            long ft = (_prevTs - tsPrev) * 1000000000L / Stopwatch.Frequency;

            // 主环形缓冲（覆盖式）
            int w = fc % CAP;
            FtNs[w] = ft;
            GcB[w] = _gcOk ? (long)_gcRec.LastValue : 0;
            FrFc[w] = fc;

            // A4 观测（仅 Play；每次 1 帧读取，数组预建无装箱）
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

            // 越线判定（绝对锚点；阈值未设 = 0 则永不触发）
            if (_cooldown > 0) _cooldown--;
            // 仅 Play 态判越线；warmup 豁免只盖启动帧（实测 fc=1 帧 6.1s 域重载+首次渲染）。
// 30 帧曾把 F-LOAD 的真实加载尖峰一并豁免——那是要检的缺陷不是启动噪声，降到 2。
            if (Application.isPlaying && _thrNs > 0 && (fc - (int)_origin) > 2 && ft > _thrNs && _cooldown == 0 && Pend.Count < 32)
            {
                var p = new Pending
                {
                    fc = fc, ftNs = ft, gcBytes = GcB[w],
                    origin = _origin, waitLeft = _after + 1
                };
                Pend.Add(p);
                _cooldown = 20;   // 同一发作跨多帧：20 帧冷却防止事件风暴
                _nBreach++;
                LowFreqEvent("breach fc=" + fc + " ns=" + ft);
            }

            // pending 事件等 after 窗口收满再落盘（低频）
            for (int i = Pend.Count - 1; i >= 0; i--)
            {
                var p = Pend[i];
                p.waitLeft--;
                if (p.waitLeft <= 0)
                {
                    Flush(p);
                    Pend.RemoveAt(i);
                }
            }
        }

        // 越界样本落盘（低频区允许分配）：ring 取数 + a4 窗口 + JSON 行
        // 公开入口：编辑态 update 回调饿死（实测），Python 经 eval 反射直接派发命令
        public static string Direct(string cmd)
        {
            // 无条件重挂（-=: 幂等去重）：此前 unload 命令摘掉回调后，
            // EnsureInit 的 _inited 早退会让探针永远睡死——直发入口必须能唤醒它。
            EditorApplication.update -= Tick;
            EditorApplication.update += Tick;
            EnsureInit();
            try { Handle(cmd); } catch (Exception e) { return "EX " + e.Message; }
            try { return File.Exists(RES) ? File.ReadAllText(RES).Trim() : "ERR no-res"; }
            catch (Exception e) { return "ERR read " + e.Message; }
        }

        // 手动把某 rel（相对 origin 帧数）合成为越界样本并立即落盘。
        // 用途：编辑态 after 窗口收不满（帧门控饿死）时，由 Python 在 Play 停后
        // 对已积累的 ring 数据直接出样。before 用真实积累，after 允许短于 14
        // （契约拒收逻辑仍由判定 3 单独验证，与本判据分离）。
        public static void FlushAtRel(int rel)
        {
            EnsureInit();
            if (_origin < 0) return;
            int fc = (int)_origin + rel;
            int s = fc % CAP;
            if (FrFc[s] != fc || FtNs[s] <= 0) return;
            var p = new Pending { fc = fc, ftNs = FtNs[s], gcBytes = GcB[s],
                                  origin = _origin, waitLeft = 0 };
            Flush(p);
        }

        static void Flush(Pending p)
        {
            try
            {
                var sb = new StringBuilder(4096);
                sb.Append('{');
                JN(sb, "frame_idx", p.fc, true);
                JN(sb, "rel", p.origin >= 0 ? p.fc - (int)p.origin : -1, true);
                sb.Append("\"metric\":\"frame_time_ms\",");
                JF(sb, "value", p.ftNs / 1e6, true);
                JF(sb, "threshold_abs", _thrNs / 1e6, true);
                JF(sb, "deviation_ratio", _thrNs > 0 ? p.ftNs / (double)_thrNs : 0.0, true);

                sb.Append("\"ring_before\":[");
                Series(sb, p.fc - _before, p.fc, false);
                sb.Append("],\"ring_after\":[");
                Series(sb, p.fc + 1, p.fc + _after, true);
                sb.Append("],\"gc_ring_before\":[");
                SeriesGc(sb, p.fc - _before, p.fc, false);
                sb.Append("],\"gc_ring_after\":[");
                SeriesGc(sb, p.fc + 1, p.fc + _after, true);
                sb.Append(']');

                // A4 窗口：捕获帧 = 真帧 + 4（固定延迟，D17 向后深度依据）
                sb.Append(",\"a4_window\":[");
                bool first = true;
                for (int f = p.fc - 2; f <= p.fc + _after; f++)
                {
                    int a = ((f % A4CAP) + A4CAP) % A4CAP;
                    if (A4Fc[a] != f) continue;   // 槽位不属于这帧 = 没采到
                    if (!first) sb.Append(',');
                    first = false;
                    sb.Append('{');
                    JN(sb, "frame_idx", f, true);
                    JF(sb, "cpu_frame_time_ms", A4Cpu[a], true);
                    // 哨兵规则（DESIGN §2.1 A4）：gpu=0 且 complete=0 = 未回填 ⇒ null，不得当 0
                    bool unfilled = A4Gpu[a] == 0.0 && A4Ts[a] == 0L;
                    sb.Append("\"gpu_frame_time_ms\":");
                    if (unfilled) sb.Append("null");
                    else sb.Append(A4Gpu[a].ToString("R", CultureInfo.InvariantCulture));
                    sb.Append(',');   // gpu 值与下一字段之间的分隔符
                    JN(sb, "complete_ts", A4Ts[a], false);
                    sb.Append('}');
                }
                sb.Append(']');

                sb.Append(",\"data_tier\":\"A4\"");
                sb.Append(",\"replay_seed\":" + _seed.ToString(CultureInfo.InvariantCulture));
                sb.Append('}');

                File.AppendAllText(BREACH, sb.ToString() + "\n");
                LowFreqEvent("flush fc=" + p.fc + " a4first=" + (first ? "none" : "ok"));
            }
            catch (Exception e) { LowFreqEvent("flush-err " + e.GetType().Name + " " + e.Message); }   // 落盘失败只弃该样本，但必须留痕（探针侧禁止吞异常，DESIGN §12 方法学 c）
        }

        static void Series(StringBuilder sb, int from, int to, bool withFt)
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

        static void SeriesGc(StringBuilder sb, int from, int to, bool unused)
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

        static void LowFreqEvent(string line)
        {
            try { File.AppendAllText(EVENTS, DateTime.Now.ToString("HH:mm:ss") + " " + line + "\n"); }
            catch (Exception) { }
        }

        static void PollControl()
        {
            string cmd = null;
            try { if (File.Exists(CTL)) cmd = File.ReadAllText(CTL).Trim(); }
            catch (Exception) { }
            if (string.IsNullOrEmpty(cmd)) return;
            try { File.Delete(CTL); } catch (Exception) { }
            try { Handle(cmd); } catch (Exception e) { Write(e.ToString()); }
        }

        static void Handle(string cmd)
        {
            int c = cmd.IndexOf(':');
            string head = c < 0 ? cmd : cmd.Substring(0, c);
            string arg = c < 0 ? "" : cmd.Substring(c + 1);
            switch (head)
            {
                case "status":
                    Write("playing=" + Application.isPlaying
                        + " fc=" + Time.frameCount
                        + " origin=" + _origin
                        + " thrNs=" + _thrNs
                        + " seed=" + _seed
                        + " depth=" + _before + "/" + _after
                        + " gc=" + (_gcOk ? 1 : 0)
                        + " a4=" + (_a4Avail ? 1 : 0)
                        + " pend=" + Pend.Count
                        + " breach=" + _nBreach);
                    break;
                case "thr":
                    long ns;
                    if (long.TryParse(arg, NumberStyles.Integer, CultureInfo.InvariantCulture, out ns))
                    {
                        _thrNs = ns;
                        Write("thr=" + ns);
                    }
                    else Write("ERR thr " + arg);
                    break;
                case "seed":
                    int sd;
                    if (int.TryParse(arg, NumberStyles.Integer, CultureInfo.InvariantCulture, out sd))
                    {
                        _seed = sd;
                        Write("seed=" + sd);
                    }
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
                            _after = Math.Max(14, Math.Min(40, a));   // D17 下限硬保 14
                            Write("depth=" + _before + "/" + _after);
                        }
                        else Write("ERR depth " + arg);
                    }
                    break;
                case "gcseq":
                    // 导出主环最近 CAP 帧的逐帧逻辑量序列（低频命令，允许分配）。
                    // J4 判据的本体证据：GC 是逻辑量（分配序列唯一决定），
                    // 同回放两遍必须逐点全等；ft 是墙钟量（vsync/GPU 调度），
                    // 两遍差异只作声明——越线"位置"对墙钟量本就不可复现（实测
                    // spike rel A=[383,708] vs B=[400]），拿它判 FAIL 是判据设计错。
                    {
                        var sb = new StringBuilder(8192);
                        int newest = _lastFc;
                        for (int f = newest - CAP + 1; f <= newest; f++)
                        {
                            int sg = ((f % CAP) + CAP) % CAP;
                            if (FrFc[sg] != f) continue;
                            sb.Append("rel=" + (f - (int)_origin)
                                + " fc=" + f
                                + " gc=" + GcB[sg]
                                + " ft_ns=" + FtNs[sg] + "\n");
                        }
                        try { File.WriteAllText(DIR + "/pb_gcseq.txt", sb.ToString()); }
                        catch (Exception) { }
                        Write("gcseq newest=" + newest);
                    }
                    break;
                case "clear":
                    try { if (File.Exists(BREACH)) File.Delete(BREACH); } catch (Exception) { }
                    try { if (File.Exists(EVENTS)) File.Delete(EVENTS); } catch (Exception) { }
                    _nBreach = 0;
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

        static void Write(string s)
        {
            try { File.WriteAllText(RES, s); } catch (Exception) { }
        }
    }
}