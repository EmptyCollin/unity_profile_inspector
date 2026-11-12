"""I8b：从证据包生成候选根因（不读 case/oracle）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.candidates_v2 import generate_candidates  # noqa: E402
from upi.core.evidence_graph import EvidenceGraph  # noqa: E402
from upi.core.evidence_v2 import EvidenceBundleV2, clean_game_evidence  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--fingerprint-db", default="")
    a = ap.parse_args()

    project = Path(a.project).resolve()
    raw = EvidenceBundleV2(**json.loads(Path(a.evidence).read_text(encoding="utf-8")))
    bundle = clean_game_evidence(raw)
    g = EvidenceGraph.from_bundle(bundle, project)
    store = None
    if a.fingerprint_db:
        from upi.core.fingerprint import FingerprintStore
        store = FingerprintStore(Path(a.fingerprint_db))
    candidates = generate_candidates(bundle, g, project_root=project, limit=a.limit,
                                     fingerprint_store=store)
    result = {
        "run_id": bundle.run_id,
        "project_id": bundle.project_id,
        "scene": bundle.scene,
        "nodes": len(g.nodes), "edges": len(g.edges),
        "candidates": [c.model_dump(mode="json") for c in candidates],
        "blindspots": [b.model_dump() for b in bundle.blindspots],
        "graph": g.to_dict(),
    }
    out = Path(a.out) if a.out else (ROOT / "runs" / bundle.run_id /
                                     "evidence" / "candidates_v2.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "ok": True, "out": str(out), "nodes": len(g.nodes), "edges": len(g.edges),
        "candidates": [{"kind": c.kind, "root_cause": c.root_cause,
                        "anchor": c.code_anchors[:1], "score": c.score}
                       for c in candidates],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
