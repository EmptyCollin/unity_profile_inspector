# ADR-0019：回归报告（I4b2b2b1）

- 状态：报告完成；夹具 Unity 自动适配/全量守护选择待 I4b2b2b2（2026-11-11）
- 关联：ADR-0014、ADR-0016、`upi/core/regression_report.py`

## 背景

回归有 suite/matrix/plan/verdict，但没有一份人能打开的报告；UI 只能看摘要。
I4b2b2b1 生成只读 HTML，把基线、计划、失败审计、守护放在一起。

## 决策

- `build_report(store, sid)`：读 suite/matrix/plan/verdicts，返回结构化数据；
- `render_html(data)`：HTML 表格（runs/plan/verdict/guards），全部字段 `html.escape`；
- `write_report()`：写 `runs/_regression/reports/<sid>.html`；
- Web：
  - `POST /api/regression/suites/{id}/report` 生成；
  - `GET /api/regression/suites/{id}/report` 返回 HTML（token 门禁）；
  - 回归页“报告”按钮生成并新窗口打开。

## 验证

- `test_regression.py`：30 项（报告含 plan/verdict、落盘）。
- `test_webui.py`：45 项（POST 生成、GET HTML）。
- 全量离线：31 套。

## 边界

- **没有夹具 Unity 自动适配**：报告基于用户 `command` 生成的 run；不自动 Play/ReplayDriver。
- **没有成本/trace 章节**：报告只有回归数据，不是完整 RunReport。
- **没有全量守护选择**：展示 run 采到的所有 guards；没有按链路筛选。
- **报告只读**：不提供改阈值/改权限入口；XSS 已转义。
- 失败 verdict 已入报告，但 `last_verdict` 只显示最近一条。
