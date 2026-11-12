# devsim oracle（仅评测，不进工程）

这个目录**不属于** `sim/project`，也不应出现在工具的 workspace / 提示词 / 日志里。

- `cases/*.json`：每个 case 的入口、期望机会、参考指标、允许修复、风险；
- `replay` 只接收 scenario 配置（seed/难度/性能开关），不接收这里的 expected findings；
- 评测器在工具跑完后读取 oracle，与 `OpportunityFinding` 对比 recall/precision/FP。

工程内禁止出现 `oracle`、`case_id`、`defect`、`F-` 等答案痕迹。
