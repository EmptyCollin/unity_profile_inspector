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

`c18` 是混合 case，共 22 个 scenario（17 常规 + 2 混合 + 3 负对照），
定义在 `sim/replay/scenarios.json`；`sim/oracle/cases/*.json` 只给评测器看，
不传给工程或工具。

## 当前性能开关（用于 case 设计，不在 UI 暴露为“缺陷”）

- `legacy_ui`：HUD 每帧刷新；
- `legacy_material`：每块 UI 一个 Material 实例；
- `legacy_match_alloc`：匹配扫描额外分配；
- `sync_shop_load`：打开商店同步加载 24 个 Resources；
- `legacy_particle`：每次消除 Instantiate/Destroy UI 粒子；
- `legacy_canvas`：每帧改侧栏尺寸触发 rebuild；
- `legacy_texture`：每格创建独立 Texture2D/Sprite；
- `legacy_hint`：空闲时每帧全盘扫描提示；
- `autoplay`/`round_seconds`：仅 replay 用的自动局面，不改变正常游戏。

## 尚未完成

- 22 个 case 的 oracle 已生成，但还没有评测器逐项打分；
- Addressables 分组/加载；
- 音效、粒子、更完整的动画；
- 自动点击 replay 的完整“商店/BONUS”断言（当前可自动跑一局并截图）。
