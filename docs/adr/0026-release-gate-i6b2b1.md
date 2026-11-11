# ADR-0026：发布前审计门（I6b2b1）

- 状态：已完成（2026-11-11）
- 关联：ADR-0024、ADR-0025、`scripts/release_check.py`

## 背景

Acceptance/E2E/迁移检查分散；发布前需要一条“跑完给结论”的门，并显式
把 fixture 脏区列为 warning（它阻断真机写，不阻断 Web 面发布）。

## 决策

`scripts/release_check.py`：

- 跑 `acceptance.py`（6 项）；
- 跑 `e2e_smoke.py`（10 项）；
- 检查 `dev tui` 已移除、README/使用说明不再教；
- ADR 数量 ≥25；
- `git status fixtures/upi-fixture` 脏区 → warning；
- 结果写 `runs/release_<ts>.json`，critical 失败 exit 1。

`docs/发布检查.md` 给出：
- 一键审计命令；
- 手工检查清单；
- 已知发布阻断（I3b/I4b/I6b2b2、旧 ui.py）；
- 可对外/不可对外的口径。

## 验证

- `python scripts/release_check.py`：**6/6 通过，1 warning**，证据
  `runs/release_20261111_233050.json`。
- warning：fixture 5 项未提交改动；真机快照会被 G4 拒绝。

## 边界

- release gate 不跑真实 Unity/探针/写入；它只决定“Web 面可发布候选”。
- fixture 脏区不会让 gate 失败；发布说明必须带 warning。
- 旧 `upi/agents/ui.py` 仍在（测试用），gate 不检查删除。
- 真实工程 E2E 仍是 I6b2b2。
