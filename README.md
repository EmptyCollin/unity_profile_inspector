# UPI — Unity 性能自动监测与优化 Agent

一个住在 Unity 项目里的“性能体检医生”：探针持续采数据、按 cluster 找问题、
生成批量工单；**改代码前一定等你签字**；常驻修复只记 `unverified`，
回归形态才可能记 `verified`。

## 它怎么工作

```text
常驻形态（日常开发）
  探针事件（breach / sample） → 聚类 → 批量工单 → 人批准
    → 一次一个 patch → snapshot/编译/观测 → keep/rollback → unverified 账本

回归形态（可复现问题）
  replay 命令 → 3 次基线 → cluster 复现率 → 稳健计划
    → 修后同 replay 再验 → verified 账本 + HTML 报告
```

关键设计：

- **探针是唯一数据源**；1Hz 轮询只做哨兵，不单独出 finding；
- **两形态分账**：常驻不做复现验证，只 `unverified`；回归才 `verified`；
- **cluster 是修复/验证单位**：一个 replay/巡检里可能多个相同/不同问题；
- **批量工单、串行落地**：一次只落一个 patch，编译失败立即回滚并停止；
- **每个数字来自实测**，模型只解释证据；
- **UI 是唯一人机通道**：本地 Web 控制台；CLI 只留启动/停止/诊断/应急。

## 快速开始（Windows）

前置：Python 3.14、Unity 6000.6.7f1（图形界面模式）、`unity` CLI、模型密钥。

```powershell
# 1. 密钥只由你本人设置（系统级环境变量）
[Environment]::SetEnvironmentVariable("UNITY_PROFILE_INSPECTOR", "<你的密钥>", "User")

# 2. 自检 → 启动 → 打开 Web 控制台
python -m upi status
python -m upi start
python -m upi ui
```

当前做到 **I5b2**：问题聚类、指纹库、批量工单/正式审批、探针安装/采样/
自动巡检、回归套件/基线/验证/报告、设置/接入只读页都已接进 Web。
旧 ANSI 终端入口 `dev tui` 已移除；日常只用 `python -m upi ui`。

**接入自己的工程**：设置页只读展示当前 profile/工程；完整 Web 接入向导在
I5b 之后。迁移期编辑 `projects/<id>.json`。回放准入见
[docs/回放准入标准.md](docs/回放准入标准.md)，只约束回归形态。

## 当前边界（务必一起了解）

- 数据来自 **PC 编辑器采样**，不等于真机中低端机型；
- **常驻自动修**当前只有少量模板（夹具 CPU full-reuse）能生成 plan；
  夹具脚本是 `blocked`，需要单条人工批准，且夹具工作区脏时 `snapshot()`
  会拒绝写入；
- **回归真实 replay**：可用 `suite.command` 跑用户提供的命令；尚未内置
  夹具 Unity 自动 Play/ReplayDriver/退出适配；
- **GPU 逐笔归因**仍转人工；“量大了以后会炸”类潜在风险暂测不到；
- 完整盲区与解锁路径见[使用说明](docs/使用说明.md)，报告模板会强制附带。

## 目录结构

```
upi/          管线代码（探针控制、事件/聚类/指纹、工单/执行、回归、Web）
probe/        注入编辑器的采样片段（经 unity CLI，非自建插件）
fixtures/     内置演示工程（含故意植入的性能缺陷）
scripts/      测试套件与校验脚本（31 套离线；统一入口 run_tests.py）
knowledge/    特征库（症状→根因→动作→实测效果）
docs/         使用说明、迭代计划、UI 设计、ADR、认知债
runs/         运行留痕（本地独立 git 仓库，不进 GitHub）
AGENTS.md     角色契约与硬门禁（§8 两形态执行契约）
DESIGN.md     详细设计（§14 为两形态权威）
```

`runs/` 用本地独立 git 仓库管理，换机器整目录拷走即可。

## 进一步阅读

| 想了解 | 看哪 |
|---|---|
| 当前进度、下一步 | [docs/迭代计划.md](docs/迭代计划.md) |
| 怎么装、怎么用、每项配置 | [docs/使用说明.md](docs/使用说明.md) |
| UI 信息架构与页面 | [docs/UI设计.md](docs/UI设计.md) |
| 为什么这样设计 | [DESIGN.md](DESIGN.md) §14 + [docs/adr](docs/adr) |
| 行为边界与门禁 | [AGENTS.md](AGENTS.md) §8 |
| 当前认知债/盲区 | [docs/现状与认知债.md](docs/现状与认知债.md) §0 |
| 发布前检查 | [docs/发布检查.md](docs/发布检查.md) |

## 验证

```powershell
python scripts/run_tests.py             # 31 套离线测试（自动 UTF-8）
python scripts/check_fixture_split.py   # 夹具隔离闸
python scripts/acceptance.py            # 6 项验收，落 runs/acceptance_*.json
python scripts/e2e_smoke.py             # Web/API 全链路 10 项，落 runs/e2e_smoke_*.json
python scripts/release_check.py         # 发布前审计，落 runs/release_*.json
python -m upi ui --check                # Web 状态自检（ASCII JSON）
```
