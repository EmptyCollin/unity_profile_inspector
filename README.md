# UPI — Unity 性能自动监测与优化 Agent

一个住在你 Unity 项目里的"体检医生"：定期自动测性能、找问题、开处方，
但**改代码之前一定等你签字**；改完用数据证明"确实好了、没弄坏别的"，
证明不了就自动恢复原样。

## 它怎么工作

```text
常驻形态（日常开发）
  探针事件（breach / sample） → 聚类 → 批量工单 → 人批准
    → 一次一个 patch → 编译/冒烟/观测 → keep/rollback → unverified 账本

回归形态（批量回归）
  replay + 测试目标 → 3 次基线 → cluster 复现率 → 批量修复
    → 同 replay 重跑 + 重新聚类 → verified 账本
```

几个关键设计：
- **探针是唯一数据源**，1Hz 轮询只做哨兵；
- **两形态分账**：常驻只记 `unverified`（不做复现验证），回归才可 `verified`；
- **cluster 是单位**：一个 replay/巡检里多个相同/不同问题先聚类再分类；
- **批量工单、串行落地**：工单可列多个，执行一次只落一个 patch；
- **每个数字都来自实测采样**，模型只负责解释证据，禁止编造数据；
- **修复前先拍 git 快照**，失败一键回滚；危险改动永远先报后批；
- **UI 是唯一人机通道**：本地 Web 控制台，不要求人读 JSON/敲 CLI。

## 快速开始（Windows）

前置：Python 3.14、Unity 6000.6.7f1（图形界面模式）、`unity` CLI、一个模型密钥。
详细清单见[使用说明](docs/使用说明.md)第二节。

```powershell
# 1. 设密钥（一次性，需要你本人操作）
[Environment]::SetEnvironmentVariable("UNITY_PROFILE_INSPECTOR", "<你的密钥>", "User")

# 2. 自检 → 启动 → 打开 Web 控制台
python -m upi status     # 全绿再继续
python -m upi start      # 后台启动巡检机器人
python -m upi ui         # 本地 Web 控制台（浏览器）
```

当前做到 **I5a**：Web 控制台已能看问题聚类、指纹库、批量工单/审批、
探针安装/采样/巡检、回归套件/基线/验证/报告、设置/接入只读页。
旧 ANSI 终端入口 `dev tui` 已移除；日常只用 `python -m upi ui`。

**接入你自己的工程**：完整 Web 接入向导在 I5b；迁移期可编辑 `projects/<id>.json`
（设置页只读展示当前 profile/工程）。回放准入仍按
[docs/回放准入标准.md](docs/回放准入标准.md)，只约束回归形态。

## 能力边界（务必连这段一起了解）

- 数据来自 **PC 编辑器采样**，不等于中低端真机表现；
- **常驻形态**做发现 + best-effort 修复，结论只 `unverified`；**回归形态**有确定性
  replay + 测试目标时才做 `verified`。当前“自动修复”只覆盖已建模板的问题类型；
- **GPU 逐笔归因**仍转人工；注册表当前没有 GPU episode detector，“已解禁”不等于
  “已注册”；“量大了以后会炸”类潜在风险暂测不到；
- 完整盲区清单（含解锁路径）见[使用说明第五节](docs/使用说明.md)，
  每份运行报告里也会强制附带。

## 目录结构

```
upi/          管线代码（守护、Web 控制台、检测/归因/修复/验证）
probe/        注入编辑器做采样的代码片段（经 unity CLI，非自建插件）
fixtures/     内置演示工程（含故意植入的性能缺陷）
scripts/      测试套件与校验脚本（当前 24 套离线测试；统一入口 run_tests.py）
knowledge/    特征库（症状→根因→动作→实测效果）
dashboard/    历史统计网页报告（I5 后简化并入 Web 控制台）
docs/         使用说明、迭代计划、UI 设计、ADR、认知债清单
runs/         运行留痕（本地独立 git 仓库管理，不进 GitHub）
AGENTS.md     角色契约与硬门禁（含 v1.0 两形态执行契约）
DESIGN.md     详细设计（v0.16，§14 为两形态权威）
```

**关于 `runs/`**：运行留痕全部不进 GitHub（含本机路径与可重采的大体积原始
数据），但本地照旧用 git 管——`runs/` 内挂了一个无远端的独立仓库，工单与
决定的逐版本历史在里面可查。换机器时把 `runs/` 整目录拷走即可。

## 进一步阅读

| 想了解 | 看哪 |
|---|---|
| 后续怎么开发、当前做到哪、下一步是什么 | [docs/迭代计划.md](docs/迭代计划.md) |
| UI 长什么样、为什么是唯一通道 | [docs/UI设计.md](docs/UI设计.md) |
| 为什么这样设计（两形态、事件/聚类/指纹） | [DESIGN.md](DESIGN.md) §14 + [docs/adr](docs/adr) |
| 机器人的行为边界与门禁（G1~G23） | [AGENTS.md](AGENTS.md) §8 |
| 当前做到哪、还欠什么 | [docs/现状与认知债.md](docs/现状与认知债.md) §0 |
| 怎么装、怎么用、每项配置含义 | [docs/使用说明.md](docs/使用说明.md)（I5 重写） |

## 验证机制健全性（零成本，不碰编辑器不花积分）

```powershell
python scripts/run_tests.py             # 全部 24 套离线测试（自动 UTF-8）
python scripts/check_fixture_split.py   # 夹具隔离闸
```
