# ADR-0004：I0 范围、退役清单与验收

- 状态：已接受（I0，2026-11-11）
- 关联：ADR-0001/0002/0003

## I0 交付

1. 三份 ADR（两形态 / UI 栈 / 事件-聚类-指纹契约）。
2. CP936 输出守门：`upi/tooling/console.py`，`python -m upi` 入口调用。
3. 本地 Web 控制台骨架：`upi/agents/webui.py`
   - `GET /` 首页壳（导航、守护/工程/探针/待批工单、事件流）；
   - `GET /api/status` 状态 JSON；
   - `GET /api/events` SSE；
   - 本地 token + HttpOnly cookie；只绑 127.0.0.1。
4. `upi ui` 改为启动 Web 控制台；旧 ANSI TUI 移到 `upi dev tui`（迁移期保留）。
5. `scripts/test_webui.py`：HTML/状态/token/SSE 冒烟。
6. `scripts/run_tests.py`：统一 UTF-8 环境跑全部离线测试，解决 CP936 下
   `✓/⇒` 崩溃。

## 退役清单（I0 标记，I5 前删除）

| 项 | 处置 |
|---|---|
| A 档 1Hz 轮询当水位主力 | 退役；1Hz 只做哨兵 |
| 四窗口长标定 | 退役；改预算表 + 短 warmup + 可选基线 |
| C+ 静态扫描（M3.5） | 不进常驻链路；可选离线 |
| M8 B 档 / EX-1 | 继续延期 |
| M9 dashboard 服务 | 取消 |
| 七视图重报告 | 简化为报告页 + 导出 |
| ANSI TUI | `dev tui` 迁移期保留，I5 删除 |
| 文件制人工批准 | 降为内部审计；人只在 Web UI 批 |

## 验收

- `python -m upi ui --check` 在默认 CP936 下退出 0，输出状态 JSON；
- `python scripts/test_webui.py` 通过；
- `python scripts/run_tests.py` 全部离线测试通过；
- 旧 `python -m upi dev tui` 在非 TTY 下仍能打印快照（迁移期不破坏测试）。

## 不做什么

- I0 不实现单探针合并、聚类、指纹库、批量工单、回归模式；它们在 I1~I4。
- I0 不重写 README/使用说明；I5 随 UI 完整化统一改。
