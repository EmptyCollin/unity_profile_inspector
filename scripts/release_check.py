"""I6b2b 发布前审计：acceptance + E2E + 迁移 + 脏区告警，落 runs/release_*.json。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.tooling.console import force_utf8_streams  # noqa: E402

force_utf8_streams()


def run(cmd: list[str], timeout: int = 300) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    checks: list[dict] = []
    warnings: list[str] = []

    def add(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + ("" if ok else f" -> {str(detail)[:160]}"))

    rc, out = run([sys.executable, "scripts/acceptance.py"])
    add("acceptance 6/6", rc == 0 and "6/6" in out, out[-300:])

    rc, out = run([sys.executable, "scripts/e2e_smoke.py"])
    add("e2e 10/10", rc == 0 and "10/10" in out, out[-300:])

    # 迁移：用户入口不再有 tui；README/使用说明不再教 dev tui
    try:
        from upi.__main__ import _DEV_CMDS
        add("dev tui removed", "tui" not in _DEV_CMDS)
    except Exception as e:  # noqa: BLE001
        add("dev tui removed", False, f"{type(e).__name__}: {e}")
    for rel in ("README.md", "docs/使用说明.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        bad = "python -m upi dev tui" in text
        add(f"{rel} 不再教 dev tui", not bad)

    # ADR 数量
    adrs = sorted((ROOT / "docs" / "adr").glob("0*.md"))
    add("ADR >= 25", len(adrs) >= 25, f"{len(adrs)} ADRs")

    # 脏区是警告，不是发布失败：它挡的是真机写闭环，不是 Web 面
    p = subprocess.run(["git", "status", "--porcelain", "--", "fixtures/upi-fixture"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    dirty = [ln for ln in p.stdout.splitlines() if ln.strip()]
    if dirty:
        warnings.append(f"fixtures/upi-fixture 有未提交改动 {len(dirty)} 项；真机快照会被 G4 拒绝")
    print(f"  [warn] fixture dirty: {len(dirty)} 项" if dirty else "  [ok] fixture clean")

    ok = all(c["ok"] for c in checks)
    ts = time.strftime("%Y%m%d_%H%M%S")
    outpath = ROOT / "runs" / f"release_{ts}.json"
    outpath.parent.mkdir(parents=True, exist_ok=True)
    outpath.write_text(json.dumps({
        "ok": ok, "checks": checks, "warnings": warnings, "at": ts,
        "note": "I3b-2c2b/I4b2b2b2 真机闭环仍未完成；发布前必须处理 fixture 脏区/blocked",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n==== release {'通过' if ok else '失败'}: "
          f"{sum(c['ok'] for c in checks)}/{len(checks)}；"
          f"{len(warnings)} warning；证据 {outpath}")
    for w in warnings:
        print("  ! " + w)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
