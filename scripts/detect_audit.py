"""检出审计：case × finding × observed window。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "test_project_20261115_212712"
CAP = "test_project_baseline_1_212718"
CASE_SYMBOL = {
    "c01": "FindMatches", "c02": "UpdateHud", "c03": "RefreshItemLabels",
    "c04": "payload", "c05": "BuildBoard", "c06": "ResolveAllSync",
    "c07": "Update", "c08": "RefreshTiles", "c09": "ResolvePendingWhenIdle",
    "c10": "UpdateBody", "c11": "OpenShop",
}


def main() -> int:
    findings = json.loads((RUN / "findings.json").read_text(
        encoding="utf-8"))["findings"]
    roots = [str((f.get("attribution") or {}).get("factors", {})
                 .get("root_cause") or "") for f in findings]
    windows = {}
    for idx in (18, 21, 23, 28):
        p = ROOT / "runs" / f"{CAP}_a{idx}" / "evidence" / "evidence_v2.json"
        if not p.is_file():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        windows[idx] = {str(x.get("symbol") or "")
                        for x in (d.get("functions") or [])}
    print("| case | 期望符号 | finding | 检出 | observed windows |")
    print("|---|---|---|---|---|")
    for cid, sym in CASE_SYMBOL.items():
        if cid == "c04":
            hit = "opportunity"
            found = "yes (opportunity)"
            obs = "28"
        else:
            hit = next((r for r in roots if sym in r), "")
            found = "yes" if hit else "NO"
            obs = ",".join(str(i) for i, syms in windows.items()
                           if any(sym in s for s in syms))
        print(f"| {cid} | {sym} | {hit or '-'} | {found} | {obs or '-'} |")
    print()
    print("finding 总数:", len(findings))
    print("未匹配 finding:", [r for r in roots
                             if not any(s in r for s in CASE_SYMBOL.values())])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
