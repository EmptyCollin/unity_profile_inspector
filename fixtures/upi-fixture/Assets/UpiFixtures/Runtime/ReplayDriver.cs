// UPI 确定性回放驱动器（夹具基建 = AGENTS §3 blocked 档，本轮经人工指令开工 M2a 修改）
//
// T2 可复现回放基建（DESIGN §2.4）：回放脚本 = seed + 固定步长 + 时长。
// 同一脚本下场景行为序列逐帧确定 ⇒ 越界样本可复跑、断言可绑定、
// FD 人工下钻可定格到同一相对帧。
//
// 形态决策（沿用 A2 采样器实测教训，I9）：
//   常驻 update 回调 + 文件控制协议；参数持久化到 rp_script.txt——
//   Enter Play 触发域重载会重置静态字段，只认文件不认内存。
//   幂等判据 = 心跳文件新鲜度。
//
// 时基口径（本轮 T2 判据抓出的关键修正）：
//   驱动逻辑不用 Time.time——它是墙钟累加，帧疏密不同 ⇒ 同一相对帧的
//   Time.time 跨运行不可复现，"确定性"静默失效。改用帧索引虚拟时基
//   replayT：每帧恒进 fixedDt×timeScale，ReplayTime 属性供驱动读取。
//
// 对齐探针（arm:stride:count）：从 Origin 起按固定相对帧采样 Mover 位置，
//   同一脚本两次重放逐点比对 = 确定性的直接判据。常态零开销（不装不写）。
//
// 哈希协议（与 Python 侧 scripts/m2a_replay.py 逐字节一致）：
//   "seed;dtNum;dtDen;timeScaleMillis;targetFps;durationMs;assembly=Upi.Fixtures"
//   的 sha256 十六进制前 12 位。字段变更 = 哈希变更 = G7 指纹失配。
using System;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace Upi.Fixtures.Drivers
{
    [InitializeOnLoad]
    public static class ReplayDriver
    {
        const string DIR = @"D:/unity_profile_inspector/runs/_probe";
        const string HB = DIR + "/rp_hb.txt";
        const string CTL = DIR + "/rp_cmd.txt";
        const string RES = DIR + "/rp_res.txt";
        const string EVENTS = DIR + "/rp_events.txt";
        const string FRAMES = DIR + "/rp_frames.txt";
        const string SCRIPT = DIR + "/rp_script.txt";

        static int _seed = 20261108;
        static float _fixedDtNum = 1f, _fixedDtDen = 60f;
        static float _timeScale = 1f;
        static int _targetFps = 60;
        static float _durationS = 120f;
        static int _installedFrame = -1;

        static int _origin = -1;       // 本次 Play 首帧（帧号对齐 t0）
        static int _probeAtRel = -1;   // 已武装的相对帧（-1=未武装）
        static int _probeNext = -1;    // 下一个采样绝对帧
        static int _probeLeft;         // 剩余采样点数
        static int _probeStride = 10;  // 采样间隔（相对帧）
        static float _replayT;         // 帧索引虚拟时基（秒）
        // 武装门控（第二轮彩排实测教训）：驱动器活着就每帧压 targetFrameRate=60
        // 等设置，会污染一切非回放采样窗——F-CPU-01 缺陷态的每帧分配被
        // capped60 形态从 78KB 抬到 183KB，修复效果直接测不出（183→179KB）。
        // 默认**未武装**（与标定态 uncapped 一致）；load: 显式武装，disarm 解除。
        static bool _armed;

        /// <summary>驱动逻辑读取的回放时基（确定性）。未进 Play 时回退墙钟。</summary>
        public static float ReplayTime { get { return Application.isPlaying ? _replayT : Time.time; } }

        static ReplayDriver()
        {
            LoadPersisted();
            EditorApplication.update += Tick;
            EditorApplication.playModeStateChanged += s =>
                Append(EVENTS, "state=" + (int)s + " frame=" + Time.frameCount
                    + " seed=" + _seed + " hash=" + Hash());
        }

        static void LoadPersisted()
        {
            try
            {
                if (!File.Exists(SCRIPT)) return;
                var p = File.ReadAllText(SCRIPT).Trim().Split(':');
                if (p.Length >= 6)
                {
                    _seed = int.Parse(p[0]);
                    _fixedDtNum = int.Parse(p[1]);
                    _fixedDtDen = int.Parse(p[2]);
                    _timeScale = int.Parse(p[3]) / 1000f;
                    _targetFps = int.Parse(p[4]);
                    _durationS = int.Parse(p[5]) / 1000f;
                    if (p.Length >= 7 && p[6] != "-" && p[6] != "")
                    {
                        _probeAtRel = int.Parse(p[6]);
                        _probeStride = p.Length >= 8 ? int.Parse(p[7]) : 10;
                        _probeLeft = p.Length >= 9 ? int.Parse(p[8]) : 12;
                    }
                    // 第 10 字段=武装标志；文件不存在或字段不足 ⇒ 未武装（默认）
                    _armed = p.Length >= 10 && p[9] == "1";
                }
            }
            catch (Exception) { }
        }

        static void Persist()
        {
            WriteAtomic(SCRIPT, _seed + ":" + (int)_fixedDtNum + ":" + (int)_fixedDtDen + ":"
                + Mathf.RoundToInt(_timeScale * 1000) + ":" + _targetFps + ":"
                + Mathf.RoundToInt(_durationS * 1000) + ":"
                + (_probeAtRel < 0 ? "-" : _probeAtRel.ToString()) + ":" + _probeStride
                + ":" + _probeLeft + ":" + (_armed ? "1" : "0"));
        }

        static DateTime _lastCtlT = DateTime.MinValue;
        static DateTime _lastBeatT = DateTime.MinValue;

        static void Tick()
        {
            // 墙钟节流区：编辑态帧号不推进（实测），控制轮询与心跳必须按墙钟走，
            // 否则驱动器在非 Play 状态聋掉。采样写入保持按帧门控（update 频率
            // 约 2100/s ≠ 帧率，I9）——两类节律分开是本轮实测抓出的设计修正。
            var now = DateTime.UtcNow;
            if ((now - _lastCtlT).TotalMilliseconds >= 100) { _lastCtlT = now; PollControl(); }
            if ((now - _lastBeatT).TotalMilliseconds >= 1000)
            {
                _lastBeatT = now;
                try { WriteAtomic(HB, Time.frameCount + ":" + _seed + ":" + Hash()); } catch (Exception) { }
            }

            // Origin 状态机放在墙钟区（本轮实测修正）：帧停滞时帧门控会把整条
            // 复位/武装链一起聋掉——失焦停帧恰恰是停滞最常见成因，二者会死锁。
            int fcn = Time.frameCount;
            if (Application.isPlaying && _origin < 0)
            {
                LoadPersisted();
                _origin = fcn;
                _replayT = 0f;
                // 回放前提=连续出帧。runInBackground 每次域重载被重置（实测），
                // 但仅**武装回放**才需要压它——未武装时压了会改变非回放窗的
                // 出帧形态，构成仪器污染（第二轮彩排实测教训）。
                if (_armed) Application.runInBackground = true;
                Append(EVENTS, "origin frame=" + fcn + " hash=" + Hash()
                    + " seed=" + _seed + " armed=" + _armed);
                if (_armed && _probeAtRel >= 0) _probeNext = fcn + _probeAtRel;
            }
            if (!Application.isPlaying && _origin >= 0) { _origin = -1; _probeNext = -1; }

            int fc = fcn;
            if (fc == _installedFrame) return;
            _installedFrame = fc;

            // 未武装 = 纯旁观：不压任何 Time/帧率设置（标定与验证窗的仪器态
            // 由采样方自设，驱动器活着也不得覆盖——第二轮彩排实测教训）。
            // runInBackground 仅武装回放才需要（进 Play 首帧压一次），不在此列。
            if (!_armed) return;

            Time.fixedDeltaTime = _fixedDtNum / _fixedDtDen;
            Time.maximumDeltaTime = Mathf.Min(1f / 30f, Time.fixedDeltaTime * 4f);
            Time.timeScale = _timeScale;
            Application.targetFrameRate = _targetFps;
            if (Application.isPlaying) _replayT += Time.fixedDeltaTime * _timeScale;

            // 对齐采样序列（仅显式 arm 后启用；常态零写盘）
            if (_probeNext >= 0 && fc >= _probeNext && Application.isPlaying)
            {
                var mv = GameObject.Find("Mover");
                string pos = mv != null
                    ? mv.transform.position.x.ToString("R", CultureInfo.InvariantCulture) + ","
                      + mv.transform.position.z.ToString("R", CultureInfo.InvariantCulture)
                    : "nan";
                Append(FRAMES, "rel=" + (fc - _origin) + " frame=" + fc
                    + " mover=" + pos + " replayT=" + _replayT.ToString("R", CultureInfo.InvariantCulture));
                _probeLeft--;
                if (_probeLeft <= 0) _probeNext = -1;
                else _probeNext = _origin + (fc - _origin) + _probeStride;
            }

        }

        static void PollControl()
        {
            string cmd = null;
            try { if (File.Exists(CTL)) { cmd = File.ReadAllText(CTL).Trim(); } } catch (Exception) { }
            if (string.IsNullOrEmpty(cmd)) return;
            try { File.Delete(CTL); } catch (Exception) { }
            Handle(cmd);
        }

        static void Handle(string cmd)
        {
            if (cmd.StartsWith("load:"))
            {
                // load:seed:dtNum:dtDen:timeScaleMillis:targetFps:durationMillis
                var p = cmd.Split(':');
                if (p.Length >= 7)
                {
                    _seed = int.Parse(p[1]);
                    _fixedDtNum = int.Parse(p[2]);
                    _fixedDtDen = int.Parse(p[3]);
                    _timeScale = int.Parse(p[4]) / 1000f;
                    _targetFps = int.Parse(p[5]);
                    _durationS = int.Parse(p[6]) / 1000f;
                    _armed = true;   // 装载回放脚本即武装（显式，默认态是旁观）
                    Persist();
                    if (Application.isPlaying) UnityEngine.Random.InitState(_seed);
                    WriteAtomic(RES, "loaded+armed " + Hash());
                }
                else WriteAtomic(RES, "bad load arg count");
            }
            else if (cmd == "disarm")
            {
                // 解除武装：驱动器退回纯旁观（不压 Time/帧率/runInBackground）。
                // 采样方自己的仪器状态自己设——驱动器不得越权覆盖。
                _armed = false;
                _probeNext = -1;
                Persist();
                WriteAtomic(RES, "disarmed");
            }
            else if (cmd.StartsWith("arm:"))
            {
                // arm:<relativeFrame>:<stride>:<count> —— 相对 Origin 的固定帧点列，
                // 写进持久化脚本，重载/重进 Play 后依然生效（两 run 同一相对帧采样）
                var p = cmd.Split(':');
                if (p.Length >= 4)
                {
                    _probeAtRel = int.Parse(p[1]);
                    _probeStride = int.Parse(p[2]);
                    _probeLeft = int.Parse(p[3]);
                    _probeNext = _origin >= 0 ? _origin + _probeAtRel : -1;
                    try { if (File.Exists(FRAMES)) File.Delete(FRAMES); } catch (Exception) { }
                    Persist();
                    WriteAtomic(RES, "armed atRel=" + _probeAtRel + " stride=" + _probeStride
                        + " n=" + _probeLeft + " origin=" + _origin + " hash=" + Hash());
                }
                else WriteAtomic(RES, "bad arm arg count");
            }
            else if (cmd == "unload")
            {
                // 标定 L0 参考态需要摘钩（与 UpiBreachProbe.unload 对称）。
                // 注意：进 Play 的域重载会重新装载 ⇒ unload 必须在 Play 之后发。
                EditorApplication.update -= Tick;
                WriteAtomic(RES, "unloaded");
            }
            else if (cmd == "hash") WriteAtomic(RES, Hash());
            else if (cmd == "status")
                // armed 进回执：仪器状态必须可观测——第二轮彩排就是因为
                // 没人能一眼看出驱动器在不在压帧率，两束形态不同查了半天
                WriteAtomic(RES, "seed=" + _seed + " fixedDt=" + (int)_fixedDtNum + "/" + (int)_fixedDtDen
                    + " timeScaleMillis=" + Mathf.RoundToInt(_timeScale * 1000)
                    + " targetFps=" + _targetFps + " durationMillis=" + Mathf.RoundToInt(_durationS * 1000)
                    + " armed=" + _armed
                    + " hash=" + Hash() + " origin=" + _origin
                    + " replayT=" + _replayT.ToString("R", CultureInfo.InvariantCulture));
        }

        static string Hash()
        {
            var canon = _seed + ";" + (int)_fixedDtNum + ";" + (int)_fixedDtDen
                + ";" + Mathf.RoundToInt(_timeScale * 1000) + ";" + _targetFps
                + ";" + Mathf.RoundToInt(_durationS * 1000) + ";assembly=Upi.Fixtures";
            using (var sha = SHA256.Create())
            {
                var b = sha.ComputeHash(Encoding.UTF8.GetBytes(canon));
                var sb = new StringBuilder(12);
                for (int i = 0; i < 6; i++) sb.Append(b[i].ToString("x2"));
                return sb.ToString();
            }
        }

        static void WriteAtomic(string path, string content)
        {
            Directory.CreateDirectory(DIR);
            var tmp = path + ".tmp";
            File.WriteAllText(tmp, content);
            if (File.Exists(path)) File.Delete(path);
            File.Move(tmp, path);
        }

        static void Append(string path, string line)
        {
            try
            {
                Directory.CreateDirectory(DIR);
                File.AppendAllText(path, line + "\n");
            }
            catch (Exception) { }
        }
    }
}
