"""真实考试驱动：live LLM 诊断/规划 → 工单 → 执行 → 记账 → 评分。

这是“监考”脚本，不是产品路径；它只调用产品节点与工单模型，
不向 LLM 注入任何测试/答案信息（提示词筛查在 factor_judge/llm_planner 内）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from upi.agents import nodes as N  # noqa: E402
from upi.agents.llm_planner import plan_issue  # noqa: E402
from upi.core import events as E  # noqa: E402
from upi.core.findings import (Anchor, AttributionInfo, RepairRecord,  # noqa: E402
                               RunFindings, ToolFinding, new_run_id, save_run,
                               score_run)
from upi.core.protocol import build as fp_build, digest as fp_digest, file_hash  # noqa: E402
from upi.core.ticket import (BatchTicket, TicketItem, TicketStore,  # noqa: E402
                             _item_token, _token, approve, approve_item)
from upi.core.unity_exec import UnityTicketExecutor  # noqa: E402
from upi.tooling import snapshot as SN  # noqa: E402


def unity(*args, timeout=60):
    import subprocess as sp
    p = sp.run(["unity", "command", *args, "--project-path", "test_project",
                "--format", "json"], capture_output=True, text=True,
               encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"success": False, "raw": p.stdout[-200:]}


def anchors_of_hyp(h: dict) -> list[str]:
    return [a for a in (h.get("code_anchors") or []) if a]


def anchor_objs(h: dict) -> list[Anchor]:
    out = []
    for a in anchors_of_hyp(h):
        if "::" in a:
            f, m = a.split("::", 1)
            out.append(Anchor(file=f, method=m.split(".")[-1]))
        else:
            f, _, line = a.rpartition(":")
            try:
                ln = int(line)
            except ValueError:
                f, ln = a, 0
            out.append(Anchor(file=f, start_line=ln, end_line=ln))
    return out


def findings_from_hypotheses(run_id: str, hs: list[dict], archive: dict) -> list[ToolFinding]:
    out = []
    for i, h in enumerate(hs):
        anchors = anchor_objs(h)
        if not anchors:
            continue
        raw = archive.get(h.get("root_cause"), {}) or {}
        refs = []
        for k, v in (raw.items() if isinstance(raw, dict) else []):
            if isinstance(v, dict):
                refs.extend([str(x) for x in (v.get("refs")
                                              or v.get("evidence_refs") or [])])
        out.append(ToolFinding(
            finding_id=f"llm-{run_id}-{i}",
            mechanism=str(h.get("root_cause", "")).split(":")[0],
            metric="llm", link="",
            anchors=anchors, evidence_refs=sorted(set(refs)),
            attribution=AttributionInfo(confidence=float(h.get("confidence", 0)),
                                        factors={"root_cause": h.get("root_cause")}),
            state="candidate"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", default="runs/i8_lab/paper_replay.json")
    ap.add_argument("--project", default="test_project")
    ap.add_argument("--project-id", default="test_project")
    a = ap.parse_args()
    p = Path(a.project).resolve()
    run_id = new_run_id(a.project_id)
    run_dir = ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1) 证据采集（跑 replay + marker）
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "capability_run.py"),
                        "--project", str(p), "--project-id", a.project_id,
                        "--replay", a.replay, "--no-save"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    print("capture:", (r.stdout or r.stderr)[-300:])
    if r.returncode != 0:
        return 1

    # 2) detect → diagnose（live LLM）
    nd = N.make_nodes(run_dir, live_llm=True,
                      knowledge_db=run_dir / "knowledge" / "patterns.db")
    d = nd["detect"]({"project": str(p)})
    print("detect:", d.get("route"), str(d.get("error", ""))[:120])
    if d.get("route") != "proceed":
        return 1
    g = nd["diagnose"]({"issue": d["issue"], "project": str(p)})
    print("diagnose:", g.get("route"), g.get("diagnose_source"),
          "tokens", g.get("tokens_used"))
    if g.get("route") != "proceed":
        print("diagnose error:", g.get("error"))
        return 1
    hs = g.get("hypotheses") or []
    print("top1:", hs[0].get("root_cause"), "conf", hs[0].get("confidence"))
    archive = {}
    apath = run_dir / "diagnose_archive.json"
    if apath.exists():
        try:
            archive = json.loads(apath.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            archive = {}

    # 3) 写 findings（LLM 诊断产物）
    findings = findings_from_hypotheses(run_id, hs, archive)
    run = RunFindings(
        run_id=run_id, project_id=a.project_id, mode="resident",
        protocol_fingerprint=fp_digest(fp_build(
            replay_script_hash=file_hash(Path(a.replay)), data_tier="A2",
            editor_mode="gui")),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        findings=findings, repairs=[])
    save_run(run)
    print("findings:", len(findings))

    # 4) LLM 规划（第一个能出方案的假设）
    plan = None
    meta = {}
    for h in hs:
        plan, meta = plan_issue(N.load_config(ROOT / "llm_config.yaml"),
                                _issue(d["issue"]), h, p)
        print("plan:", h.get("root_cause"), "->", bool(plan), meta.get("error", ""))
        if plan is not None:
            break
    if plan is None:
        print("no LLM plan")
        return 1

    # 5) 工单（人=用户代理，全部批准）
    store = TicketStore()
    tid = "bt-" + time.strftime("%Y%m%d_%H%M%S") + "-llm"
    iid = "it-llm"
    item = TicketItem(item_id=iid, cluster_id="llm", metric="llm",
                      shape="llm_plan", kind="llm", count=1,
                      pattern_id="llm", playbook_id=plan.playbook_id,
                      permission_tier=plan.permission_tier,
                      plan=plan.model_dump(), status="pending",
                      item_approve_token=_item_token(tid, iid))
    t = BatchTicket(ticket_id=tid, project_id="paper", mode="resident",
                    created_at=E.now(), status="awaiting_approval",
                    items=[item], approve_token=_token(tid, [item]))
    store.save(t)
    print("approve batch:", approve(t, approver="user",
                                    note="用户授权：全部批准",
                                    token=t.approve_token))
    if item.permission_tier != "auto":
        print("approve item:", approve_item(
            t, iid, approver="user", note="用户授权：全部批准",
            token=item.item_approve_token))
    store.save(t)

    def snap_fn(attempt, project, scene, fp):
        return SN.snapshot(attempt, project, scene, fp,
                           root=Path(project).resolve())

    ex = UnityTicketExecutor(store, snapshot_fn=snap_fn, project=p,
                             scene=p / "Assets/Scenes/00_Bootstrap.unity")
    out = ex.execute(t)
    print("execute:", out.status)
    for it in out.items:
        print(" item:", it.status, it.error[:100])

    # 6) 修复记账 + 冒烟
    ok_smoke = False
    try:
        import capability_run as CR
        ok_smoke = bool(CR._smoke(p).get("ok"))
    except Exception as e:  # noqa: BLE001
        print("smoke error:", type(e).__name__, e)
    target = findings[0] if findings else None
    if target is not None:
        run.repairs = [RepairRecord(
            finding_id=target.finding_id, plan_id=plan.playbook_id,
            files=plan.touched_assets, compile_ok=(out.items[0].status in
                                                   ("applied", "applied_unverified")),
            runnable=ok_smoke, verdict="unverified", metric_delta=None)]
    save_run(run)

    # 7) 评分（与 Lab 同口径）
    paper = json.loads((ROOT / "runs" / "i8_lab" / "paper.json")
                       .read_text(encoding="utf-8"))
    score = score_run(paper, run)
    print(json.dumps(score, ensure_ascii=False, indent=1))
    return 0


def _issue(issue_d: dict):
    from upi.core.models import Issue
    if "issue_model" in issue_d:
        return Issue(**issue_d["issue_model"])
    raise RuntimeError("issue_model missing")


if __name__ == "__main__":
    raise SystemExit(main())
