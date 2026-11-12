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

| case | 病灶 | 形式 |
|---|---|---|
| c01 | BoardModel 每次匹配分配 | 整文件 overlay |
| c02 | HUD 文本每次重建 | ChromaGame patch |
| c03 | item label 每次重建 | ChromaGame patch（0 context） |

## 验证边界

- 组合可编译/可玩：已验证 c01+c02+c03。
- 组合可同时诊断三病：已验证。
- 组合性能 verified：未完成。
- 真实资产 case（同路径 `.asset/.prefab/.unity` 替换）：规则已测，病例未建。
