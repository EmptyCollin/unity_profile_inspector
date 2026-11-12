using System.Collections.Generic;

namespace DevSim
{
    /// <summary>
    /// UI 专项测试与 replay 共用的 case scenario 开关。
    /// 只描述“当前激活哪些开发写法”，不含 oracle/期望机会。
    /// </summary>
    public static class DevSimScenarios
    {
        public static readonly string[] Ids =
        {
            "c01", "c02", "c03", "c04", "c05", "c06", "c07", "c08",
            "c09", "c10", "c11", "c12", "c13", "c14", "c15", "c16",
            "c17", "c18", "c19", "c20", "c21", "c22", "c23", "c24",
        };

        private static readonly Dictionary<string, string[]> Flags = new()
        {
            ["c01"] = new[] { "legacy_ui" },
            ["c02"] = new[] { "legacy_material" },
            ["c03"] = new[] { "legacy_match_alloc" },
            ["c04"] = new[] { "sync_shop_load" },
            ["c05"] = new[] { "legacy_particle" },
            ["c06"] = new[] { "legacy_canvas" },
            ["c07"] = new[] { "legacy_texture" },
            ["c08"] = new[] { "legacy_hint" },
            ["c09"] = new[] { "legacy_ui", "legacy_match_alloc" },
            ["c10"] = new[] { "legacy_material", "legacy_texture" },
            ["c11"] = new[] { "legacy_particle", "legacy_canvas" },
            ["c12"] = new[] { "sync_shop_load", "legacy_material" },
            ["c13"] = new[] { "legacy_hint", "legacy_ui" },
            ["c14"] = new[] { "legacy_particle", "legacy_match_alloc" },
            ["c15"] = new[] { "legacy_texture", "legacy_canvas" },
            ["c16"] = new[] { "legacy_ui", "sync_shop_load" },
            ["c17"] = new[] { "legacy_material", "legacy_hint" },
            ["c18"] = new[] { "legacy_ui", "legacy_material", "legacy_match_alloc", "sync_shop_load" },
            ["c19"] = new[] { "legacy_material", "legacy_particle", "sync_shop_load" },
            ["c20"] = new string[0],
            ["c21"] = new string[0],
            ["c22"] = new string[0],
            ["c23"] = new[] { "legacy_anim" },
            ["c24"] = new[] { "legacy_material", "legacy_anim", "sync_shop_load" },
        };

        public static CaseConfig For(string id)
        {
            var c = new CaseConfig
            {
                case_id = id,
                seed = 20261112 + System.Array.IndexOf(Ids, id),
                start_tier = (id == "c18" || id == "c19" || id == "c24") ? 3 : 1,
                round_seconds = 30f,
                target_base = 600,
                target_step = 300,
                board_start = 10,
                board_min = 6,
                board_step_rounds = 2,
                shop_every = 3,
                preview_seconds = 2.5f,
                autoplay = false,
            };
            if (Flags.TryGetValue(id, out var flags))
            {
                foreach (var f in flags)
                {
                    switch (f)
                    {
                        case "legacy_ui": c.legacy_ui = true; break;
                        case "legacy_material": c.legacy_material = true; break;
                        case "legacy_match_alloc": c.legacy_match_alloc = true; break;
                        case "sync_shop_load": c.sync_shop_load = true; break;
                        case "legacy_particle": c.legacy_particle = true; break;
                        case "legacy_canvas": c.legacy_canvas = true; break;
                        case "legacy_texture": c.legacy_texture = true; break;
                        case "legacy_hint": c.legacy_hint = true; break;
                        case "legacy_anim": c.legacy_anim = true; break;
                    }
                }
            }
            return c;
        }

        public static string Label(string id)
        {
            string name = id switch
            {
                "c01" => "HUD", "c02" => "材质", "c03" => "匹配分配",
                "c04" => "同步加载", "c05" => "粒子", "c06" => "布局",
                "c07" => "纹理", "c08" => "提示", "c09" => "HUD+分配",
                "c10" => "材质+纹理", "c11" => "粒子+布局", "c12" => "加载+材质",
                "c13" => "提示+HUD", "c14" => "粒子+分配", "c15" => "纹理+布局",
                "c16" => "HUD+加载", "c17" => "材质+提示", "c18" => "混合",
                "c19" => "混合", "c20" => "负对照", "c21" => "负对照",
                "c22" => "负对照", "c23" => "动画", "c24" => "GPU/ANIM/LOAD",
                _ => id,
            };
            return id + " · " + name;
        }
    }
}
