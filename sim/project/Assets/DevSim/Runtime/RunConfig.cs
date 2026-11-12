using System;
using UnityEngine;

namespace DevSim
{
    // 由外部命令传入的运行配置；工程本身不包含期望结果。
    // 轮次/棋盘/商店规则全部走这里，UI 与 replay 共用同一份数值。
    [Serializable]
    public sealed class RunConfig
    {
        public int seed = 20261112;
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
    }
}
