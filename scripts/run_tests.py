"""统一离线测试入口（I0）。

为什么需要它：Windows 默认 CP936，测试脚本里的 ✓/⇒ 会抛 UnicodeEncodeError，
README 里的逐条 `python scripts/test_xxx.py` 在中文 Windows 上直接失败。
本入口给子进程强制 UTF-8，并汇总通过/失败；不改变任何测试的语义。

用法：
  python scripts/run_tests.py                 # 全部 test_*.py
  python scripts/run_tests.py --only webui    # 只跑名字含 webui 的
  python scripts/run_tests.py --list          # 列出会跑哪些
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.tooling.console import force_utf8_streams  # noqa: E402

force_utf8_streams()


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _tail(text: str, n: int = 6) -> str:
    lines = [x for x in (text or "").splitlines() if x.strip()]
    return "\n".join(lines[-n:])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="run_tests")
    ap.add_argument("--only", default="", help="只跑文件名含该子串的测试")
    ap.add_argument("--list", action="store_true", help="只列出测试文件")
    ns = ap.parse_args(argv or [])

    files = sorted(ROOT.glob("scripts/test_*.py"))
    if ns.only:
        files = [p for p in files if ns.only.lower() in p.name.lower()]
    if not files:
        print("!! 没有匹配的测试文件")
        return 2
    if ns.list:
        for p in files:
            print(p.name)
        return 0

    passed, failed = [], []
    for p in files:
        proc = subprocess.run(
            [sys.executable, str(p)],
            cwd=str(ROOT), env=_env(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=1200,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            passed.append(p.name)
            print(f"[ok]   {p.name}")
        else:
            failed.append((p.name, proc.returncode, out))
            print(f"[FAIL] {p.name}  exit={proc.returncode}")
            print(_tail(out, 8))

    print()
    print(f"==== 离线测试汇总: {len(passed)} 套通过, {len(failed)} 套失败 / 共 {len(files)} 套")
    if failed:
        for name, rc, _out in failed:
            print(f"  - {name} (exit {rc})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
