# ADR-0027：删除 TUI 死代码（I6c）

- 状态：已完成（2026-11-11）
- 关联：ADR-0022、`upi/agents/webui.py`

## 背景

I5b2 已移除 `upi dev tui` 入口，但 `upi/agents/ui.py`、`ui_actions.py` 和
`test_ui_actions.py` 仍留在仓库；它们只服务已停用的 ANSI TUI，属于死代码。

## 决策

- 删除 `upi/agents/ui.py`、`upi/agents/ui_actions.py`、`scripts/test_ui_actions.py`；
- `test_daemon.py` 的 UI snapshot 段改为直接核对 `status.alerts`；
- `test_recovery.py` 删除“UI 帧画得出告警”断言，保留 `status.alerts`；
- `acceptance.py` 的套件阈值从 31 改为 30，`--with-tests` 文案同步；
- README/使用说明/发布检查中的 31 套改为 30 套；
- AGENTS 适用范围移除 `agents/ui.py`。

## 验证

- `python scripts/run_tests.py`：**30 套通过 / 0 失败**。
- `python scripts/acceptance.py`：**6/6 通过**，证据
  `runs/acceptance_20261111_233359.json`。
- `python scripts/e2e_smoke.py`：**10/10 通过**，证据
  `runs/e2e_smoke_20261111_233401.json`。
- `python scripts/release_check.py`：**6/6 通过，1 warning**（fixture 脏区），
  证据 `runs/release_20261111_233406.json`。

## 边界

- 删除的是 TUI 代码与测试；Web/API 功能未变。
- 测试套件从 31 降到 30，属预期（移除了仅服务 TUI 的套件）。
- 没有新增真实 Unity E2E；I6b2b2 仍待。
- fixture 脏区仍阻断真机写闭环。
