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
- 动态棋盘三消（初始 10×10，最难 6×6）；时间驱动回合：时间到才结算；拖拽交换；
- 2.5D 色块、消除/掉落动效、倒计时进度条；
- 单步（含连锁）同一种颜色消除 ≥6 → BONUS 三选一（得分、时间、炸弹），带卡片入场/选择反馈和触发日志；
- 生成式点击/消除/BONUS 音效，无外部音频资源；
- 每 3 轮商店（炸弹/改色/锤子/洗牌），可付费刷新；已购售罄、不能重复购买；
- 商店后难度提升；数字色块 = 叠了 N 个色块，N≥2 不能换位，同色消除/道具每次只拆 1 层；
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
- `legacy_canvas`：每帧写入侧栏尺寸触发 rebuild（尺寸不变，肉眼无差）；
- `legacy_texture`：每格创建独立 Texture2D/Sprite（像素与共享贴图一致，肉眼无差）；
- `legacy_hint`：空闲时每帧全盘扫描提示；
- `legacy_anim`：每帧重算全部格子缩放动画（ANIM，幅度 ≤0.2%，肉眼无差）；
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
- 拖拽 begin/drag/end 已用 ExecuteEvents 验证；`runtime simulate_pointer` 在无 GUI 焦点的自动化会话里不投递事件（实测 dragEvents=0），因此用 ExecuteEvents 走同一 handler 链路并留截图。
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
- `scripts/e2e_real_project.py`：devsim 真实工程闭环 12/12（注册→机会→建单→apply+compile→smoke→回滚）；
- `scripts/e2e_project_onboarding.py`：devsim+testlab 双工程接入 8/8；
- `scripts/e2e_opportunity_ticket.py`：Web 建单/审批/执行/同步模板 7/7。
- `build_opportunity_ticket()`：auto → pending；semi/no-plan → needs_human；
- Web 工单页“从机会生成工单” → `POST /api/tickets/opportunities`。

## 尚未完成

- 音效、粒子、更完整的动画；
- 自动点击 replay 的完整“商店/BONUS”断言（当前可自动跑一局并截图）；
- 真实工程接入。

## 确定性玩法自测（2026-11-12 起）

```powershell
python scripts/test_devsim_game.py        # Play Mode + script run，38 项
python scripts/e2e_devsim_ui.py           # ExecuteEvents 拖拽 + 关键界面截图
python scripts/check_devsim_degrade_visual.py  # 劣化必须肉眼不可辨（像素门禁）
```

- 自测覆盖：交换绑定、无效回弹、既存匹配不误判、数字色块层数语义、道具拖放语义、
  BONUS 同色 6（含 5 个/异色 6 个负例）、时间驱动回合、预告内容、10→6 棋盘、
  商店刷新/售罄/重复购买拦截、动效范围（只动受影响格）、1:1 画质门禁。
- 证据：`runs/devsim_selftest_*.json`、`runs/devsim_ui_e2e_*.json`、
  `runs/devsim_degrade_visual_*.json`、`runs/devsim_v03_*.png`。
- 劣化只允许产生性能差异，不允许肉眼可见差异；视觉门禁要求平均像素差 < 1/255、
  明显差异像素占比 < 0.5%。
