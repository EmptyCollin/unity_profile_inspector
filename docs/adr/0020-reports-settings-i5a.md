# ADR-0020：报告页 + 设置/接入页（I5a）

- 状态：报告/设置页完成；正式审批/执行时间线/onboarding 待 I5b（2026-11-11）
- 关联：ADR-0019、`upi/agents/webui.py`

## 背景

I4 后 UI 已有问题/指纹/工单/回归，但“报告”和“设置/接入”仍是占位；
用户无法在 UI 里确认当前工程、探针、指纹库、回归目录，也看不到报告入口。

## 决策

状态 API 增加 `settings`：

- `profile_id / project / fix_subject / probe_file / fingerprint_db /
  ticket_dir / regression_dir`；
- 只读，不提供改配置/切工程入口。

Web：

- “报告”页：回归套件、基线数、verified 数、最近 verdict、生成/打开报告；
- “设置/接入”页：当前工程/探针/指纹库/目录 + 首次使用顺序；
- 导航把 reports/settings 从占位改为真实页面；
- shell 升为 `I5a`。

## 验证

- `test_webui.py`：46 项（settings 字段、页面标签）。
- 全量离线：31 套。

## 边界

- 设置**只读**：没有改路径/切 profile/改阈值入口（blocked 档）。
- 报告只覆盖回归；没有工单/探针/成本报告。
- 没有正式审批表单/执行时间线/onboarding 引导；I5b。
- **I3b-2c2b 仍被挡**：夹具脏区 + scripts=blocked，真机写闭环仍需干净工作区/工程。
