# devsim — Chroma Rogue vertical slice

```text
sim/
  project/        # Unity 工程（I7b 选择这个目录）
  oracle/         # 仅评测；不进 project、不进工具 workspace
  replay/         # 外部 replay / scenario
```

## 当前可玩内容

- 默认场景 `Assets/Scenes/00_Bootstrap.unity`；
- Lobby：开始游戏 / 商店练习 / 背包说明；
- 8×8 三消，30s/轮，目标分结转；
- 5 连 BONUS 三选一（得分、时间、炸弹）；
- 每 5 轮商店（炸弹/改色/锤子/洗牌）；
- 商店后难度提升，高色块带数字护甲，锁位；
- 本地 Top 10（PlayerPrefs，街机式）。

## 运行

```powershell
python sim/replay/devsim_replay.py --case c18
```

`c18` 场景配置在 `sim/replay/scenarios.json`，只含 seed/性能开关，不含答案。
`sim/oracle/cases/c18.json` 只给评测器看，不传给工程或工具。

## 当前性能开关（用于 case 设计，不在 UI 暴露为“缺陷”）

- `legacy_ui`：HUD 每帧刷新；
- `legacy_material`：每块 UI 一个 Material 实例；
- `legacy_match_alloc`：匹配扫描额外分配；
- `sync_shop_load`：打开商店同步加载 24 个 Resources。

## 尚未完成

- 22 个 case 的完整矩阵（当前只有 c18/c20 的 scenario）；
- Addressables 分组/加载；
- 音效、粒子、更完整的动画；
- 自动点击 replay（当前只启动并截图，UI 可手动玩）。
