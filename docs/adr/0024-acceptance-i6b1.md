# ADR-0024：验收脚本与 Web 安全头（I6b1）

- 状态：已完成（2026-11-11）
- 关联：`scripts/acceptance.py`、`upi/agents/webui.py`

## 背景

发布前需要一条可重复的验收证据链：探针文件、dev 命令面无 TUI、测试套件数、
`upi ui --check`、安全响应头。此前这些散落在人工命令里。

## 决策

- `scripts/acceptance.py`：
  - 检查探针文件存在；
  - 检查 `dev` 命令表无 `tui`；
  - 检查离线测试套件数 ≥31；
  - 跑 `python -m upi ui --check` 并解析 JSON；
  - 检查安全头实现标记；
  - 可选 `--with-tests` 跑全量 31 套；
  - 结果写 `runs/acceptance_<ts>.json`，失败 exit 1。
- Web 响应头增加：
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: no-referrer`
  - `X-Frame-Options: DENY`
- 测试增加安全头断言。

## 验证

- `python scripts/acceptance.py`：6/6 通过，证据
  `runs/acceptance_20261111_232637.json`。
- `test_webui.py`：49 项通过（安全头）。
- 全量离线：31 套（上一轮已跑；本轮代码仅加头 + 脚本）。

## 边界

- 验收脚本不跑真实 Unity/探针；只验证文件/命令/HTTP/测试面。
- 没有真实工程 E2E；I6b2。
- `--with-tests` 会跑完整离线套件，约数分钟；默认不跑。
- 安全头不是完整 CSP/CSRF 方案；本地单用户 token 模型不变。
