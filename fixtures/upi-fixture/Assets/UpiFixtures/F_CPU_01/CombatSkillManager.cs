// F-CPU-01 缺陷夹具（tuning 集）：战斗技能管理器
// 缺陷机制（对应 DESIGN §1 链路 CPU/GC）：
//   ① Update 热路径每帧 new List / new Dictionary
//   ② 每帧 LINQ 链式 Where/OrderBy（枚举器 + 委托闭包分配）
//   ③ 每帧 GetComponent 重复查找
//   ④ 每帧枚举 ToString 拼接（字符串分配）
// 修复方向：缓冲复用 / 预生成表 / 组件引用缓存 —— 纯 C#、语义保持 ⇒ auto 试验田
// 注意：本文件属夹具脚本，AGENTS §3 维度一 = blocked，管线修复 Agent 禁止改动，仅人工可改。
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace Upi.Fixtures
{
    public enum SkillState { Idle, Casting, Cooldown, Ready }

    public sealed class CombatSkillManager : MonoBehaviour
    {
        [SerializeField] private int skillCount = 24;

        private readonly List<SkillRuntime> _skills = new List<SkillRuntime>();

        private void Awake()
        {
            for (int i = 0; i < skillCount; i++)
                _skills.Add(new SkillRuntime { index = i, level = 1 + (i % 3), state = (SkillState)(i % 4) });
        }

        private void Update()
        {
            // ①②③④ 全部落在每帧路径上
            var ready = new List<SkillRuntime>();                 // 每帧分配：List
            foreach (var s in _skills)
            {
                if (s.state == SkillState.Ready) ready.Add(s);
            }

            var ranked = ready
                .Where(x => x.level > 1)                          // LINQ 枚举器 + 闭包
                .OrderByDescending(x => x.level)
                .ThenBy(x => x.index)
                .ToList();

            foreach (var s in ranked)
            {
                var body = GetComponent<Rigidbody>();             // 每帧 GetComponent
                var col = GetComponent<CapsuleCollider>();        // 每帧 GetComponent
                var key = s.index + ":" + s.state + ":" + (body != null ? 1 : 0) + (col != null ? 1 : 0); // 枚举 ToString + 字符串拼接
                s.lastKey = key;
                var map = new Dictionary<string, int>();          // 每帧分配：Dictionary
                map[key] = s.level;
                s.score = map[key];
            }

            _cooldownMap = new Dictionary<int, float>();          // 每帧分配：Dictionary
            for (int i = 0; i < _skills.Count; i++)
                _cooldownMap[i] = Time.time + 0.1f * i;
        }

        private Dictionary<int, float> _cooldownMap;

        private sealed class SkillRuntime
        {
            public int index;
            public int level;
            public SkillState state;
            public string lastKey;
            public int score;
        }
    }
}
