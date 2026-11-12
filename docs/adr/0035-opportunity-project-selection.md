# ADR-0035：机会发现与工程选择（v0.17 方向修正）

- 状态：设计已定（2026-11-12）
- 关联：`DESIGN.md` §14.9~§14.11、`docs/迭代计划.md` I7

## 背景

对夹具复盘的结论：

- 夹具多为**主动植入缺陷**（每帧分配、逐实例材质、同步加载），适合做机制回归，
  但不能证明工具能发现真实工程“可做但未做”的优化；
- `Scene_CLEAN` 是理想态，不能作为产品目标/负对照；
- 当前自动修复只对 `upi-fixture` + `F-CPU-01` 生效，模板来自 `_refs` 按行切片，
  属于“已知答案重放”，不是通用 planner；
- UI 没有工程注册/切换/历史/管理，修复对象无法快速设置。

## 决策

1. **Issue / Opportunity / Defect 三分**
   - Issue：越界/回归；
   - Opportunity：未越界但相对可达参考有明显差距；
   - Defect：仅测试夹具，不能当产品目标。
2. **CLEAN 退出产品**
   - 噪声来自同场景重复运行；合格线来自 floor/budget；机会参考来自
     peer/history/static/algorithmic；
   - `zero-to-zero=unassessed`，不算通过。
3. **新增 `OpportunityFinding`**
   - 必须带 `reference_kind/reference_value/reference_ref`；
   - 不进入 `resolved/verified` 分母；用 `gap_closed/regressed` 分账。
4. **去夹具化**
   - `_refs`/`defect_ref`/`fix_subject`/按行切片只允许测试；
   - 产品 planner 从当前源码+证据生成 diff，不得按 `profile_id` 分流。
5. **工程选择/管理（UI）**
   - `ProjectRegistry` 是唯一事实源：路径/场景/质量档/replay/状态/历史；
   - 顶栏切换 + 管理页（新增/编辑/校验/归档/删除）；
   - 活动工程绑定 `project_id`；运行中禁止切换；失效 fail-closed，不回退夹具；
   - **不保留兼容性**：旧 `projects/*.json`、旧 API/CLI 可直接替换；不做迁移层/双读/fallback。
6. **I7 计划**：I7b 工程管理优先，I7c 机会发现，I7d 通用 planner，
   I7e GPU/ANIM/LOAD + 负对照，I7f 真实工程。

## 影响

- 之前“夹具 verified”仍有效，但只作为**已知缺陷修复/验证机器**的证据；
- 不再把 `CLEAN` 当产品概念；
- 真实工程验收必须等 I7b~I7f；
- 现有 `PotentialFinding` 仅覆盖“规模放大风险”，不等于 Opportunity，不能直接复用其语义。

## 边界

- 本 ADR 只改设计与计划，未改代码；
- 工程 registry、机会检测、通用 planner 均未实现；
- 真实用户工程仍未验收。
