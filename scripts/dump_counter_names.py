"""scripts/dump_counter_names.py —— #27 前置：句柄名×类目权威对照表（一键驱动）。

为什么需要（sampler_v3.cs 头注同款教训）：字符串构造 ProfilerRecorder(类目, 名)
找不到的名字**不抛异常** ⇒ 静默 0（实测 data.json skipped=[] 而渲染四项全 0）。
Targets 定稿要求每条 (类目, 真名) 都有实测证据，不许按 UI 显示名联想（I1）。

流程：拷贝片段进工程根 → script eval 注入 → 编辑器侧写全量 "类目|名" 到
runs/_probe/counter_cat_full.txt → 删除工程根副本（不留残余）。
编辑器必须可达；edit 态即可跑（只读句柄表，不进 Play、不动场景）。

CLI 返回一律走 upi.tooling.unity_cli.run_unity（信封归一唯一入口）——
beta.17 起 `unity command` 返回裸载荷而非旧信封，自行 json.loads 按旧读法
会恒拿空结果（normalize_envelope 头注的实测教训，别再犯一遍）。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from upi.tooling.unity_cli import run_unity  # noqa: E402

PROJECT = ROOT / "fixtures" / "upi-fixture"
SRC = ROOT / "probe" / "counter_names_dump.cs"
OUT = ROOT / "runs" / "_probe" / "counter_cat_full.txt"


def cmd(name: str, *extra: str, timeout: int = 300):
    return run_unity(["command", name, "--project-path", str(PROJECT), *extra],
                     timeout_s=timeout)


def main() -> int:
    st = cmd("editor status", timeout=90)
    if not st.success:
        print("!! 编辑器不可达:", str(st.errors)[:200]); return 1
    dst = PROJECT / SRC.name
    shutil.copy(SRC, dst)
    try:
        r = cmd("script eval", "--file", dst.name)
    finally:
        dst.unlink(missing_ok=True)
    if not r.success:
        print("!! 注入失败:", str(r.errors)[:300]); return 1
    res = (r.data or {}).get("result") or {}
    txt = res.get("output") or res.get("result") or str(res)
    print(str(txt).replace("\\r", "\r").replace("\\n", "\n")[:3000])
    if OUT.exists():
        rows = OUT.read_text(encoding="utf-8").splitlines()
        print(f"\n[全量表已落盘] {OUT}  行数={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
