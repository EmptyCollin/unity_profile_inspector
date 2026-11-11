# ADR-0009：探针控制接 UI/CLI（I3b-1）

- 状态：已完成（2026-11-11）
- 关联：ADR-0006、`upi/tooling/probe_control.py`

## 背景

I1b 证明了统一探针件能跑，但安装/卸载仍是手工 `unity command "script eval_file"`。
I3 的常驻形态必须能在 UI 里装探针、开/关采样、卸载，且不能和旧探针并存。

## 决策

`upi/tooling/probe_control.py` 提供：

- `install(project)`：先 `legacy_stop()`（旧 `pb_cmd:unload` + 旧 sampler `cmd:stop`），
  再 `unity command script eval_file --project-path ... --file probe/upi_probe_v4.cs`；
- `unload()`：`probe_cmd:unload` + legacy_stop；
- `sample(on, window)`：`sample:on:<30~600>` / `sample:off`；
- `status()`：读 `probe_status.json` / `pb_hb.txt` 新鲜度；
- `send_cmd()`：原子写 `probe_cmd.txt`，等 `probe_res.txt` 新写；超时返回
  `ERR no-response`，不冒充成功。

Web：

- `POST /api/probe/install|unload|sample`；
- 首页探针卡增加“安装 / 采样开 / 采样关 / 卸载”按钮；
- 安装/卸载失败如实弹错，不静默。

## 真机验证（2026-11-11）

- `install`：`ok=true`，`installed recorders=12/13 skipped=[Render/Indices(未注册)]`；
- `status`：`alive`，心跳 1.1s；
- `sample(True,60)`：`ok=true`，状态行 `sample=on window=60`；
- `unload`：`ok=true`，`unloaded`；3s 后心跳停在 22:48:13，无残留回调；
- 离线测试：`test_probe_control` 16 项；`test_webui` 34 项。

## 边界

- **Unity 执行器未接**：`/api/tickets/{id}/execute` 仍 501；`apply/compile/observe/
  rollback` 还是注入口（I3b-2）。
- `install` 依赖 Editor 可达；不可达时返回错误，不做“装上了”的假报。
- `send_cmd` 是文件通道 3s 超时；长操作（install）由 CLI 自身 timeout 管。
- 旧探针卸载后若发生域重载，`[InitializeOnLoad]` 可能重新挂旧探针；生产随机
  工程没有旧探针，夹具迁移期需要一次干净重启。
- 还没有 live/抽检调度；I3b-2 接。
