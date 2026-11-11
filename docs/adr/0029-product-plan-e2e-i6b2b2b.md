# ADR-0029：真实产品 plan E2E（I6b2b2b）

- 状态：已完成（2026-11-11）
- 关联：ADR-0028、`scripts/e2e_unity_product.py`

## 背景

ADR-0028 只验证了执行器 harness（临时注释 patch），没有验证真实产品
`PB-FULL-REUSE`。当前夹具 HEAD 是修复态，产品 plan 的 find 段来自缺陷参照，
直接套用会 `diff_rejected`。

## 做法

`scripts/e2e_unity_product.py`：

1. 检查夹具干净；
2. 把 `upi/agents/_refs/F_CPU_01_defect.cs` 装回夹具目标文件，临时提交
   （让 `snapshot()` 能在干净树上建）；
3. `snapshot("e2e-product-...")`；
4. `_plan_for("per_frame_heap_alloc_in_hot_path")` 取真实 `PB-FULL-REUSE`
   （`permission_tier=blocked`）；
5. 用 `human_approved=True` 走产品执行器：diff 精确匹配 → apply → 编译；
6. 断言缺陷签名 `var ready = new List<SkillRuntime>();` 消失、修复标记出现、
   编译 `ok=True`；
7. rollback → 删 tag → reset 原始 HEAD；
8. 断言目标文件逐字节恢复、工作区干净、HEAD 未变。

## 证据

- `python scripts/e2e_unity_product.py`：**17/17 通过**；
- 证据：`runs/e2e_unity_product_20261111_234124.json`；
- 结果：`product plan status=applied`、`compile.ok=True`、
  `defect signature gone`、`original bytes restored`、`HEAD restored`；
- 全程临时缺陷提交已随 `reset` 丢弃，tag 已删。

## 证明了什么 / 没证明什么

**证明**：

- 真实产品 `PB-FULL-REUSE` 能在缺陷态夹具上精确应用；
- blocked 产品 plan 经人工批准后能真实写入并通过 Unity 编译；
- 回滚能逐字节恢复原始文件与 HEAD；
- 产品 plan 的 find/replace 不是纸面模板。

**没证明**：

- 没有在“当前修复态 HEAD”上直接跑产品 plan（会 diff_rejected）；
- 没有通过 Web 工单/单条批准 UI 走完整路径（脚本直接传 human_approved）；
- 没有真实用户工程；
- 没有 replay/verified 闭环。

## 边界

- 临时缺陷提交与快照已在 finally 中清理；运行前要求工作区干净。
- 这是夹具缺陷态 E2E，不是“当前 HEAD 可被自动再修一遍”。
- 真实用户工程/产品 plan 仍属 I6b2b2c。
