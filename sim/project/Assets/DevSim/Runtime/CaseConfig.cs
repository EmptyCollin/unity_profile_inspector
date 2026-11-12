using System;
using System.IO;
using UnityEngine;

namespace DevSim
{
    // 由外部 replay 传入的场景配置；工程本身不知道 oracle/期望机会。
    // 轮次/棋盘/商店规则全部走这里，UI 与 replay 共用同一份数值。
    [Serializable]
    public sealed class CaseConfig
    {
        public string case_id = "default";
        public int seed = 20261112;
        public bool legacy_ui = false;
        public bool legacy_material = false;
        public bool legacy_match_alloc = false;
        public bool sync_shop_load = false;
        public bool legacy_particle = false;
        public bool legacy_canvas = false;
        public bool legacy_texture = false;
        public bool legacy_hint = false;
        public bool legacy_anim = false;
        public bool autoplay = false;
        public bool pause_on_bonus = false;

        // ── 回合规则（时间驱动）───────────────────────────────────────
        public float round_seconds = 30f;        // 第 1 轮时长（秒）
        public float round_seconds_step = 0f;    // 每轮时长增量
        public float round_seconds_min = 8f;     // 时长下限
        public int target_base = 600;            // 第 1 轮目标分
        public int target_step = 300;            // 每轮目标分增量
        public int board_start = 10;             // 初始棋盘边长
        public int board_min = 6;                // 最难棋盘边长
        public int board_step_rounds = 2;        // 每 N 轮缩小 1
        public float preview_seconds = 2.5f;     // 回合间预告时长（0 = 需手动继续）
        public float stack_chance_base = 0.05f;  // 新块叠层基础概率
        public float stack_chance_per_tier = 0.06f; // 每级难度追加概率

        // ── 商店 ─────────────────────────────────────────────────────
        public int shop_every = 3;               // 每 N 轮进一次商店
        public int shop_refresh_cost = 40;       // 刷新费用
        public int start_tier = 1;

        public int RoundSecondsFor(int round)
        {
            float v = round_seconds + round_seconds_step * Mathf.Max(0, round - 1);
            return Mathf.Max(1, Mathf.RoundToInt(Mathf.Max(round_seconds_min, v)));
        }

        public int TargetFor(int round)
        {
            return target_base + target_step * Mathf.Max(0, round - 1);
        }

        public int BoardSizeFor(int round)
        {
            int start = Mathf.Clamp(board_start, 4, 12);
            int min = Mathf.Clamp(board_min, 3, start);
            int step = Mathf.Max(1, board_step_rounds);
            int shrink = Mathf.Max(0, round - 1) / step;
            return Mathf.Clamp(start - shrink, min, start);
        }

        public static CaseConfig Load()
        {
            var cfg = new CaseConfig();
            var args = Environment.GetCommandLineArgs();
            string path = null;
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == "--devsim-case") path = args[i + 1];
            }
            if (string.IsNullOrEmpty(path))
                path = Environment.GetEnvironmentVariable("DEVSIM_CASE");
            if (string.IsNullOrEmpty(path))
                path = Path.Combine(Path.GetTempPath(), "devsim_case.json");
            if (!string.IsNullOrEmpty(path) && File.Exists(path))
            {
                try { cfg = JsonUtility.FromJson<CaseConfig>(File.ReadAllText(path)) ?? cfg; }
                catch (Exception e) { Debug.LogWarning("DevSim case config ignored: " + e.Message); }
            }
            cfg.seed = cfg.seed == 0 ? 20261112 : cfg.seed;
            cfg.start_tier = Mathf.Clamp(cfg.start_tier, 1, 6);
            return cfg;
        }
    }
}
