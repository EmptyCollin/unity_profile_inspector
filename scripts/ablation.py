"""I26：ablation 开关 CLI。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.ablation import (AblationSpec, read_flags, set_flag,  # noqa: E402
                               write_flags)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--site", default="")
    ap.add_argument("--on", action="store_true", help="禁用该 site（ablate）")
    ap.add_argument("--off", action="store_true", help="恢复该 site")
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.clear:
        write_flags(a.root, {})
    elif a.site and a.on:
        set_flag(a.root, a.site, True)
    elif a.site and a.off:
        set_flag(a.root, a.site, False)
    flags = read_flags(a.root)
    doc = {"flags": flags,
           "guards": {k: AblationSpec(k).guard() for k in flags}}
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
