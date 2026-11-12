# Case overlay 组合契约

## 目录

```text
sim/cases/cXX/
  manifest.json
  overlay/       # 独占文件或资产（同路径替换）
  patches/       # 共享代码的最小 unified diff
```

`manifest.json`：

```json
{
  "case_id": "cXX",
  "overlay": ["Assets/..."],
  "patches": [{"file": "Assets/...", "patch": "patches/xxx.patch"}],
  "replay": "cXX",
  "oracle": "cXX",
  "base_hash": "..."
}
```

## 组合规则

- 不同 case 的 `overlay` 路径必须不相交；同路径直接拒绝。
- 同一代码文件可以多 case 同时改，但必须用 patch，且 hunk 不能重叠。
- 资产 overlay 必须同时提供 `.meta`；同路径资产不能叠加。
- patch 按 case 顺序应用；任一处 context 冲突，整个组合失败。
- 一次组合只从干净 base 复制一次，绝不改 base。
- replay 合并时 `start_game` / `stop` 只保留一次。

## 命令

```bash
# 应用单个/多个 case
python scripts/apply_case.py --base sim/project \
  --case sim/cases/c01 --case sim/cases/c02 --out runs/combo

# 同时合并 replay
python scripts/compose_cases.py --base sim/project \
  --case sim/cases/c01 --case sim/cases/c02 --case sim/cases/c03 \
  --out runs/combo --replay-out runs/combo_replay.json
```

## 当前病例

| case | 病灶 | 窗口 | 期望符号 | 形式 |
|---|---|---|---|---|
| c01 | BoardModel 每次匹配分配 | board | BoardModel.FindMatches | 整文件 overlay |
| c02 | HUD 文本每次重建 | hud | ChromaGame.UpdateHud | ChromaGame patch |
| c03 | item label 每次重建 | hud | ChromaGame.RefreshItemLabels | ChromaGame patch |
| c04 | 商店 payload 放大 | shop | 无（断言 phase=Shop/shopOpen） | 资产 overlay + .meta |
| c05 | 逐格材质实例 | board | ChromaGame.BuildBoard | patch |
| c06 | 每次消除即时粒子 | board | ChromaGame.ResolveAllSync | patch |
| c07 | 每帧强制 canvas 布局 | hud | ChromaGame.Update | patch |
| c08 | 逐格临时纹理 | board | ChromaGame.RefreshTiles | patch |
| c09 | 空闲全盘 hint 扫描 | hud | ChromaGame.ResolvePendingWhenIdle | patch |
| c10 | 逐帧手写 tile 缩放 | hud | ChromaGame.UpdateBody | patch |
| c11 | 商店同步加载 payload | shop | ChromaGame.OpenShop | patch |

manifest 里的 `pattern_id` 与 `sim/oracle/cases` 的 `expected_opportunities` 对齐；
oracle 的 24 个评测场景通过 `atomic_cases` 组合上述原子病例（混合场景=多原子组合，
负对照=空组合）。

## 验证边界

- 11 个原子病例可同时叠加、编译通过；合并后 11/11 均能出现期望证据（`runs/i8_lab/evidence/candidates.json` 的 `per_case`）；
- 合并态玩法自测 44/44 通过（`runs/devsim_selftest_*.json`）；
- 组合性能 verified：未完成。
