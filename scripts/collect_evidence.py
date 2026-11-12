"""I8a：把探针事件转成 EvidenceBundleV2，并补源码符号位置。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.evidence_v2 import EvidenceBundleV2, from_probe_events  # noqa: E402
from upi.core.probe_events import EventLog  # noqa: E402
from upi.core.source_index import SourceIndex  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--events", default="")
    ap.add_argument("--evidence", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--scene", default="")
    ap.add_argument("--protocol", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    project = Path(a.project).resolve()
    events_path = Path(a.events) if a.events else None
    log = EventLog(events_path) if events_path else EventLog()
    run_id = a.run_id or ("evidence-" + __import__("time").strftime("%Y%m%d_%H%M%S"))

    if a.evidence:
        raw = json.loads(Path(a.evidence).read_text(encoding="utf-8"))
        bundle = EvidenceBundleV2(**raw)
        bundle.run_id = run_id
        bundle.project_id = project.name
        bundle.scene = a.scene or bundle.scene
        if a.protocol:
            bundle.protocol_fingerprint = a.protocol
        bundle.raw_refs.append(str(Path(a.evidence)))
        events, bad = [], 0
    else:
        events, bad = log.read_all(compat=events_path is None)
        bundle = from_probe_events(
            events, run_id=run_id, project_id=project.name, scene=a.scene,
            protocol_fingerprint=a.protocol,
            raw_refs=[str(log.path)] if log.path.exists() else [],
        )
    idx = SourceIndex.build(project)
    for f in bundle.functions:
        if f.source is None:
            f.source = idx.resolve(f.symbol)
    for cs in bundle.callstacks:
        cs.frames = idx.annotate_stack(cs.frames)
    for site in bundle.allocations:
        if site.source is None and site.stack:
            site.source = idx.resolve(site.stack[0].symbol)

    out = Path(a.out) if a.out else (ROOT / "runs" / run_id / "evidence" / "evidence_v2.json")
    bundle.write(out)
    print(json.dumps({
        "ok": True, "out": str(out), "events": len(events), "bad_events": bad,
        "frames": len(bundle.frames), "callstacks": len(bundle.callstacks),
        "functions": len(bundle.functions), "render_objects": len(bundle.render_objects),
        "allocations": len(bundle.allocations),
        "blindspots": [b.code for b in bundle.blindspots],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
