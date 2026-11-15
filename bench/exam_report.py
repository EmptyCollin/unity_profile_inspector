"""I25-ext E7：从一次 exam run 生成完整考试报告。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(run_dir: Path) -> dict:
    findings = []
    fp = run_dir / "findings.json"
    if fp.is_file():
        findings = json.loads(fp.read_text(encoding="utf-8")).get("findings") or []
    records = []
    cp = run_dir / "counterfactual.jsonl"
    if cp.is_file():
        for line in cp.read_text(encoding="utf-8").splitlines():
            try:
                records.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
    by_status: dict[str, int] = {}
    for r in records:
        s = str(r.get("status") or "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    trees = []
    for p in run_dir.rglob("repair_tree.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        d["unit_id"] = p.parent.name
        trees.append(d)
    n = len(records) or len(findings)
    unrecorded = max(0, len(findings) - len(records))
    if unrecorded:
        by_status["no_plan"] = by_status.get("no_plan", 0) + unrecorded
    n = max(n, len(findings))
    verified = by_status.get("verified", 0)
    improved = by_status.get("improved_partial", 0)
    remaining = max(0, n - verified - improved)
    coverage = round((n - remaining) / n, 4) if n else 0.0
    return {
        "run_id": run_dir.name,
        "exam_executed": True,
        "exam_passed": bool(n and verified == n),
        "n_findings": len(findings),
        "n_units": n,
        "statuses": by_status,
        "verified": verified,
        "improved_partial": improved,
        "remaining": remaining,
        "coverage": coverage,
        "records": records,
        "trees": trees,
    }


def markdown(doc: dict) -> str:
    lines = [
        f"# 考试报告 {doc['run_id']}",
        "",
        "```text",
        f"exam_executed = {doc['exam_executed']}",
        f"exam_passed   = {doc['exam_passed']}",
        f"findings      = {doc['n_findings']}",
        f"units         = {doc['n_units']}",
        f"verified      = {doc['verified']}",
        f"improved_partial = {doc['improved_partial']}",
        f"remaining     = {doc['remaining']}",
        f"coverage      = {doc['coverage']}",
        "```",
        "",
        "## 逐 unit",
        "",
        "| unit | target | status | gone | metric |",
        "|---|---|---|---|---|",
    ]
    for r in doc["records"]:
        lines.append("| {unit} | {target} | {status} | {gone} | {metric} |".format(
            unit=r.get("unit_id", ""), target=r.get("target", ""),
            status=r.get("status", ""), gone=r.get("gone", ""),
            metric=r.get("metric_status", "")))
    lines += ["", "## 状态树", ""]
    for t in doc["trees"]:
        lines.append(f"- {t.get('unit_id', '')}: current={t.get('current')} "
                     f"states={len(t.get('states') or [])} "
                     f"attempts={t.get('total_attempts')}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    run_dir = ROOT / "runs" / a.run_id
    doc = build(run_dir)
    md = markdown(doc)
    out = Path(a.out) if a.out else run_dir / "exam_report.md"
    out.write_text(md, encoding="utf-8")
    (run_dir / "exam_report.json").write_text(
        json.dumps({k: v for k, v in doc.items() if k != "records"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
