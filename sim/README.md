# devsim — Chroma Rogue vertical slice

> I7g 视觉/交互效果设计：[`docs/视觉设计.md`](../docs/视觉设计.md)。

```text
sim/
  project/        # Unity 工程（I7b 选择这个目录）
  oracle/         # 仅评测；不进 project、不进工具 workspace
  replay/         # 外部 replay / scenario
```

## 当前可玩内容

- 默认场景 `Assets/Scenes/00_Bootstrap.unity`；
- Lobby：开始游戏 / 说明 / 专项测试下拉（选中即启动）；
- 8×8 三消，30s/轮，目标分结转；拖拽交换；
- 2.5D 色块、消除/掉落动效、倒计时进度条；
- 5 连 BONUS 三选一（得分、时间、炸弹），带触发日志；
- 每 5 轮商店（炸弹/改色/锤子/洗牌）；
- 商店后难度提升，高色块带数字护甲，锁位；
- 本地 Top 10（PlayerPrefs，街机式）。

## 运行

```powershell
python sim/replay/devsim_replay.py --case c18
```

`c18` 是混合 case，共 24 个 scenario（18 常规 + 3 混合 + 3 负对照），
定义在 `sim/replay/scenarios.json`；`sim/oracle/cases/*.json` 只给评测器看，
不传给工程或工具。

## 当前性能开关（用于 case 设计，不在 UI 暴露为“缺陷”）

- `legacy_ui`：HUD 每帧刷新；
- `legacy_material`：每块 UI 一个 Material 实例；
- `legacy_match_alloc`：匹配扫描额外分配；
- `sync_shop_load`：打开商店时 `WaitForCompletion` 同步等待 24 个 Addressables 资源；
- `legacy_particle`：每次消除 Instantiate/Destroy UI 粒子；
- `legacy_canvas`：每帧改侧栏尺寸触发 rebuild；
- `legacy_texture`：每格创建独立 Texture2D/Sprite；
- `legacy_hint`：空闲时每帧全盘扫描提示；
- `legacy_anim`：每帧重算 64 格缩放动画（ANIM）；
- `autoplay`/`round_seconds`：仅 replay 用的自动局面，不改变正常游戏。

## 静态机会扫描（I7c 已起步）

```powershell
python scripts/scan_opportunities.py --project sim/project --project-id chroma-rogue
python sim/oracle/evaluate.py --case c18
```

- `OpportunityFinding` 已落模型：必须带 `reference_ref`，不进入 `verified` 计数；
- 当前是**静态模式匹配 + 运行期 feature 门控**：游戏启动时写
  `%TEMP%/devsim_active_features.json`，扫描器只报当前 case 激活的分支；
- 24 case 全跑结果：**recall=1.0、precision=1.0、3 个负对照全过**；
- 拖拽 begin/drag/end 已用 ExecuteEvents 验证（events=3）；真实鼠标手感待验收。
- 若没有运行期 feature 状态，静态扫描仍会报休眠分支（这是已知局限）。

## source-aware planner（I7d 起步）

```powershell
python scripts/plan_opportunity.py --project sim/project --pattern ui_text_rebuild_per_frame
```

- 只读当前源码；
- `find` 必须在当前文件精确命中一次，否则返回 None（转人工）；
- 不读 `_refs`、不按 profile_id 分流；
- 当前覆盖 6 个 devsim pattern（含 sync Addressables → async 模板）；
- `scripts/e2e_devsim_plan.py`：UI/材质已真实 apply+compile+rollback（6/6）；
- `scripts/e2e_opportunity_ticket.py`：Web 建单/审批/执行/同步模板 7/7。
- `build_opportunity_ticket()`：auto → pending；semi/no-plan → needs_human；
- Web 工单页“从机会生成工单” → `POST /api/tickets/opportunities`。

## 尚未完成

- 音效、粒子、更完整的动画；
- 自动点击 replay 的完整“商店/BONUS”断言（当前可自动跑一局并截图）；
- 真实工程接入。
