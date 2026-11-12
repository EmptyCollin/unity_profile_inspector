# UPI Web 控制台设计（UI 唯一人机通道）

> 状态：I0/I1a 骨架已落地；I3/I4/I5 逐步接功能。
> 关联：`docs/adr/0002-ui-stack.md`、`docs/迭代计划.md`、`upi/agents/webui.py`。

## 1. 设计原则

1. **UI 是唯一人机通道**：日常操作不读 JSON、不敲 CLI、不手写 decision。
2. **三问**：现在发生了什么？要我决定什么？上次修/验的结果是什么？
3. **证据可下钻，但主视图不是 JSON**：卡片、表格、时间线、矩阵。
4. **危险操作要读完再签**：批准保留页脚校验码；回滚/删除工程要手输确认。
5. **长任务有进度和停止**：基线、修复、回归都能看到当前步骤。
6. **状态不靠颜色单一表达**：颜色 + 图标 + 文字；键盘可导航；中文优先。
7. **UI 挂了不丢状态**：所有状态在后端/文件；刷新/重开恢复。

## 2. 技术形态

- 本地 Web：`127.0.0.1` + 随机 token + `HttpOnly` cookie。
- Python 标准库 `ThreadingHTTPServer` + 服务端 HTML + SSE；无 Node 构建、无 CDN。
- 图表用手写 SVG/Canvas；前端原生 JS，不引入框架。
- `upi ui` 启动并打开浏览器；`--check` 做 HTTP 自检；`--port 0` 系统分配端口。
- 旧 ANSI TUI 入口已移除（`upi dev tui` 不再可用）；模块仅保留给历史测试。

## 3. 信息架构

```text
┌ 顶栏：工程 [当前工程 ▾] | 形态 常驻/回归 | 探针 ● 关/稳态/全程 | 守护 ● | 待我处理 (3)
├ 左栏：首页 / 问题 / 机会 / 工单 / 回归 / 指纹库 / 报告 / 工程 / 设置
└ 主区
```

**工程切换器是顶栏第一入口**：当前工程名 + 状态点（有效/警告/失效）+
最近工程下拉 + “管理工程”。运行中禁用切换；切换后所有页面/操作绑定新
`project_id`，不把 A 工程的事件/工单/回归混到 B。

### 首页（Live）
- 四张状态卡：守护、工程、探针、待批工单。
- 事件流：合并 daemon 事件与 probe 事件（breach/sample）；最新在前。
- 待我处理：待批工单、失败回滚、盲区告警、探针掉线。
- 迭代进度表：已完成/待接入一目了然。

### 问题（Issues / Clusters）
- cluster 列表：指标、相对帧、波形、复现率、指纹、风险。
- 详情：证据链、ring、A4 窗口、共现指标、代码锚点、历史同类。
- 操作：加入工单、转回归验证、标记 benign/ambiguous。

### 机会（Opportunities）
- 与“问题”分开：机会未越界，但有可达参考差距（floor/budget/peer/history/static）。
- 每项展示：当前值、参考类型/值/出处、gap、预计收益、证据、代码锚点、风险、置信度。
- 操作：转工单、转回归基线、忽略（需理由）；没有参考的条目不允许生成计划。
- 机会状态：`draft/reviewed/planned/closed/regressed/rejected`；不混入问题
  的 `resolved/verified` 计数。

### 工程（Projects）
- 列表：显示名、路径、Unity 版本、场景、状态（有效/警告/失效）、最近使用、
  tags、最近 run；支持搜索/固定。
- 新增：选目录 → 读 Unity 版本/git root → 选默认场景/质量档/replay 命令 →
  **校验** → 保存；**不导入旧 `projects/*.json`（不保留兼容）**。
- 编辑/复制/归档/删除：有历史 run 只允许归档；物理删除二次确认且不删文件。
- 校验：路径、git root、场景、profile、scripts 是否 blocked、探针状态；
  失效工程保留历史但禁止运行。
- 快速开始：选中工程后自动带出上次场景/质量档/replay 模式；一键启动巡检或跑基线。
- 空状态：无工程时只允许新增/导入，**不默认回退夹具**。

### 工单（Tickets）
- 批量候选：按置信度排序，每项带证据、风险、回滚方式、`permission_tier`。
- 批准：整单批准 + 逐 patch 权限仍生效；高风险 patch 单独确认。
- 执行时间线：每个 patch 的 snapshot/编译/观测/keep/rollback，可停止。
- 结果：每 patch 单独记 `kept / rolled_back / unverified`。

### 回归（Regression）
- replay 套件 + 测试目标（命令、时长、warmup、预算线、目标指标）。
- 3 次基线矩阵：行=cluster，列=run1/2/3，格=hit/miss，右侧复现率。
- 批量计划：只允许稳定 cluster 进入；一次一个 patch；修完重跑、重新聚类。
- 验证报告：目标 cluster 消失、无新 cluster、守护不劣化。

### 指纹库（Library）
- pattern 卡片：指纹特征、命中率、last seen、置信度、关联 playbook。
- 节省统计：LLM 调用数 / 100 事件、命中率、命中后修复成功率。
- “教一次”：人工确认新 pattern 后入库；阈值/权限仍不可在 UI 改。

### 报告（Reports）
- verified vs unverified 两本账。
- 检出/误报、成本、回滚、盲区清单；导出 HTML/JSON；只读。

### 设置 / 接入（Settings / Onboarding）
- 工程管理入口（也可从顶栏进入）：新增/校验/归档；不在设置里手改 JSON。
- 探针三态、巡检间隔、冷却、预算、模型配置（只存环境变量名）。
- 回归回放套件/测试目标；conformance tier 展示。
- 所有变更留审计；阈值/权限类变更仍走 blocked 流程。

## 4. 关键交互

### 工程选择 / 快速开始
1. 顶栏点工程名 → 最近工程（置顶当前）+ “管理工程”；
2. 选择后显示校验状态与上次场景/质量档/模式；
3. 点“开始巡检”或“跑基线”直接使用上次设置；需要改再进工程管理；
4. 运行中切换被禁用；切换后顶栏、首页、事件流、工单/回归全部按新
   `project_id` 重新加载；
5. 工程失效 → 顶部红条，禁止运行，只允许打开目录/重新校验/归档。

### 批量工单
1. 问题页选中多个 cluster → “生成工单”。
2. 工单页按置信度排序，显示独立 issue vs 同一 issue 的备选假设。
3. 批准整单；每个 patch 仍按自己的 tier 检查。
4. 执行：一次一个 patch；每步显示安全验证/观测/决策。
5. 失败立即回滚并停止后续；结果分账。

### 回归基线
1. 选择 replay 套件 + 测试目标。
2. 跑 3 次基线（不修），显示矩阵。
3. 只把复现率达标且分类明确的 cluster 加入批量计划。
4. 修一个 → 重跑 → 重新聚类 → 下一个。
5. 全绿后出 `verified` 报告；失败回滚并留痕。

### 审批
- 保留“读完再签”：页脚 4 位校验码；批准人 + 批语必填。
- 批准动作只写“意向”，由守护/执行器复述落盘；UI 等真落盘再报“已生效”。
- 批量批准不绕过单个 patch 的权限闸。

## 5. 视觉

- 深色、紧凑、高对比；状态色 + 图标 + 文案。
- 等宽字体用于事件/指标；中文优先。
- 键盘快捷键；移动端只读（操作以桌面为主）。
- 所有资源本地化，离线可用。

## 6. API（当前/规划）

| 路径 | 状态 | 说明 |
|---|---|---|
| `GET /` | 已接入 | 首页壳 |
| `GET /api/status` | 已接入 | 守护/工程/探针/工单/事件摘要 |
| `GET /api/events` | 已接入 | SSE 状态流 |
| `GET /api/probe/events` | I1b | 探针事件列表（当前状态里带 `probe_events`） |
| `POST /api/tickets` | I3a | 创建批量工单（实际路径 `/api/tickets/build`） |
| `POST /api/tickets/{id}/approve` | I3a | 批准（批准人/批语/4 位校验码） |
| `POST /api/tickets/{id}/execute` | I3b-2a | 串行执行；无可执行 plan 返回 409 |
| `POST /api/probe/install` | I3b-1 | 安装统一探针（先卸旧探针） |
| `POST /api/probe/sample` | I3b-1 | `{on,window}` 采样开关 |
| `POST /api/probe/unload` | I3b-1 | 卸载探针 |
| `POST /api/resident/start` | I3b-2b1 | 启动后台巡检（`interval` 秒） |
| `POST /api/resident/tick` | I3b-2b1 | 手动巡检一轮 |
| `POST /api/resident/stop` | I3b-2b1 | 停止后台巡检 |
| `POST /api/regression/run` | I4 | 跑基线/回归 |
| `GET /api/reports` | I5 | verified/unverified 两本账 |
| `GET /api/projects` | I7b | 工程列表/最近/当前 |
| `POST /api/projects` | I7b | 新增/导入工程 |
| `POST /api/projects/{id}/activate` | I7b | 切换活动工程 |
| `PATCH /api/projects/{id}` | I7b | 显示名/默认场景/质量档/tags |
| `POST /api/projects/{id}/validate` | I7b | 路径/场景/profile/探针校验 |
| `POST /api/projects/{id}/archive` | I7b | 归档（有历史不物理删） |
| `DELETE /api/projects/{id}` | I7b | 无历史才允许物理删；否则 409 |
| `GET /api/opportunities` | I7c | 机会列表（按工程/场景过滤） |

## 7. 当前 I0/I1a/I2/I3a 边界

- 已接入：首页、状态 API、SSE、探针事件摘要、token/cookie、CP936 不崩；
  **问题页（cluster 表）**、**指纹库页**、**工单页（建单/明细/批准/驳回/执行）**、
  **首页探针按钮（安装/采样开/采样关/卸载）**、**自动巡检/巡检一轮/停**、
  **回归页（新建套件/记录 3 次基线/计划）**。
- 工单执行已接 `snapshot/fixer/compile/rollback`；没有可执行 plan 时返回 409，
  不盲改；巡检只建单，不自动执行。
- 夹具 CPU 已能生成真实 plan，但目标 scripts=blocked，工单显示 `needs_human`，
  不会因批量批准而降级执行；明细里可对**单条** patch 再批准（人+批语+4 位码）。
- 回归页只记录当前事件或按套件 `command` 跑 3 次；命令由用户填，
  不内置夹具 Unity 适配；≥4 次记录后可“验证最后一次”，只有 verified 落账。
- 报告页列出回归套件并生成/打开 HTML；设置页只读展示工程/探针/指纹库/目录。
- 工单页用正式审批表单（批准人/批语/4 位码），不再用 prompt；明细带执行时间线。
- 设置页 onboarding 实时勾选探针/事件/工单/回归套件。
- 明细 diff 展开、审批历史已接入；旧 TUI 已删除。
- **未接入**：工程选择/管理（I7b）、机会页（I7c）、真实工程接入（I7f）。
- `GET /api/probe/events`、`/api/clusters` 仍是状态内嵌；独立端点随 I7 补。
- `README`/`使用说明` 仍是旧 TUI 口径，I5 统一重写。

## 8. 验收

- 所有人类操作有 UI 路径，不读 JSON/不敲 CLI。
- 批准、执行、回滚、回归都有进度与结果。
- CP936 下可用；localhost 安全；关键流程有浏览器冒烟测试。
- UI 故障时有 break-glass 停止/回滚，但日常不依赖 CLI。
