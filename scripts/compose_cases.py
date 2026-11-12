"""组合多个 case：overlay/patch 叠加 + replay 合并。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.case_overlay import apply_cases  # noqa: E402
from upi.core.replay_spec import load_replay, merge_replays  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--case", action="append", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--replay-out", default="")
    a = ap.parse_args()
    try:
        run = apply_cases(a.base, a.case, a.out)
        specs = []
        for cd in a.case:
            m = json.loads((Path(cd) / "manifest.json").read_text(encoding="utf-8"))
            if m.get("replay"):
                specs.append(load_replay(ROOT / "sim" / "replay" / f"{m['replay']}.json"))
        merged = merge_replays(specs) if specs else None
        if merged is not None and a.replay_out:
            Path(a.replay_out).write_text(merged.model_dump_json(indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "workdir": run.workdir, "cases": run.case_ids,
                          "files": run.files,
                          "replay": (merged.model_dump() if merged else None)},
                         ensure_ascii=False, indent=2))
        return 0
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"},
                         ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
