# ADR-0028：真实 Unity 写-编译-回滚 E2E（I3b-2c2b / I6b2b2a）

- 状态：已完成（2026-11-11）
- 关联：ADR-0010、`scripts/e2e_unity_write.py`

## 背景

此前只有注入式执行器测试；夹具脏区导致 `snapshot()` 一直无法真跑。
用户确认清理后，夹具工作区已干净（`workdir_state dirty=False`），可以
做一次真实写入/编译/回滚验证。

## 做法

`scripts/e2e_unity_write.py`：

1. 先检查 `fixtures/upi-fixture` 干净；
2. 记录原始 HEAD；
3. `snapshot.snapshot("e2e-...")` 建 attempt 快照/tag；
4. 构造一个**语义保持的临时注释 patch**（`PB-E2E-COMMENT`），
   在 `CombatSkillManager.cs` 的 `skillCount` 字段后插一行注释；
5. `fixer.execute(plan, snap, project_path=..., scene_path=..., root=...)`：
   - 权限闸、diff 精确匹配、`compile_gate`；
6. 断言文件确实出现 marker、编译 `ok=True`；
7. `snapshot.rollback` → 删 tag → `git reset --hard 原始 HEAD`；
8. 断言工作区干净、HEAD 未变、tag 已删。

## 证据

- `python scripts/e2e_unity_write.py`：**9/9 通过**；
- 证据：`runs/e2e_unity_write_20261111_233839.json`；
- 关键结果：`status=applied`、`compile.ok=True`、`fixture restored clean`、
  `HEAD restored`、`tag deleted`；
- 快照 tag：`upi/attempt-e2e-233827`（已删）。

## 这次证明了什么 / 没证明什么

**证明**：

- `snapshot` 能在干净夹具上建立；
- `fixer.execute` 能真实写文件、触发 Unity 编译并通过；
- 回滚能恢复树与工作区，HEAD/tag 可清理；
- 整套写-编译-回滚在真实 Editor 上可跑。

**没证明**：

- 没有应用夹具 CPU 产品 plan：该 plan 的 find 段来自缺陷参照，当前 HEAD 已是
  修复态，`find_in_file=False`；强行套用会 `diff_rejected`；
- 没有证明 blocked/semi 产品 patch 可自动执行（仍需单条人工批准）；
- 没有证明真实用户工程可接入；
- 没有做 3 次 replay/verified 闭环。

## 边界

- 这是 **harness E2E**，不是“已能自动修夹具/真实工程”。
- 临时 patch 是注释，语义保持；不是性能修复。
- 脚本只在夹具干净时运行，并强制恢复；生产使用前仍需独立工程验证。
