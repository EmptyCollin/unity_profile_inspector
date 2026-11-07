"""llm_config 首次使用引导（DESIGN §2.3.3 / §2.3.4）。

⚠️ 维护者开发通道：Windows 上 GUI 设置的用户级变量不会注入已运行的进程，
   因此提供注册表回退读取（_bootstrap_env），并把结果标记 channel: dev，
   不得冒充终端用户通道的"已完成引导"。

用法：
    python scripts/bootstrap_llm.py               # 用户通道：只认进程环境
    python scripts/bootstrap_llm.py --dev         # 开发通道：允许注册表回退
    python scripts/bootstrap_llm.py --dev --recheck
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.config.settings import format_validation_error, load_config  # noqa: E402

# 设计文档 §2.3.2 记录的已知限制（Windows）
WIN_HINT = (
    "在 Windows 上，通过「设置 → 环境变量」新增的用户级变量写入注册表，\n"
    "   但不会自动注入已经运行的终端进程。需要重启终端，或让新建会话继承。\n"
    "   开发联调时可加 --dev 走注册表回退读取（DESIGN §2.3.3 维护者通道）。"
)


def _bootstrap_env(name: str) -> str | None:
    """进程环境读不到时，回退注册表。只返回字符串，不打印；调用方禁止回显。"""
    if os.environ.get(name, "").strip():
        return os.environ[name]
    if sys.platform != "win32":
        return None
    import winreg

    for root, path in (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ):
        try:
            with winreg.OpenKey(root, path) as k:
                val, _ = winreg.QueryValueEx(k, name)
        except OSError:
            continue
        if val and str(val).strip():
            os.environ[name] = str(val)
            return str(val)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="模型服务配置引导与连通性测试")
    ap.add_argument("--dev", action="store_true", help="维护者开发通道：允许注册表回退读取")
    ap.add_argument("--recheck", action="store_true", help="强制复检（结果已存在也重跑）")
    ap.add_argument("--config", default=str(ROOT / "llm_config.yaml"))
    args = ap.parse_args()
    channel = "dev" if args.dev else "user"

    # 1) 读配置 + schema 校验
    try:
        cfg = load_config(args.config)
    except FileNotFoundError:
        print("未找到 llm_config.yaml —— 需要选择：自建配置 / 使用默认配置")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(format_validation_error(exc))
        print("请选择：重建配置 / 手动修正 / 切换默认配置（不会覆盖你的文件）")
        return 2
    print(f"[1/4] 配置加载与 schema 校验：通过（{len(cfg.providers)} 个 provider）")

    # 2) 逐 provider 检查密钥变量存在性（只报"有/无"，绝不回显值）
    results = []
    for pname, provider in cfg.providers.items():
        present, env_name = provider.key_presence()
        source = "process"
        if not present and args.dev:
            if _bootstrap_env(env_name):
                present, source = True, "registry_fallback"
        if not present:
            print(f"[2/4] provider '{pname}'：环境变量 {env_name} 未设置")
            print("   " + (WIN_HINT if sys.platform == "win32" else "请设置该环境变量后重试。"))
            return 3
        print(f"[2/4] provider '{pname}'：变量 {env_name} 存在（来源 {source}），通道={channel}")
        for m in provider.models:
            results.append((pname, provider, m))

    # 3) 是否可跳过
    from upi.config.connectivity import run_check

    out = ROOT / "knowledge" / "llm_connectivity.json"
    for pname, provider, model in results:
        print(f"[3/4] 开始连通性测试 provider={pname} model={model.id}")
        try:
            res = run_check(cfg, pname, model.id, result_path=out, channel=channel)
        except Exception as exc:  # noqa: BLE001
            print(f"   测试无法启动：{type(exc).__name__}")
            return 4

        for s in res["steps"]:
            flag = "通过" if s["ok"] else f"失败({s.get('fail_mode', s.get('error'))})"
            print(f"   - {s['name']}: {flag} [{s['duration_ms']}ms]")
        if not res["all_passed"]:
            print("[4/4] 结论：未通过，禁止进入真实调用阶段")
            return 4
        print("[4/4] 结论：全部通过，能力字段已回填到 knowledge/llm_connectivity.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
