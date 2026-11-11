"""I6b 验收：离线测试/CLI/UI/迁移/探针 的可重复检查，结果落 runs/。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.tooling.console import force_utf8_streams  # noqa: E402

force_utf8_streams()


def run(cmd: list[str], timeout: int = 600) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-tests", action="store_true",
                    help="同时跑 31 套离线测试（较慢）")
    args = ap.parse_args()
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail[:500]})
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + ("" if ok else f" -> {detail[:200]}"))

    # 1. 探针文件
    probe = ROOT / "probe" / "upi_probe_v4.cs"
    add("probe file exists", probe.exists(), str(probe))

    # 2. dev 命令不再有 tui
    try:
        from upi.__main__ import _DEV_CMDS
        add("dev tui removed", "tui" not in _DEV_CMDS, str(sorted(_DEV_CMDS)))
    except Exception as e:  # noqa: BLE001
        add("dev tui removed", False, f"{type(e).__name__}: {e}")

    # 3. 测试文件数量
    tests = sorted((ROOT / "scripts").glob("test_*.py"))
    add("offline suites >= 31", len(tests) >= 31, f"{len(tests)} suites")

    # 4. UI --check
    rc, out = run([sys.executable, "-m", "upi", "ui", "--check", "--port", "0"], 120)
    data = {}
    try:
        data = json.loads(out)
    except Exception:  # noqa: BLE001
        pass
    add("upi ui --check rc=0", rc == 0, out[:300])
    add("ui --check json ok", bool(data.get("ok")) and bool(data.get("ui", {}).get("shell")),
        json.dumps(data.get("ui", {}), ensure_ascii=True))

    # 5. 安全响应头（不启服务，直接看模板实现标记）
    web = (ROOT / "upi" / "agents" / "webui.py").read_text(encoding="utf-8")
    add("security headers present",
        all(h in web for h in ("X-Content-Type-Options", "X-Frame-Options",
                               "Referrer-Policy")))

    # 6. 可选全量测试
    if args.with_tests:
        rc, out = run([sys.executable, "scripts/run_tests.py"], 1800)
        add("run_tests 31/31", rc == 0 and "31 套通过" in out, out[-400:])

    ok = all(c["ok"] for c in checks)
    ts = time.strftime("%Y%m%d_%H%M%S")
    outdir = ROOT / "runs"
    outdir.mkdir(parents=True, exist_ok=True)
    p = outdir / f"acceptance_{ts}.json"
    p.write_text(json.dumps({"ok": ok, "checks": checks, "at": ts},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n==== 验收 {'通过' if ok else '失败'}: {sum(c['ok'] for c in checks)}/{len(checks)}；证据 {p}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
