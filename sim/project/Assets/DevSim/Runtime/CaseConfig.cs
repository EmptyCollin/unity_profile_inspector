using System;
using System.IO;
using UnityEngine;

namespace DevSim
{
    // 由外部 replay 传入的场景配置；工程本身不知道 oracle/期望机会。
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
        public float round_seconds = 30f;
        public int target_base = 600;
        public int start_tier = 1;

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
