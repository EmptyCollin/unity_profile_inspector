# sim/tests — Chroma Rogue 玩法确定性自测

`devsim_selftest.cs` 在 Unity Play Mode 里经 Pipeline 执行：

```powershell
python scripts/test_devsim_game.py
```

它通过 `DevSimSelfTest.RunAll` 调用游戏公开的 `Debug*` 取证接口，覆盖：

- 交换绑定：拖拽 A→相邻 B 只交换 A/B；非相邻拒绝；无效交换回弹且盘面不变；
- 旧 bug 回归：盘面别处已有三连时，无关交换不得被误判为有效；
- 数字色块：N≥2 不能换位；同色消除每步只减 1 层；N=2→1 后变普通块且不显示数字；
- 道具：锤子/炸弹只拆 1 层；改色拒绝叠层块；非法目标不消耗；拖放坐标命中格子；
- BONUS：单步（含连锁）同一种颜色 ≥6 才触发；5 个不触发、异色合计 6 个不触发；
- 回合：时间不到不因达标提前进轮；时间到才结算；预告含目标分/当前分/时长/棋盘变化；
- 棋盘：10→9→…→6 随难度收缩，换轮重建；
- 商店：每 3 轮一次；购买置售罄；重复购买被拦截；刷新扣费且不再出现已购商品；
- 动效范围：只有退场格退场、只有受重力影响的格移动（向下）、只有新格生成；
- 画质：Canvas 设计分辨率 1:1、色块纹理与显示像素一致。

输出 `runs/devsim_selftest_<ts>.json`。截图与交互证据由 `scripts/e2e_devsim_ui.py` 产出到 `runs/devsim_v03_*.png`。
