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

当前做到 **I4b2b2b2a**：问题聚类、指纹库、批量工单/正式审批、探针安装/采样/
自动巡检、回归套件/基线/验证/报告、设置/接入只读页都已接进 Web；真实
Unity **写-编译-回滚 harness**、**缺陷态产品 plan**、**自动 replay 3 次基线**
均已通过。旧 ANSI 终端入口/模块已移除。

**接入自己的工程**：设置页只读展示当前 profile/工程；完整 Web 接入向导在
I5b 之后。迁移期编辑 `projects/<id>.json`。回放准入见
[docs/回放准入标准.md](docs/回放准入标准.md)，只约束回归形态。

## 当前边界（务必一起了解）

- 数据来自 **PC 编辑器采样**，不等于真机中低端机型；
- **常驻自动修**当前只有少量模板（夹具 CPU full-reuse）能生成 plan；
  夹具脚本是 `blocked`，需要单条人工批准；
- **真实 Unity harness** 已做“临时注释 patch → snapshot → apply → 编译 →
  回滚 → HEAD 恢复”E2E；**缺陷态夹具**上还真实执行了 `PB-FULL-REUSE`
  产品 plan（17/17：applied/compile/rollback/字节恢复）。但这不等于当前
  修复态 HEAD 可被再套一遍：产品 plan 的 find 段来自缺陷参照，当前 HEAD
  已修复，直接套用会 `diff_rejected`；
- **回归真实 replay**：已能用 Unity CLI 自动 `scene open → Play → 探针采样 →
  Stop`；夹具缺陷态 3 次基线 + 修复后 3 次 replay 已 **verified**（目标 GC
  breach 消失、GC 77.5KB→572B、CPU 守护正常）。`Memory/TotalUsedBytes` 按
  G20 标为未评估盲区，不能对外说“内存不劣化”；
- **GPU 逐笔归因**仍转人工；“量大了以后会炸”类潜在风险暂测不到；
- 完整盲区与解锁路径见[使用说明](docs/使用说明.md)，报告模板会强制附带。

## 目录结构

```
upi/          管线代码（探针控制、事件/聚类/指纹、工单/执行、回归、Web）
probe/        注入编辑器的采样片段（经 unity CLI，非自建插件）
fixtures/     内置演示工程（含故意植入的性能缺陷）
scripts/      测试套件与校验脚本（30 套离线；统一入口 run_tests.py）
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
python scripts/run_tests.py             # 30 套离线测试（自动 UTF-8）
python scripts/check_fixture_split.py   # 夹具隔离闸
python scripts/acceptance.py            # 6 项验收，落 runs/acceptance_*.json
python scripts/e2e_smoke.py             # Web/API 全链路 10 项，落 runs/e2e_smoke_*.json
python scripts/e2e_unity_write.py       # 真实 Unity 写-编译-回滚 9 项（临时改夹具，自动恢复）
python scripts/e2e_unity_product.py     # 缺陷态夹具产品 plan 17 项（临时装回缺陷，自动恢复）
python scripts/e2e_replay.py            # 夹具自动 replay 3 次基线（真实 Play/采样）
python scripts/release_check.py         # 发布前审计，落 runs/release_*.json
python -m upi ui --check                # Web 状态自检（ASCII JSON）
```

## 2026-11-15：I25-ext 阶段成果

本阶段把“修复—验证—再修复”从线性重来推进到 **checkpoint 树 + 单 finding 串行尝试 + 黑名单 + UI**。

### 已跑通

```text
11case --serial 真机完整考试
run test_project_20261115_123247
baseline 11 roots
verified 3 / improved_partial 1 / needs_recheck 6 / no_plan 1
exam_executed=true
exam_passed=false
```

这不是“全部通过”，是完整考试可执行、结果可追溯。

### 新增能力

```text
upi/core/repair_tree.py        checkpoint 状态树、预算、git_ref 回滚
upi/core/repair_engine.py      RepairEngine / SerialEngine
upi/core/repair_serial.py      逐 finding 串行循环
upi/core/finding_blacklist.py  黑名单指纹库（proposed/active/TTL/audit）
upi/core/repair_policy.py      生产查询/提议/失败回调
upi/agents/repair_ui.py        修复状态页面
```

### 新增页面

```text
/repair
  状态树、attempt 轨迹、预算、黑名单、命中、审计、回滚、模式
```

### 关键命令

```powershell
# 完整考试（压缩 replay、3 基线、串行）
python scripts/exam_batch.py --replay runs/i8_lab/paper_replay.json \
  --baselines 3 --rounds 1 --plan-repeat 3 --plan-min-votes 2 --serial

# 考试报告
python bench/exam_report.py --run-id <run_id>

# UI
python -m upi ui
```

### 产物

```text
runs/<run_id>/repair_tree.json（每 unit 一份）
runs/<run_id>/counterfactual.jsonl
runs/<run_id>/exam_report.md
runs/<run_id>/exam_report.json
```

### 边界

```text
- engine 尚未完全接管 orchestrator 的 attempt 执行
- 回滚所有权尚未完全收敛
- 检测稳定性仍不足，单次 root 数会波动
- 11case 当前 3 verified，不能当“全部修好”
```
阶段复盘：[docs/I25-ext-复盘.md](docs/I25-ext-复盘.md)
测量优先复盘：[docs/I25-ext-复盘-测量优先.md](docs/I25-ext-复盘-测量优先.md)
