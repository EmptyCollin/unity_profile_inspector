# ADR-0025：Web/API 全链路 E2E 冒烟（I6b2a）

- 状态：已完成（2026-11-11）
- 关联：ADR-0024、`scripts/e2e_smoke.py`

## 背景

单元/集成测试覆盖了各模块，但没有一条“从状态到报告”的全链路证据。
I6b2a 用临时 runs + 随机端口跑一遍 Web/API，落 JSON 证据，不碰 Unity。

## 决策

`scripts/e2e_smoke.py`：

- `E.isolate()` 临时 runs，重定向 fingerprint/ticket/regression；
- 启 `WebUI(127.0.0.1:0)`，token `e2e-token`；
- 流程：
  1. GET `/`；
  2. GET `/api/status`（settings）；
  3. 建工单 → 批准 → 执行 409（无 plan）；
  4. 建回归套件 → 记录 3 次 → 第 4 次 → verify；
  5. 生成/获取 HTML 报告；
- 结果写 `runs/e2e_smoke_<ts>.json`，失败 exit 1。

## 验证

- `python scripts/e2e_smoke.py`：**10/10 通过**，证据
  `runs/e2e_smoke_20261111_232944.json`。
- 不改变现有 31 套测试；web 49 项仍通过。

## 边界

- 不启动 Unity/探针；不验证真实写入/编译/回滚。
- 工单执行按当前现实返回 409（无 plan），不是“执行成功”证据。
- 回归用记录当前事件，不是真实 3 次 replay。
- 安全头只在 web 测试断言；E2E 未逐项断言。
- 真实工程/夹具 Unity E2E 仍属 I6b2b。
