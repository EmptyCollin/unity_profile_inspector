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

        // M3 修复（PB-FULL-REUSE，行段 21-27 替换）：缓存 + 预生成。语义等价
        // 前提——state/level 在 Awake 后不再被改写（Update 全文无赋值点，已核对），
        // 故 ready 筛选、排序结果、key 字符串均为常量，可一次预生成。
        private Rigidbody _body;
        private CapsuleCollider _col;
        private List<SkillRuntime> _readyRanked;
        private string[] _keyByIndex;
        private readonly Dictionary<int, float> _reusedCooldown = new Dictionary<int, float>();

        private void Awake()
        {
            for (int i = 0; i < skillCount; i++)
                _skills.Add(new SkillRuntime { index = i, level = 1 + (i % 3), state = (SkillState)(i % 4) });

            _body = GetComponent<Rigidbody>();
            _col = GetComponent<CapsuleCollider>();
            // 语义等价关键：原 Update 先筛 state==Ready 再筛 level>1 再排序。
            // 预生成必须取同一交集——若漏掉 Ready 筛选，非 Ready 技能的
            // score/lastKey 会从默认值变成被赋值，即改变可见行为（实测复查抓出）。
            _readyRanked = _skills.Where(x => x.state == SkillState.Ready)
                                   .Where(x => x.level > 1)
                                   .OrderByDescending(x => x.level).ThenBy(x => x.index).ToList();
            _keyByIndex = new string[skillCount];
            foreach (var s in _skills)
                _keyByIndex[s.index] = s.index + ":" + s.state + ":"
                    + (_body != null ? 1 : 0) + (_col != null ? 1 : 0);
        }

        private void Update()
        {
            // 每帧分配清零（①②③④ 逐条对应证据，行段 31-57 替换）：
            foreach (var s in _readyRanked)
            {
                var key = _keyByIndex[s.index];
                s.lastKey = key;
                // 原逻辑 map[key]=s.level; s.score=map[key]; map 每帧新建且
                // 只含该 key ⇒ s.score 恒等于 s.level，直取（字符串+字典分配消）
                s.score = s.level;
            }

            _reusedCooldown.Clear();
            for (int i = 0; i < _skills.Count; i++)
                _reusedCooldown[i] = Time.time + 0.1f * i;
            _cooldownMap = _reusedCooldown;
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
