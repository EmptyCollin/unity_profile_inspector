"""I19：机制合并批量考试驱动（单轮先跑通）。

N findings → M 机制单元 → M 补丁 → 全部应用 → 编译一次 → 一次 capture → 验全部。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import exam_live as X  # noqa: E402
import exam_multi as M  # noqa: E402
from upi.agents import nodes as N  # noqa: E402
from upi.adapters.loader import load_adapter  # noqa: E402
from upi.agents.flow_run import FlowRun  # noqa: E402
from upi.agents.llm_planner import plan_issue  # noqa: E402
from upi.core import events as E  # noqa: E402
from upi.core.batch_round import BatchResult, run_batch  # noqa: E402
from upi.core.evidence_diagnosis import candidates_from_latest  # noqa: E402
from upi.core.findings import (RepairRecord, RunFindings, new_run_id,  # noqa: E402
                               save_run)
from upi.core.mechanism_batch import PatchRef, consolidate  # noqa: E402
from upi.core.mechanism_infer import count_in_method  # noqa: E402
from upi.core.plan_consensus import choose_consensus, plan_key  # noqa: E402
from upi.core.repair_verify import decide as _decide_verify  # noqa: E402
from upi.core.ticket import (BatchTicket, TicketItem, TicketStore,  # noqa: E402
                             _item_token, _token, approve, approve_item)
from upi.core.unity_exec import UnityTicketExecutor  # noqa: E402
from upi.tooling import snapshot as SN  # noqa: E402


def latest_counters() -> dict:
    files = sorted((ROOT / "runs").glob("*/counters.json"),
                   key=lambda p: p.stat().st_mtime)
    if not files:
        return {}
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _merge_counters(parts: list[dict]) -> dict:
    merged: dict[str, dict] = {}
    for d in parts:
        for name, node in (d.get("counters") or {}).items():
            series = node.get("series") if isinstance(node, dict) else None
            if not isinstance(series, list):
                continue
            slot = merged.setdefault(name, {"series": []})
            slot["series"].extend(float(x) for x in series)
    return {"counters": merged}


def counters_since(start_ts: float, prefix: str = "") -> dict:
    merged: dict[str, dict] = {}
    for p in (ROOT / "runs").glob("*/counters.json"):
        if prefix and not p.parent.name.startswith(prefix):
            continue
        try:
            if p.stat().st_mtime < start_ts:
                continue
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for name, node in (d.get("counters") or {}).items():
            series = node.get("series") if isinstance(node, dict) else None
            if not isinstance(series, list):
                continue
            slot = merged.setdefault(name, {"series": []})
            slot["series"].extend(float(x) for x in series)
    return {"counters": merged}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", default="runs/i8_lab/paper_replay.json")
    ap.add_argument("--project", default="test_project")
    ap.add_argument("--project-id", default="test_project")
    ap.add_argument("--baselines", type=int, default=None)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--max-units", type=int, default=0)
    ap.add_argument("--max-findings", type=int, default=12,
                    help="每次 diagnose 最多处理多少个 finding（控 LLM 成本）")
    ap.add_argument("--targets", default="",
                    help="逗号分隔的 mechanism 子串，只跑匹配的单元")
    ap.add_argument("--plan-repeat", type=int, default=1,
                    help="同一输入重复 plan 次数，用于统计 LLM 一致性")
    ap.add_argument("--plan-min-votes", type=int, default=2,
                    help="重复采样时判定稳定所需最少同票")
    ap.add_argument("--llm-decide", action="store_true",
                    help="decide 节点由 LLM 选择 retry/stop（失败退回规则）")
    ap.add_argument("--adapter", default="upi.adapters.devsim:DevSimAdapter",
                    help="项目适配器 module:Class")
    ap.add_argument("--static-repair", action="store_true",
                    help="把静态-only 候选也纳入修复（默认只作 potential）")
    ap.add_argument("--serial", action="store_true",
                    help="逐 finding 串行修复（RepairTree），默认批量")
    ap.add_argument("--after-repeats", type=int, default=None,
                    help="after 采集重复次数；默认按 measure-profile")
    ap.add_argument("--measure-profile", default="final",
                    choices=["screen", "candidate", "final"],
                    help="测量分层；只有 final 可 verified")
    ap.add_argument("--asset-attempt", action="store_true",
                    help="对 asset unit 走 proposal→apply→memory→rollback")
    ap.add_argument("--baseline-cache", action="store_true",
                    help="按 replay+project+baselines 复用 baseline")
    ap.add_argument("--oracle-plans", default="",
                    help="oracle clean plans JSON（绕过 LLM planner）")
    ap.add_argument("--claim-gate", action="store_true",
                    help="plan 阶段生成/校验/冻结 PatchClaim")
    ap.add_argument("--acceptance-status", action="store_true",
                    help="用 I27 三轴 acceptance 覆盖旧 status（默认只记录）")
    ap.add_argument("--group-acceptance", action="store_true",
                    help="组验收：组前 3 次 before + 补丁 + 组后 3 次 after")
    ap.add_argument("--group-mode", choices=["single", "components"],
                    default="single",
                    help="single=window 不相交的 unit 全放一组；components=按相交拆组")
    a = ap.parse_args()
    if a.group_acceptance and a.serial:
        raise SystemExit("--group-acceptance 与 --serial 不能同时使用")
    from upi.core.measure_profile import get as _mp
    _prof = _mp(a.measure_profile)
    if a.baselines is None:
        a.baselines = _prof.baselines
    if a.after_repeats is None:
        a.after_repeats = _prof.after
    _oracle_plans = {}
    if a.oracle_plans:
        _oracle_plans = json.loads(
            Path(a.oracle_plans).read_text(encoding="utf-8"))
    _claims = {}
    if a.claim_gate:
        pass
    p = Path(a.project).resolve()
    run_id = new_run_id(a.project_id)
    run_dir = ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    flow = FlowRun(run_dir, run_id)
    flow.enter("baseline")
    t0 = time.time()
    M.ensure_ready(p)
    adapter = load_adapter(a.adapter, p)

    baseline_roots = set()
    baseline_root_counts: dict[str, int] = {}
    baseline_stable: set[str] = set()
    baseline_metrics: dict = {}
    baseline_runs: list[dict] = []
    baseline_windows: list[dict[int, dict]] = []
    baseline_caps: list[str] = []
    after_windows: list[dict[int, dict]] = []
    after_n = 0
    from upi.core.window_metrics import (action_indices, exact_index,
                                         index_for_window, pick_control_index)
    from upi.core.window_metrics import window_counters
    action_idx = action_indices(a.replay)
    last_cap = {"id": ""}
    cache_key = hashlib.sha256(
        (str(p) + "|" + str(a.baselines) + "|" +
         Path(a.replay).read_text(encoding="utf-8")).encode("utf-8")
    ).hexdigest()
    cache_hit = False
    cached_site_card = ""
    if a.baseline_cache:
        from upi.core.baseline_cache import get as _cache_get
        _cached = _cache_get(cache_key)
        if _cached and isinstance(_cached.get("data"), dict):
            _d = _cached["data"]
            baseline_roots = set(_d.get("roots") or [])
            baseline_root_counts = dict(_d.get("root_counts") or {})
            baseline_runs = list(_d.get("runs") or [])
            baseline_windows = [
                {int(k): v for k, v in (w or {}).items()}
                for w in (_d.get("windows") or [])]
            cache_hit = True
            cached_site_card = str(_d.get("site_card") or "")
            print("baseline cache hit", len(baseline_runs))

    def capture_with_recovery(label: str, *, two_pass: bool,
                              light: bool = False, fast: bool = False,
                              target_windows: bool = False):
        ok, out = adapter.capture_replay(
            a.replay, run_dir, label, two_pass=two_pass, light=light,
            fast=fast, target_windows=target_windows)
        if ok:
            last_cap["id"] = getattr(adapter, "last_capture_id", "")
            return True, out
        print("capture failed; restart editor and retry", label)
        try:
            M.ensure_ready(p)
        except Exception as e:  # noqa: BLE001
            print("editor restart failed", type(e).__name__, e)
            return False, out
        ok2, out2 = adapter.capture_replay(
            a.replay, run_dir, label + "-retry", two_pass=two_pass,
            light=light, fast=fast, target_windows=target_windows)
        if ok2:
            last_cap["id"] = getattr(adapter, "last_capture_id", "")
        return ok2, out2

    if not cache_hit:
        for i in range(max(1, a.baselines)):
            bstart = time.time()
            ok, _ = capture_with_recovery(
                f"baseline#{i + 1}", two_pass=False,
                light=True, target_windows=True)
            if not ok:
                return 1
            baseline_runs.append(counters_since(0, last_cap["id"]))
            baseline_windows.append(window_counters(last_cap["id"]))
            if last_cap.get("id"):
                baseline_caps.append(str(last_cap["id"]))
            (run_dir / "baseline_capture.json").write_text(json.dumps(
                {"capture_id": last_cap["id"], "project_id": a.project_id,
                 "replay": a.replay}, ensure_ascii=False, indent=2),
                encoding="utf-8")
            mp = run_dir / "merged_candidates.json"
            if mp.exists():
                roots = {str(x.get("root_cause", "")).split(":", 1)[-1]
                         for x in json.loads(mp.read_text(encoding="utf-8"))}
            else:
                cands, _, _ = candidates_from_latest(p, limit=12)
                roots = {str(c.root_cause).split(":", 1)[-1] for c in cands}
            baseline_roots |= roots
            for _r in roots:
                baseline_root_counts[_r] = baseline_root_counts.get(_r, 0) + 1
            print("baseline", i + 1, "roots", len(baseline_roots))
    if a.baseline_cache and not cache_hit:
        from upi.core.baseline_cache import put as _cache_put
        _card = ""
        if last_cap.get("id"):
            try:
                from upi.core.trace_analysis import build_trace as _bt
                _td = _bt(last_cap["id"], ROOT / "runs")
                _card = "\n".join(
                    f"{s.get('symbol')} | {s.get('file')}:{s.get('line')} "
                    f"| self_ms={s.get('self_ms')} calls={s.get('calls')}"
                    for s in (_td.get("site_metrics") or [])[:10])
            except Exception:  # noqa: BLE001
                _card = ""
        _cache_put(cache_key, {
            "roots": sorted(baseline_roots),
            "root_counts": baseline_root_counts,
            "runs": baseline_runs,
            "windows": baseline_windows,
            "site_card": _card,
        })
        cached_site_card = _card

    if baseline_root_counts:
        import math
        _need = max(2, math.ceil(0.6 * max(1, len(baseline_runs))))
        baseline_stable = {r for r, n in baseline_root_counts.items()
                           if n >= _need}
    else:
        baseline_stable = set(baseline_roots)
    baseline_metrics = baseline_runs
    flow.enter("diagnose")
    collector = X._make_collector(p, a.project_id, a.replay)
    if _prof.name == "screen":
        _mp = run_dir / "merged_candidates.json"
        if _mp.is_file():
            _raw = json.loads(_mp.read_text(encoding="utf-8"))
        else:
            _cands, _b, _p = candidates_from_latest(p, limit=12)
            _raw = [{"root_cause": c.root_cause,
                     "sources": [{"file": s.file, "start_line": s.start_line}
                                 for s in (getattr(c, "sources", None) or [])]}
                    for c in _cands]
        _hs = []
        for _c in _raw[:30]:
            if str(_c.get("kind") or "") == "anomaly":
                continue
            _anchors = []
            for _s in (_c.get("sources") or [])[:4]:
                if isinstance(_s, dict):
                    _anchors.append(f"{_s.get('file','')}:{_s.get('start_line',0)}")
            if _anchors:
                _hs.append({"root_cause": _c.get("root_cause", ""),
                            "code_anchors": _anchors, "confidence": 0.5,
                            "window_id": _c.get("window_id", "")})
        findings = X.findings_from_hypotheses(run_id, _hs, {})
    else:
        from upi.core.project_store import knowledge_db as _knowledge_db
        nd = N.make_nodes(run_dir, live_llm=True,
                          knowledge_db=_knowledge_db(a.project_id),
                          mode="regression", evidence_collector=collector)
        _bdirs = [str(ROOT / "runs" / c) for c in baseline_caps]
        d = nd["detect"]({"project": str(p), "project_id": a.project_id,
                          "baseline_dirs": _bdirs})
        if d.get("route") != "proceed":
            print("detect failed", d)
            return 1
        _finds = d.get("findings_v2") or []
        if _finds:
            from upi.core.finding import Finding as _Finding
            _all_hs: list[dict] = []
            _failed: list[dict] = []
            for _idx, _fd in enumerate(_finds[:max(1, a.max_findings)]):
                try:
                    _fobj = _Finding(**_fd)
                except Exception as e:  # noqa: BLE001
                    _failed.append({"finding": str(_fd), "error": str(e)})
                    continue
                _g = None
                _last_err = ""
                for _try in range(2):
                    try:
                        _g = nd["diagnose"]({
                            "issue": N.issue_from_finding(_fobj),
                            "project": str(p),
                            "primary_finding": _fobj.model_dump(),
                            "baseline_dirs": _bdirs,
                            "use_evidence_loop": True, "replay": a.replay,
                            "allow_deep": _idx < 3})
                    except Exception as _de:  # noqa: BLE001
                        _g = None
                        _last_err = f"{type(_de).__name__}: {str(_de)[:160]}"
                    if _g and _g.get("route") == "proceed":
                        break
                    if _try == 0:
                        print("diagnose retry", _fobj.finding_id,
                              _last_err or (_g or {}).get("route"))
                if _g is None:
                    _failed.append({"finding": _fobj.finding_id,
                                    "route": "exception",
                                    "error": _last_err})
                    continue
                if _g.get("route") == "proceed":
                    _all_hs.extend(_g.get("hypotheses") or [])
                else:
                    _failed.append({"finding": _fobj.finding_id,
                                    "route": _g.get("route"),
                                    "error": str(_g.get("error") or "")[:160]})
            if not _all_hs:
                print("diagnose failed", _failed)
                return 1
            g = {"hypotheses": _all_hs, "route": "proceed",
                 "failed_findings": _failed}
        else:
            g = nd["diagnose"]({"issue": d["issue"], "project": str(p),
                                "primary_finding": d.get("primary_finding"),
                                "baseline_dirs": _bdirs,
                                "use_evidence_loop": True, "replay": a.replay})
        if g.get("route") != "proceed":
            print("diagnose failed", g)
            return 1
        hs = g.get("hypotheses") or []
        from upi.core.finding import dedupe_hypotheses
        _n_hs = len(hs)
        hs = dedupe_hypotheses(hs)
        if len(hs) != _n_hs:
            print("hypotheses dedupe", _n_hs, "->", len(hs))
        findings = X.findings_from_hypotheses(run_id, hs, {})
    _asset_findings = [f for f in findings
                       if "oversized_resource_asset" in str(f.mechanism)]
    findings = [f for f in findings
                if "oversized_resource_asset" not in str(f.mechanism)]
    _win_map = {}
    _mp = run_dir / "merged_candidates.json"
    if _mp.is_file():
        try:
            for _c in json.loads(_mp.read_text(encoding="utf-8")):
                _win_map[str(_c.get("root_cause") or "")] = str(
                    _c.get("window_id") or "")
        except Exception:  # noqa: BLE001
            _win_map = {}
    for _f in findings:
        _root = str((_f.attribution.factors or {}).get("root_cause") or "")
        if _win_map.get(_root):
            _f.attribution.factors["window_id"] = _win_map[_root]
            if not _f.window_id:
                _f.window_id = _win_map[_root]
            if _f.window_id and _f.window_id not in _f.window_ids:
                _f.window_ids = [_f.window_id]
    try:
        from upi.core.asset_opportunity import scan as _scan_opps
        _opps = [o.as_dict() for o in _scan_opps(p)]
        (run_dir / "opportunities.json").write_text(
            json.dumps(_opps, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    # I20：静态机制站点补检只用于修复单元，不进入评分 findings
    from upi.core.mechanism_inventory import scan as _scan_sites
    static_findings: list[dict] = []
    existing_methods = {a.method for f in findings for a in f.anchors if a.method}
    added_mech: dict[str, int] = {}
    for mech, sites in _scan_sites(p).by_mechanism().items():
        if added_mech.get(mech, 0) >= 3:
            continue
        for s in sites:
            if not s.method or s.method in existing_methods:
                continue
            root = f"hot_function:{Path(s.file).stem}.{s.method}"
            if any(root == str((f.attribution.factors or {}).get("root_cause"))
                   for f in findings):
                continue
            static_findings.append({
                "finding_id": f"static-{run_id}-{len(static_findings)}",
                "mechanism": root, "metric": "", "link": "",
                "anchors": [{"file": s.file, "method": s.method,
                             "start_line": s.line, "end_line": s.line}],
                "evidence_refs": [], "state": "candidate",
                "attribution": {"confidence": 0.5,
                                "factors": {"root_cause": root}}})
            existing_methods.add(s.method)
            added_mech[mech] = added_mech.get(mech, 0) + 1
            print("static candidate", root, s.file, s.line)
            if added_mech[mech] >= 3:
                break
    run = RunFindings(run_id=run_id, project_id=a.project_id,
                      mode="regression", created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                      findings=findings, repairs=[])
    save_run(run)
    repair_inputs = [f.model_dump() for f in findings]
    try:
        (run_dir / "potentials.json").write_text(
            json.dumps(static_findings, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError:
        pass
    if a.static_repair:
        repair_inputs += static_findings
    units = consolidate(repair_inputs)
    if a.targets:
        needles = {x.strip() for x in a.targets.split(",") if x.strip()}
        units = [u for u in units
                 if u.mechanism in needles
                 or u.mechanism.split(":", 1)[-1] in needles]
    if a.max_units and a.max_units < len(units):
        units = units[:a.max_units]
    # 同窗口多 unit 必须显式可见：它们共享 before/after，不能当独立验收。
    _window_units: dict[str, list[str]] = {}
    for _u in units:
        _ws = {_finding_window.get(_fid)
               for _fid in (_u.finding_ids or [])}
        _ws |= set((unit_contract.get(_u.unit_id) or {}).get("window_ids") or [])
        for _w in _ws:
            if _w:
                _window_units.setdefault(str(_w), []).append(_u.unit_id)
    (run_dir / "window_units.json").write_text(
        json.dumps(_window_units, ensure_ascii=False, indent=2),
        encoding="utf-8")
    if _prof.name != "screen":
        _mp = run_dir / "merged_candidates.json"
        _cands = []
        if _mp.is_file():
            try:
                _cands = json.loads(_mp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                _cands = []
        _wins = sorted({str(c.get("window_id") or "") for c in _cands
                       if c.get("window_id")
                       and str(c.get("kind") or "") not in ("asset", "anomaly")})
        if _wins:
            import subprocess
            _p = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "profile_windows.py"),
                 "--project", str(p), "--replay", a.replay,
                 "--run-id", run_id, "--windows", ",".join(_wins)],
                cwd=str(ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=1800)
            _profiles = []
            _pf = run_dir / "profiles" / "profile_windows.json"
            if _pf.is_file():
                try:
                    _profiles = json.loads(_pf.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    _profiles = []
            _miss = [x.get("window_id") for x in _profiles
                     if x.get("raw_status") != "ok"]
            _two = {"status": "done" if _p.returncode == 0 else "profile_failed",
                    "windows": _wins, "profiles": len(_profiles),
                    "raw_missing": _miss,
                    "stderr": (_p.stderr or "")[-4000:]}
            (run_dir / "two_pass.json").write_text(
                json.dumps(_two, ensure_ascii=False, indent=2),
                encoding="utf-8")
            if _p.returncode != 0 or _miss:
                (run_dir / "two_pass_error.log").write_text(
                    f"status={_two['status']}\nwindows={len(_wins)}\n"
                    f"profiles={len(_profiles)}\nraw_missing={_miss}\n"
                    f"stderr:\n{_two['stderr']}\n", encoding="utf-8")
                print("two_pass degraded:", _two["status"],
                      "profiles", len(_profiles), "missing", len(_miss))
    asset_records = []
    asset_units = [u for u in units
                   if "oversized_resource_asset" in u.mechanism]
    if asset_units:
        from upi.core.asset_exam import attempt_asset_unit
        from upi.core.resource_policy import classify as _classify_asset
        from upi.core.fresh_memory import read_median as _read_mem

        def _mem():
            try:
                return _read_mem(p).get("total")
            except Exception:  # noqa: BLE001
                return None

        for _u in asset_units:
            if a.asset_attempt:
                try:
                    _r = attempt_asset_unit(
                        p, _u, measure_before=_mem, measure_after=_mem)
                    _status = _r.get("status", "no_plan")
                    _reason = _r.get("reason", "")
                except Exception as e:  # noqa: BLE001
                    _status = "no_plan"
                    _reason = f"{type(e).__name__}: {str(e)[:120]}"
            else:
                _c = _classify_asset(p, _u)
                _status = _c.get("status", "no_plan")
                _reason = _c.get("reason", "")
            asset_records.append({
                "unit_id": _u.unit_id, "target": _u.mechanism,
                "target_scope": "asset", "gone": None,
                "new_clusters": [], "invariant_ok": None,
                "metric_status": "", "metric_delta": None,
                "metric_threshold": None, "window": "",
                "before_n": 0, "after_n": 0,
                "measurement_status": "unmeasured",
                "semantic_risk": "", "patch_family": "asset",
                "sites_remaining": 0, "status": _status,
                "reason": _reason,
            })
            print("asset-unit", _u.unit_id, _status, _reason)
        units = [u for u in units if u not in asset_units]
        (run_dir / "asset_attempts.json").write_text(
            json.dumps(asset_records, ensure_ascii=False, indent=2),
            encoding="utf-8")
    print("findings", len(findings), "units", len(units))
    if _prof.name == "screen":
        (run_dir / "screen.json").write_text(json.dumps({
            "profile": "screen",
            "findings": len(findings),
            "units": [{"unit_id": u.unit_id, "mechanism": u.mechanism}
                      for u in units],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("screen only: baseline+diagnose, no repair/after")
        return 0

    cfg = N.load_config(ROOT / "llm_config.yaml")
    decide_policy = None
    if a.llm_decide:
        from upi.agents.flow_policy import llm_decide
        decide_policy = lambda f, r, m: llm_decide(cfg, f, r, m)  # noqa: E731
    store = TicketStore()
    plans, bases = {}, {}
    unit_metric: dict[str, str] = {}
    unit_inv: dict[str, tuple[str, str, str, int]] = {}
    unit_contract: dict[str, dict] = {}
    _finding_window = {f.finding_id: f.window_id for f in findings
                       if getattr(f, "window_id", "")}

    def _attach_windows(unit, contract: dict) -> None:
        """I27：contract 没有精确窗口时，从 finding 继承，绝不退化成 action 名。"""
        if not isinstance(contract, dict) or contract.get("window_ids"):
            return
        wins: list[str] = []
        for fid in (unit.finding_ids or []):
            wid = _finding_window.get(fid)
            if wid and wid not in wins:
                wins.append(wid)
        if wins:
            contract["window_ids"] = wins

    round_no = 1
    after_metrics: dict = {}
    after_start = 0.0
    cf_records: list[dict] = []
    cf_records.extend(asset_records)
    _site_cache = {"cap": "", "card": ""}

    def _site_card() -> str:
        cap = str(last_cap.get("id") or "")
        if not cap:
            return cached_site_card
        if _site_cache["cap"] == cap:
            return _site_cache["card"]
        try:
            from upi.core.observation import site_card_for
            card = site_card_for(cap, root=ROOT / "runs")
        except Exception:  # noqa: BLE001
            card = ""
        _site_cache.update(cap=cap, card=card)
        return card

    def _check_claim(pl, unit) -> bool:
        if not a.claim_gate or pl is None:
            return True
        from upi.core.claim_builder import claim_from_plan, write_claims
        from upi.core.verification_contract import (freeze_claim,
                                                    validate_claim_self)
        target = str((unit.targets or [unit.mechanism])[0])
        method = target.split(":")[-1].split(".")[-1]
        win = (pl.repair_contract or {}).get("window_indices") or []
        claim = claim_from_plan(
            pl, mechanism=str(getattr(pl, "mechanism", "") or ""),
            window_indices=[int(x) for x in win], metric=pl.metric or "",
            invariants=list(getattr(pl, "invariants_assert", []) or []),
            evidence_refs=[unit.unit_id], method=method, unit_id=unit.unit_id)
        freeze_claim(claim)
        errs = validate_claim_self(claim, pl.diffs)
        if errs:
            print("claim-gate: rejected", unit.unit_id, errs)
            return False
        _claims[unit.unit_id] = claim
        try:
            write_claims(run_dir / "claims.json", list(_claims.values()))
        except Exception:  # noqa: BLE001
            pass
        return True

    def plan(unit):
        h = next((x for x in hs
                  if str(x.get("root_cause")) in unit.targets
                  or str(x.get("root_cause")) == unit.mechanism), None)
        if h is None and unit.targets:
            anchors = [s for s in unit.sites if "::" in s]
            h = {"root_cause": unit.targets[0], "code_anchors": anchors[:4]}
        if h is None:
            return None
        _op = None
        if _oracle_plans:
            for _cand in _oracle_plans.values():
                _t = str(_cand.get("target") or "")
                if _t and any(x.endswith(_t) or _t in x
                              for x in (unit.targets or [])):
                    _op = _cand
                    break
            if _op is None:
                _op = _oracle_plans.get(unit.unit_id)
        if _op is not None:
            from upi.core.models import EditPlan
            _pl = EditPlan(playbook_id=_op.get("playbook_id", "oracle-clean"),
                           mechanism=_op.get("mechanism", ""),
                           diffs=_op.get("diffs") or [],
                           metric=_op.get("metric", ""),
                           invariants_assert=_op.get("invariants_assert") or [],
                           invariant=_op.get("invariant") or {},
                           repair_contract=_op.get("repair_contract") or {},
                           side_effect="none", permission_tier="auto")
            try:
                rp = run_dir / "plan_repeat.json"
                old = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {}
            except Exception:  # noqa: BLE001
                old = {}
            old[unit.unit_id] = {"variants": [{
                "ok": True, "key": "oracle", "metric": _pl.metric,
                "invariant": _pl.invariant, "diffs": _pl.diffs,
                "error": ""}]}
            (run_dir / "plan_repeat.json").write_text(
                json.dumps(old, ensure_ascii=False, indent=2),
                encoding="utf-8")
            if not _check_claim(_pl, unit):
                return None
            plans[unit.unit_id] = _pl
            unit_metric[unit.unit_id] = _pl.metric or ""
            unit_contract[unit.unit_id] = dict(_pl.repair_contract)
            _attach_windows(unit, unit_contract[unit.unit_id])
            print("plan", unit.unit_id, "ORACLE CLEAN")
            return PatchRef(unit.unit_id,
                            sorted({str(d.get("file", ""))
                                    for d in (_pl.diffs or [])
                                    if d.get("file")}))
        variants = []
        seen_plans = []
        meta = {}
        for _k in range(max(1, a.plan_repeat)):
            from upi.core.mechanism_inventory import site_hints
            _hints = site_hints(p, list(unit.sites or []))
            _pl, _meta = plan_issue(
                cfg, X._issue(d["issue"]), h, p,
                strategy=f"round{round_no}",
                required_sites=unit.sites,
                remaining_sites=_hints or None,
                candidate_card=_site_card(),
                mechanism_class="unknown",
                window_ids=[w for w in (_finding_window.get(fid)
                                        for fid in (unit.finding_ids or []))
                            if w])
            seen_plans.append(_pl)
            variants.append({
                "ok": _pl is not None,
                "key": plan_key(_pl),
                "metric": (_pl.metric if _pl else ""),
                "invariant": (_pl.invariant if _pl else {}),
                "diffs": (_pl.diffs if _pl else []),
                "error": _meta.get("error", ""),
            })
            meta = _meta
        min_votes = a.plan_min_votes if a.plan_repeat > 1 else 1
        pl, stable, counts = choose_consensus(seen_plans, min_votes=min_votes)
        if pl is None:
            meta = {"error": "unstable_plan",
                    "why": f"重复 {len(seen_plans)} 次无多数方案",
                    "counts": counts}
        uniq = len({v["key"] for v in variants if v["ok"]})
        print("plan", unit.unit_id, unit.mechanism, bool(pl),
              meta.get("error", ""),
              f"repeat={len(variants)} unique={uniq} stable={stable}")
        try:
            rp = run_dir / "plan_repeat.json"
            old = {}
            if rp.exists():
                try:
                    old = json.loads(rp.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    old = {}
            old[unit.unit_id] = {
                "variants": variants, "stable": stable,
                "selected": plan_key(pl) if pl else "",
                "min_votes": min_votes,
            }
            rp.write_text(json.dumps(old, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        except OSError:
            pass
        if pl is None:
            return None
        unit_metric[unit.unit_id] = pl.metric or ""
        inv = pl.invariant or {}
        if inv:
            unit_inv[unit.unit_id] = (
                "declared",
                str(inv.get("pattern") or ""),
                f"{inv.get('file','')}::{inv.get('method','')}",
                int(inv.get("expect", 0)))
        if not _check_claim(pl, unit):
            return None
        if pl.repair_contract:
            unit_contract[unit.unit_id] = dict(pl.repair_contract)
        _attach_windows(unit, unit_contract[unit.unit_id])
        plans[unit.unit_id] = pl
        bases[unit.unit_id] = X._git_head(p)
        return PatchRef(unit.unit_id, list(pl.touched_assets))

    def apply(patch):
        pl = plans.get(patch.unit_id)
        if pl is None:
            return False
        tid = "bt-" + time.strftime("%Y%m%d_%H%M%S") + "-" + patch.unit_id
        iid = "it-1"
        it = TicketItem(item_id=iid, cluster_id="llm", metric="llm",
                        shape="llm_plan", kind="llm", count=1,
                        pattern_id="llm", playbook_id=pl.playbook_id,
                        permission_tier=pl.permission_tier,
                        plan=pl.model_dump(), status="pending",
                        item_approve_token=_item_token(tid, iid))
        t = BatchTicket(ticket_id=tid, project_id="paper", mode="regression",
                        created_at=E.now(), status="awaiting_approval",
                        items=[it], approve_token=_token(tid, [it]))
        store.save(t)
        approve(t, approver="user", note="用户授权：全部批准",
                token=t.approve_token)
        if it.permission_tier != "auto":
            approve_item(t, iid, approver="user",
                         note="用户授权：全部批准",
                         token=it.item_approve_token)
        store.save(t)
        out = UnityTicketExecutor(
            store, snapshot_fn=lambda *args: SN.snapshot(*args, root=p),
            project=p, scene=p / "Assets/Scenes/00_Bootstrap.unity",
            mode="regression").execute(t)
        return out.items[0].status in ("applied", "applied_unverified")

    def compile_fn():
        import capability_run as CR
        st = CR._compile(p)
        ok = (not st.get("failed")
              and st.get("status") in {"completed", "up_to_date"})
        print("compile", ok, st.get("status"))
        return ok

    def capture_fn():
        nonlocal after_metrics, after_start, after_windows, after_n
        after_start = time.time()
        runs = []
        wins = []
        for i in range(max(1, a.after_repeats)):
            ok, _ = capture_with_recovery(
                f"batch-after-{i + 1}", two_pass=False,
                light=True, fast=False, target_windows=True)
            if not ok:
                raise RuntimeError(f"batch capture {i + 1} failed")
            runs.append(counters_since(0, last_cap["id"]))
            wins.append(window_counters(last_cap["id"]))
        after_metrics = runs
        after_windows = wins
        after_n = len(runs)
        cands, _, _ = candidates_from_latest(p, limit=12)
        return {str(c.root_cause).split(":", 1)[-1] for c in cands}

    def verify(unit, obs):
        # G21：回归形态 verified 至少需要 3 次基线；1 次只能算流程冒烟
        if a.baselines < 3:
            print("verify blocked: baselines<3 (smoke only)", a.baselines)
            _blocked = "screen_only" if _prof.name == "screen" else "needs_recheck"
            cf_records.append({"unit_id": unit.unit_id,
                               "status": _blocked,
                               "measurement_status": "unmeasured",
                               "window": "",
                               "before_n": a.baselines,
                               "after_n": after_n,
                               "reason": "baselines<3"})
            return _blocked
        target = unit.targets[0].split(":", 1)[-1] if unit.targets else unit.mechanism
        now = set(obs or [])
        gone = target not in now
        new = sorted(now - baseline_roots - {target})
        from upi.core.site_audit import sites_remaining
        _claim = None
        window_indices_obs: list[int] = []
        acceptance_doc: dict = {"status": "unassessed"}

        def finish(status: str, *, inv_ok=None, metric_status="",
                   sites=0, scope="function") -> str:
            from upi.core.repair_verify import (
                measurement_status as _measurement)
            gate = _measurement(window=window_used, after_n=after_n,
                                before_n=before_n,
                                require_n=_prof.require_after)
            if semantic_risk:
                status = "semantic_risk"
            elif gate != "measured":
                status = gate
            elif status == "verified" and patch_class == "micro":
                status = "not_structural"
            elif status == "verified" and not _prof.can_verify:
                status = "candidate_only"
            elif (a.acceptance_status and gate == "measured"
                  and acceptance_doc.get("status") not in
                  (None, "unassessed", "insufficient_evidence")):
                status = str(acceptance_doc["status"])
            cf_records.append({
                "unit_id": unit.unit_id,
                "target": target,
                "target_scope": scope,
                "gone": gone,
                "new_clusters": new,
                "invariant_ok": inv_ok,
                "metric_status": metric_status,
                "metric_delta": metric_delta,
                "metric_threshold": metric_threshold,
                "window": window_used,
                "before_n": before_n,
                "after_n": after_n,
                "measurement_status": gate,
                "semantic_risk": semantic_risk,
                "patch_family": patch_family,
                "patch_class": patch_class,
                "sites_remaining": sites,
                "acceptance": acceptance_doc,
                "same_window_units": [
                    x for x in _window_units.get(window_used, [])
                    if x != unit.unit_id],
                "status": status,
            })
            return status
        if baseline_stable and target not in baseline_stable:
            return finish("unstable_baseline", metric_status="unstable_baseline")
        bmetrics, ametrics = baseline_metrics, after_metrics
        window_used = ""
        before_n = len(baseline_runs)
        metric_delta = None
        metric_threshold = None
        _win = str((unit_contract.get(unit.unit_id) or {}).get("window") or "")
        # I27：优先精确 window_id（cmd-i-action），不再用 action→last 映射。
        _exact = list((unit_contract.get(unit.unit_id) or {}).get("window_ids")
                      or [])
        if not _exact:
            _exact = [w for w in (_finding_window.get(fid)
                                  for fid in (unit.finding_ids or []))
                      if w]
        if _exact and not window_used:
            _wid = str(_exact[0])
            _idx = exact_index(_wid)
            if _idx is not None:
                _b = [w.get(_idx) for w in baseline_windows if w.get(_idx)]
                _a = [w.get(_idx) for w in after_windows if w.get(_idx)]
                if _b and _a:
                    bmetrics, ametrics = _b, _a
                    window_used = _wid
                    window_indices_obs = [_idx]
                    print("exact-window", unit.unit_id, _wid, _idx)
        semantic_risk = ""
        patch_family = ""
        _diffs = (plans.get(unit.unit_id).diffs
                  if plans.get(unit.unit_id) else [])
        from upi.core.repair_candidate import semantic_risk_for_diffs
        semantic_risk = semantic_risk_for_diffs(_diffs)
        from upi.core.patch_shape import classify as _patch_shape
        patch_class = _patch_shape(_diffs)
        for _d in _diffs:
            _find = str(_d.get("find") or "")
            _repl = str(_d.get("replace") or "")
            if "new " in _find and "new " not in _repl:
                patch_family = "allocation"
            elif "ForceUpdate" in _find:
                patch_family = "sync"
            elif "Update" in _find and "if (" in _repl:
                patch_family = "dirty_check"
        from upi.core.repair_candidate import metric_allowed
        _m = unit_metric.get(unit.unit_id, "")
        if _m and not metric_allowed(patch_family, _m):
            return finish("metric_mismatch", metric_status="metric_mismatch",
                          sites=0)
        if patch_class == "identity":
            return finish("identity_noop", metric_status="identity_noop",
                          sites=0)
        _claim = _claims.get(unit.unit_id) if a.claim_gate else None
        if _claim and _claim.window_indices:
            _wins = list(_claim.window_indices)
            _b = [_merge_counters([w[i] for i in _wins if i in w])
                  for w in baseline_windows]
            _a = [_merge_counters([w[i] for i in _wins if i in w])
                  for w in after_windows]
            if all(_b) and all(_a):
                bmetrics, ametrics = _b, _a
                window_used = ",".join(str(i) for i in _wins)
                window_indices_obs = _wins
                print("claim-window", unit.unit_id, window_used)
        if _win and not window_used:
            _idx = index_for_window(action_idx, _win)
            if _idx is not None:
                _b = [w.get(_idx) for w in baseline_windows if w.get(_idx)]
                _a = [w.get(_idx) for w in after_windows if w.get(_idx)]
                if _b and _a:
                    bmetrics, ametrics = _b, _a
                    window_used = _win
                    print("metric-window", unit.unit_id, _win, _idx)
        # I27：三轴 acceptance 落账（暂不改 status，先让证据可见）。
        try:
            from upi.core.acceptance import verdict as _acc_verdict
            from upi.core.target_metrics import metric_series as _metric_series
            _am = unit_metric.get(unit.unit_id) or "frame_time"

            def _ser(metrics_list):
                vals: list[float] = []
                for _c in metrics_list or []:
                    vals.extend(_metric_series(_c, _am))
                return vals

            _target_idx = set(window_indices_obs)
            _ctrl_idx = pick_control_index(action_idx, _target_idx,
                                           baseline_windows, after_windows)
            _cb = ([w.get(_ctrl_idx) for w in baseline_windows
                    if w.get(_ctrl_idx)] if _ctrl_idx is not None else [])
            _ca = ([w.get(_ctrl_idx) for w in after_windows
                    if w.get(_ctrl_idx)] if _ctrl_idx is not None else [])
            _abs = (unit_contract.get(unit.unit_id) or {}).get("absolute_target") or {}
            _abs_val = _abs.get("value") if isinstance(_abs, dict) else None
            _acc = _acc_verdict(
                unit.unit_id, before=_ser(bmetrics), after=_ser(ametrics),
                control_before=_ser(_cb), control_after=_ser(_ca),
                absolute_target=_abs_val, semantic_risk=semantic_risk,
                metric=_am)
            acceptance_doc = _acc.model_dump()
            _same = [x for x in _window_units.get(window_used, [])
                     if x != unit.unit_id]
            if _same:
                acceptance_doc["same_window_units"] = _same
            print("acceptance", unit.unit_id, acceptance_doc.get("status"),
                  acceptance_doc.get("relative", {}).get("status"))
        except Exception as e:  # noqa: BLE001
            print("acceptance error", type(e).__name__, e)
        pl = plans[unit.unit_id]
        sa = sites_remaining(p, [{"file": d.get("file", ""),
                                  "find": d.get("find", ""),
                                  "replace": d.get("replace", "")}
                                 for d in pl.diffs])
        from upi.core.site_scope import sites_remaining_in_scopes
        _sa_scope = sites_remaining_in_scopes(
            p, pl.diffs, list(unit.sites or []))
        for _k, _v in _sa_scope.items():
            sa.setdefault(_k, [])
            for _s in _v:
                if _s not in sa[_k]:
                    sa[_k].append(_s)
        # I20：机制不变量优先，由补丁/源码模式推导，不查类名表
        inv = unit_inv.get(unit.unit_id)
        if inv:
            from upi.core.mechanism_checks import invariant_ok as _inv_ok
            name, pat, scope, expect = inv
            file, _, method = scope.partition("::")
            _ok, _detail = _inv_ok(p, file, method, pat, expect)
            print("invariant-verify", unit.unit_id, name, _ok, _detail,
                  "gone=", gone, "new=", new)
            # I24：invariant 只是必要条件；target/metric 才决定 verified
            metric_status = "unmeasured"
            delta_val = None
            if bmetrics and ametrics:
                from upi.core.targets import evaluate as _eval_target
                _spec, _obs, _tv = _eval_target(
                    {"finding_id": unit.unit_id, "mechanism": unit.mechanism},
                    bmetrics, ametrics,
                    metric=unit_metric.get(unit.unit_id, "frame_time"),
                    invariant_ok=True, sites_remaining=0)
                metric_status = _tv.status
                delta_val = _tv.delta
                metric_delta = _tv.delta
                metric_threshold = _tv.threshold
                print("invariant+metric", unit.unit_id, metric_status,
                      _tv.delta, _tv.threshold)
            _status = _decide_verify(
                baselines=a.baselines, invariant_ok=_ok,
                target_gone=gone, new_clusters=new,
                sites_remaining=sum(len(x) for x in sa.values()),
                metric_status=metric_status,
                require_target_gone=False)
            from upi.core.repair_verify import refine_partial as _refine
            _status = _refine(_status, delta=delta_val, new_clusters=new)
            return finish(_status, inv_ok=_ok,
                          metric_status=metric_status,
                          sites=sum(len(x) for x in sa.values()),
                          scope="mechanism")
        from upi.core.verification_v2 import evaluate_v2
        # I24：有 repair_contract 时用主指标+guard+汇率判合算性
        contract = unit_contract.get(unit.unit_id)
        if contract and bmetrics and ametrics:
            from upi.core.repair_contract import (GuardBudget,
                                                  RepairContract, evaluate)
            from upi.core.target_metrics import metric_series, stats

            def _median(metrics, name):
                if isinstance(metrics, (list, tuple)):
                    vals = []
                    for one in metrics:
                        s = stats(metric_series(one, name))
                        if s:
                            vals.append(s["median"])
                    import statistics
                    return statistics.median(vals) if vals else None
                s = stats(metric_series(metrics, name))
                return s["median"] if s else None

            primary = str(contract.get("primary_metric")
                          or unit_metric.get(unit.unit_id, "frame_time"))
            before_v = _median(bmetrics, primary)
            after_v = _median(ametrics, primary)
            guards = {}
            for g in contract.get("guards") or []:
                name = str(g.get("metric") or "")
                b = _median(bmetrics, name)
                ga = _median(ametrics, name)
                if b is not None and ga is not None:
                    guards[name] = (b, ga)
            if before_v is not None and after_v is not None:
                rc = RepairContract(
                    primary_metric=primary,
                    min_improve=float(contract.get("min_improve") or 0.05),
                    guards=[GuardBudget(
                        metric=str(g.get("metric") or ""),
                        max_relative=float(g.get("max_relative") or 0.05),
                        max_absolute=g.get("max_absolute"))
                        for g in (contract.get("guards") or [])],
                    mb_per_ms=float(contract.get("mb_per_ms") or 8.0),
                    min_net=float(contract.get("min_net") or 0.0))
                cr = evaluate(rc, primary_before=before_v,
                              primary_after=after_v, guards=guards)
                print("contract-verify", unit.unit_id, cr.status, cr.net)
                if cr.status == "regressed":
                    return finish("regressed", metric_status=cr.status)
                if (cr.status == "verified_with_tradeoff"
                        and gone and not new and not sa):
                    return finish("verified_with_tradeoff",
                                  metric_status=cr.status)
        # I20：metric delta + 机制不变量；缺失才退回 cluster
        if bmetrics and ametrics:
            from upi.core.targets import evaluate as _eval_target
            _spec, _obs, _tv = _eval_target(
                {"finding_id": unit.unit_id, "mechanism": unit.mechanism},
                bmetrics, ametrics,
                metric=unit_metric.get(unit.unit_id, "frame_time"),
                invariant_ok=True,
                sites_remaining=sum(len(x) for x in sa.values()))
            print("metric-verify", unit.unit_id, _tv.status,
                  _tv.delta, _tv.threshold)
            metric_delta = _tv.delta
            metric_threshold = _tv.threshold
            if _tv.status != "unmeasured":
                _status = ("partial" if _tv.status == "verified"
                           and (not gone or new) else _tv.status)
                from upi.core.repair_verify import refine_partial as _refine
                _status = _refine(_status, delta=_tv.delta, new_clusters=new)
                return finish(_status, metric_status=_tv.status,
                              sites=sum(len(x) for x in sa.values()))
        v = evaluate_v2(target_gone=gone, new_clusters=new, guards_ok=True,
                        sites_remaining=sa)
        print("verify", unit.unit_id, target, v.status)
        return finish(v.status)

    def rollback(patch):
        return X._git_reset(p, bases.get(patch.unit_id, ""))

    def batch_fn(active_units, round_no):
        if not a.serial:
            return run_batch(active_units, plan=plan, apply=apply,
                             compile_fn=compile_fn, capture=capture_fn,
                             verify=verify, rollback=rollback,
                             round_no=round_no)
        from upi.core.repair_tree import RepairTree, load_budget, save_tree
        combined = BatchResult(round=round_no)
        for u in active_units:
            tree = RepairTree(finding_id=u.unit_id, project_root=p,
                              budget=load_budget())
            r = run_batch([u], plan=plan, apply=apply,
                          compile_fn=compile_fn, capture=capture_fn,
                          verify=verify, rollback=rollback,
                          round_no=round_no, tree=tree)
            save_tree(tree, run_dir / u.unit_id / "repair_tree.json")
            combined.applied += r.applied
            combined.verified += r.verified
            combined.partial += r.partial
            combined.failed.update(r.failed)
            combined.rolled_back += r.rolled_back
            combined.compile_ok &= r.compile_ok
            combined.capture_ok &= r.capture_ok
        return combined

    def write_window_acceptance() -> None:
        try:
            from upi.core.acceptance import merge_window_verdicts
            doc = merge_window_verdicts(cf_records)
            (run_dir / "window_acceptance.json").write_text(
                json.dumps(doc, ensure_ascii=False, indent=2),
                encoding="utf-8")
            _multi = {w: d for w, d in doc.items() if d.get("n", 0) > 1}
            if _multi:
                print("window acceptance merged:", {
                    w: d["status"] for w, d in _multi.items()})
                if a.acceptance_status:
                    for rec in cf_records:
                        w = str(rec.get("window") or "")
                        if w in _multi:
                            rec["status"] = _multi[w]["status"]
        except Exception as e:  # noqa: BLE001
            print("window acceptance error", type(e).__name__, e)

    def write_acceptance_compare() -> None:
        try:
            from upi.core.acceptance import status_compare
            doc = status_compare(cf_records)
            (run_dir / "acceptance_compare.json").write_text(
                json.dumps(doc, ensure_ascii=False, indent=2),
                encoding="utf-8")
            print("acceptance compare: n=", doc.get("n"),
                  "changed=", doc.get("changed"))
        except Exception as e:  # noqa: BLE001
            print("acceptance compare error", type(e).__name__, e)

    def run_group_acceptance() -> int:
        """多组验收：window/files 相交的 unit 同组；每组独立 before/after。"""
        nonlocal baseline_metrics, baseline_windows, baseline_runs
        if not units:
            print("group acceptance: no units")
            return 0
        # 先 plan 全部 unit，才能按 patch files 分组；run_batch 后续复用缓存。
        patches: dict = {}
        for u in units:
            p = plan(u)
            if p is not None:
                patches[u.unit_id] = p
        active = [u for u in units if u.unit_id in patches]
        if not active:
            print("group acceptance: no planned units")
            return 1
        items = []
        for u in active:
            wins = [w for w in (_finding_window.get(fid)
                                for fid in (u.finding_ids or [])) if w]
            pl = plans.get(u.unit_id)
            files = list(pl.touched_assets) if pl else []
            items.append({"unit_id": u.unit_id, "window_ids": wins,
                          "files": files})
        if a.group_mode == "single":
            groups = [active]
        else:
            from upi.core.acceptance import group_by_overlap
            raw_groups = group_by_overlap(items)
            groups = []
            for g in raw_groups:
                ids = {x["unit_id"] for x in g}
                groups.append([u for u in active if u.unit_id in ids])
        results = []
        applied_files: set[str] = set()
        for gi, group_units in enumerate(groups, 1):
            # components 模式下，前面组的 kept patch 会改代码；本组重新 plan。
            if a.group_mode == "components" and gi > 1:
                for u in group_units:
                    p = plan(u)
                    if p is not None:
                        patches[u.unit_id] = p
            group_files = set()
            for u in group_units:
                pl = plans.get(u.unit_id)
                if pl is not None:
                    group_files |= set(pl.touched_assets or [])
            if a.group_mode == "components" and (group_files & applied_files):
                conflict = sorted(group_files & applied_files)
                print("group acceptance: plan conflict", gi, conflict)
                for u in group_units:
                    cf_records.append({
                        "unit_id": u.unit_id, "status": "plan_conflict",
                        "measurement_status": "unmeasured", "window": "",
                        "before_n": 3, "after_n": 0,
                        "reason": "replan files intersect already kept patches"})
                results.append({"group": gi,
                                "units": [u.unit_id for u in group_units],
                                "before_n": 3,
                                "plan_conflict": conflict,
                                "result": {}})
                continue
            bm: list[dict] = []
            bw: list[dict] = []
            for i in range(3):
                ok, _ = capture_with_recovery(
                    f"group{gi}-before-{i + 1}", two_pass=False,
                    light=True, target_windows=True)
                if not ok:
                    print("group acceptance: before capture failed", gi, i + 1)
                    return 1
                bm.append(counters_since(0, last_cap["id"]))
                bw.append(window_counters(last_cap["id"]))
            baseline_metrics, baseline_windows, baseline_runs = bm, bw, bm
            (run_dir / f"group_before_{gi}.json").write_text(json.dumps(
                {"group": gi, "before_n": len(bm),
                 "units": [u.unit_id for u in group_units]},
                ensure_ascii=False, indent=2), encoding="utf-8")
            print("group acceptance: group", gi, "units",
                  len(group_units), "before", len(bm))
            _cf0 = len(cf_records)
            _group_base = X._git_head(p)
            res = run_batch(
                group_units,
                plan=lambda u: patches.get(u.unit_id) or plan(u),
                apply=apply, compile_fn=compile_fn, capture=capture_fn,
                verify=verify, rollback=rollback, round_no=1)
            # I27：同一 exact window 的 merged verdict 不是 verified → 整窗回滚。
            # 用组前 git 快照 reset，然后重放本组 verified 补丁，避免逐 unit reset
            # 在共享文件上互相覆盖。
            _window_rollback = []
            try:
                from upi.core.acceptance import merge_window_verdicts
                wdoc = merge_window_verdicts(cf_records[_cf0:])
                _bad: set[str] = set()
                for w, d in wdoc.items():
                    if d.get("n", 0) <= 1 or d.get("status") == "verified":
                        continue
                    for uid in d.get("units") or []:
                        _bad.add(uid)
                        res.failed[uid] = f"window_{d['status']}"
                        if uid not in res.rolled_back:
                            res.rolled_back.append(uid)
                    for rec in cf_records[_cf0:]:
                        if str(rec.get("unit_id") or "") in (d.get("units") or []):
                            rec["window_status"] = d["status"]
                            rec["status"] = "window_rollback"
                    _window_rollback.append({"window": w,
                                             "status": d["status"],
                                             "units": d.get("units") or []})
                if _bad:
                    _ok_reset = bool(_group_base and X._git_reset(p, _group_base))
                    if not _ok_reset:
                        for uid in _bad:
                            pch = patches.get(uid)
                            if pch is not None:
                                rollback(pch)
                    else:
                        _keep = [u for u in group_units
                                 if u.unit_id not in _bad
                                 and u.unit_id in set(res.verified)]
                        res.applied = []
                        res.verified = []
                        _reapply_ok = True
                        for u in _keep:
                            pch = patches.get(u.unit_id)
                            if pch is None or not apply(pch):
                                _reapply_ok = False
                                break
                            res.applied.append(u.unit_id)
                            res.verified.append(u.unit_id)
                        if _reapply_ok and res.applied:
                            res.compile_ok = bool(compile_fn())
                            if not res.compile_ok:
                                X._git_reset(p, _group_base)
                                res.applied = []
                                res.verified = []
                                for u in _keep:
                                    res.failed[u.unit_id] = "window_reapply_compile_failed"
                        elif not _reapply_ok:
                            X._git_reset(p, _group_base)
                            res.applied = []
                            res.verified = []
                            for u in _keep:
                                res.failed[u.unit_id] = "window_reapply_failed"
                    for uid in _bad:
                        if uid in res.verified:
                            res.verified.remove(uid)
            except Exception as e:  # noqa: BLE001
                print("window rollback error", type(e).__name__, e)
            print("group acceptance result", gi, res)
            results.append({"group": gi,
                            "units": [u.unit_id for u in group_units],
                            "before_n": len(bm),
                            "window_rollback": _window_rollback,
                            "result": res.__dict__})
            applied_files |= group_files
        (run_dir / "group_acceptance.json").write_text(json.dumps(
            {"mode": a.group_mode, "groups": results,
             "same_window_units": {w: ids for w, ids
                                   in _window_units.items() if len(ids) > 1}},
            ensure_ascii=False, indent=2), encoding="utf-8")
        write_window_acceptance()
        (run_dir / "counterfactual.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in cf_records),
            encoding="utf-8")
        write_acceptance_compare()
        verified = sum(len(r["result"].get("verified") or []) for r in results)
        run.stop_reason = f"groups={len(results)}:verified={verified}"
        save_run(run)
        return 0

    if a.group_acceptance:
        return run_group_acceptance()

    active = list(units)
    flow.enter("plan", active=[u.unit_id for u in active])
    all_verified: set[str] = set()
    for r in range(1, max(1, a.rounds) + 1):
        round_no = r
        if not active:
            break
        flow.enter("round", round_no=r,
                   active=[u.unit_id for u in active])
        res, decision = flow.run_round(
            active, r, a.rounds, batch_fn=batch_fn, policy=decide_policy)
        print("batch", r, res)
        all_verified.update(res.verified)
        for u in active:
            pl = plans.get(u.unit_id)
            run.repairs.append(RepairRecord(
                finding_id=(u.finding_ids[0] if u.finding_ids else ""),
                plan_id=(pl.playbook_id if pl else ""),
                files=(pl.touched_assets if pl else []),
                compile_ok=res.compile_ok and bool(pl),
                verdict=("verified" if u.unit_id in res.verified
                         else res.failed.get(u.unit_id, "not_run")),
                attempt_no=r, stop_reason=res.failed.get(u.unit_id, "")))
        failed = set(res.failed) - {"__compile__", "__capture__"}
        if decision != "retry":
            active = []
            break
        active = [u for u in units if u.unit_id in failed]
        if not active:
            break
    flow.enter("record", verified=sorted(all_verified), route="done")
    write_window_acceptance()
    write_acceptance_compare()
    try:
        (run_dir / "counterfactual.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in cf_records),
            encoding="utf-8")
        from upi.core.repair_tree import RepairTree, save_tree
        for rec in cf_records:
            uid = str(rec.get("unit_id") or "unit")
            target = run_dir / uid / "repair_tree.json"
            if target.exists():
                continue
            tree = RepairTree(finding_id=uid,
                              metric_before=rec.get("metric_status"))
            tree.record(str(rec.get("status") or "no_effect"),
                        delta=rec.get("gone"))
            save_tree(tree, target)
    except OSError:
        pass
    run.stop_reason = f"batch:verified={len(all_verified)}"
    save_run(run)
    print("scoring deferred to bench/scorer.py")
    print("elapsed", round(time.time() - t0, 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
