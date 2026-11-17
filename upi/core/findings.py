"""工具运行产物契约（能力评分的数据面）。

设计约束：
- 只记录**工具实际产出的诊断/修复/验证结果**，不从 oracle、case 编号或正则里拿信息；
- 一次运行 = `runs/<run_id>/findings.json`，并按 project 建索引；
- 字段是"机制 + 位置 + 证据 + 效果"，供考卷按能力判分（不是按 pattern/正则）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"


class Anchor(BaseModel):
    """代码位置：文件 + 方法/行区间（至少一个可用的定位方式）。"""
    model_config = ConfigDict(extra="forbid")
    file: str
    method: str = ""
    start_line: int = 0
    end_line: int = 0

    @field_validator("file")
    @classmethod
    def _file_required(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("anchor.file 不能为空")
        return v.strip().replace("\\", "/")


class AttributionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    factors: dict = Field(default_factory=dict)


class ToolFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_id: str
    mechanism: str = ""
    metric: str = ""
    link: str = ""
    anchors: list[Anchor] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    attribution: AttributionInfo = Field(default_factory=AttributionInfo)
    state: Literal["candidate", "confirmed", "needs_human", "false_positive"] = "candidate"
    # I27：finding-first；window_id 形如 cmd-10-open_shop
    window_id: str = ""
    window_ids: list[str] = Field(default_factory=list)
    magnitude: float = 0.0
    threshold: float = 0.0

    @field_validator("anchors")
    @classmethod
    def _needs_anchor(cls, v: list[Anchor]) -> list[Anchor]:
        if not v:
            raise ValueError("ToolFinding 至少需要一个 anchor（禁止无位置结论）")
        return v

    @model_validator(mode="after")
    def _sync_window_ids(self):
        if self.window_id and not self.window_ids:
            self.window_ids = [self.window_id]
        elif self.window_ids and not self.window_id:
            self.window_id = self.window_ids[0]
        return self


class RepairRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_id: str = ""
    plan_id: str = ""
    files: list[str] = Field(default_factory=list)
    compile_ok: bool = False
    runnable: Optional[bool] = None   # 编译后能否正常进 Play 并启动一局（体验门槛）
    gameplay_ok: Optional[bool] = None  # 保留字段：玩法自测不计入考试
    verdict: str = ""            # resolved | not_significant | regressed | unverified
    metric_delta: Optional[float] = None
    attempt_no: int = 0
    ticket_id: str = ""
    diff_hash: str = ""
    stop_reason: str = ""


class RunFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    project_id: str
    mode: Literal["resident", "regression"] = "resident"
    protocol_fingerprint: str = ""
    created_at: str
    findings: list[ToolFinding] = Field(default_factory=list)
    repairs: list[RepairRecord] = Field(default_factory=list)
    attempts: list[dict] = Field(default_factory=list)
    stop_reason: str = ""
    evidence_rounds: int = 0
    blind_spots: list[str] = Field(default_factory=list)
    usability_ok: Optional[bool] = None
    usability_note: str = ""

    def path(self) -> Path:
        return RUNS / self.run_id / "findings.json"


def _index_path(project_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in project_id)
    return RUNS / "projects" / safe / "index.json"


def save_run(run: RunFindings) -> Path:
    p = run.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    idx = _index_path(run.project_id)
    idx.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    if idx.exists():
        try:
            entries = json.loads(idx.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            entries = []
    entries = [e for e in entries if e.get("run_id") != run.run_id]
    entries.append({"run_id": run.run_id, "created_at": run.created_at,
                    "mode": run.mode,
                    "protocol_fingerprint": run.protocol_fingerprint,
                    "findings": len(run.findings), "repairs": len(run.repairs)})
    entries.sort(key=lambda e: e.get("created_at", ""))
    idx.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_run(run_id: str) -> Optional[RunFindings]:
    p = RUNS / run_id / "findings.json"
    if not p.exists():
        return None
    try:
        return RunFindings(**json.loads(p.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return None


def load_runs(project_id: str) -> list[RunFindings]:
    idx = _index_path(project_id)
    if not idx.exists():
        return []
    try:
        entries = json.loads(idx.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for e in entries:
        r = load_run(str(e.get("run_id", "")))
        if r is not None:
            out.append(r)
    out.sort(key=lambda r: r.created_at)
    return out


def new_run_id(project_id: str) -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_id)
    return f"{safe}_{stamp}"


# ─────────────────────────── 能力判分（无正则/无 pattern 匹配）───────────────

def _norm_file(f: str) -> str:
    f = (f or "").replace("\\", "/").strip()
    i = f.find("Assets/")
    return f[i:] if i >= 0 else f


def _norm_method(m: str) -> str:
    m = (m or "").strip().replace("()", "")
    if "::" in m:
        m = m.split("::")[-1]
    return m.split(".")[-1]


def normalize_file(f: str) -> str:
    return _norm_file(f)


def normalize_method(m: str) -> str:
    return _norm_method(m)


def _split_anchor(anchor: str) -> tuple[str, str]:
    """oracle 的 `Assets/.../File.cs::Method` → (file, method)，统一归一化。"""
    if "::" in anchor:
        f, m = anchor.split("::", 1)
        return _norm_file(f), _norm_method(m)
    return _norm_file(anchor), ""


def _norm_ts(s: str) -> str:
    return str(s or "").replace("T", " ")


def _anchor_matches(a: Anchor, file: str, method: str,
                    accept: list[str] | tuple[str, ...] = ()) -> bool:
    if _norm_file(a.file) != file:
        return False
    if method:
        am = _norm_method(a.method)
        return bool(am) and (am == method
                             or am in {_norm_method(x) for x in accept})
    if accept:
        return _norm_method(a.method) in {_norm_method(x) for x in accept}
    # oracle 只给文件（例如资产类），同文件即命中
    return True


def score_run(paper: dict, run: RunFindings) -> dict:
    """按能力维度判分：发现（位置匹配）/ 误报 / 修复 / 可复现。

    I11f：最终可用性硬门禁——交付物不可用，整卷 0。
    """
    if run.usability_ok is False:
        zero = {"discovery": {"expected": 0, "found": 0, "tp": 0, "fp": 0,
                              "fn": 0, "precision": None, "recall": None,
                              "matched": [], "false_positive": [], "missed": []},
                "attribution": {"top1_hit": None, "evidence_refs_ratio": None,
                                "n": 0, "ece": None},
                "no_false_positive": None,
                "repair": {"matched": 0, "compiled": 0, "runnable": 0,
                           "effective": 0, "rows": []},
                "reproducible": {"runs": 1, "consistent": None}}
        return {"evaluated": True, "usability_failed": True,
                "usability_note": run.usability_note,
                "dimensions": zero, "negative_control": False,
                "negative_ok": None,
                "run": {"run_id": run.run_id, "mode": run.mode,
                        "protocol_fingerprint": run.protocol_fingerprint,
                        "created_at": run.created_at}}
    expectations = paper.get("expectations") or []
    exp = [(e.get("mechanism", ""), *_split_anchor(str(e.get("anchor", ""))),
            e.get("metric", ""),
            tuple(e.get("accept_methods") or [])) for e in expectations]
    findings = run.findings
    matched: list[dict] = []
    used: set[int] = set()
    for mech, file, method, metric, accept in exp:
        hit = None
        for i, f in enumerate(findings):
            if i in used:
                continue
            if any(_anchor_matches(a, file, method, accept) for a in f.anchors):
                hit = (i, f)
                break
        if hit:
            used.add(hit[0])
            matched.append({"mechanism": mech, "anchor": f"{file}::{method}",
                            "metric": metric, "finding_id": hit[1].finding_id,
                            "finding_mechanism": hit[1].mechanism})
    fn = [{"mechanism": m, "anchor": f"{f}::{meth}", "metric": mt}
          for (m, f, meth, mt, _acc) in exp
          if not any(x["mechanism"] == m and x["anchor"] == f"{f}::{meth}"
                     for x in matched)]
    # FP：不属于任何期望（含 accept_methods 容忍）的 finding；与是否已匹配无关
    def _matches_any(f) -> bool:
        for _m, _f, _meth, _mt, _acc in exp:
            if any(_anchor_matches(a, _f, _meth, _acc) for a in f.anchors):
                return True
        return False

    extra_records = [{"finding_id": f.finding_id, "mechanism": f.mechanism,
                      "anchors": [f"{a.file}::{a.method}" for a in f.anchors],
                      "evidence_refs": list(f.evidence_refs)}
                     for f in findings if not _matches_any(f)]
    # 无证据支撑的“额外发现”才算虚假断言；有证据的属于 extra，不记 FP
    fp = [x for x in extra_records if not x["evidence_refs"]]
    extra_valid = [x for x in extra_records if x["evidence_refs"]]

    repair_rows = []
    for m in matched:
        rs = [r for r in run.repairs if r.finding_id == m["finding_id"]]
        if not rs:
            repair_rows.append({
                "finding_id": m["finding_id"], "plan_id": "",
                "compile_ok": False, "runnable": None,
                "verdict": "", "effective": False, "attempt_no": 0,
            })
            continue
        for r in rs:
            repair_rows.append({
                "finding_id": m["finding_id"],
                "plan_id": r.plan_id,
                "compile_ok": bool(r.compile_ok),
                "runnable": r.runnable,
                "verdict": r.verdict,
                "attempt_no": r.attempt_no,
                "stop_reason": r.stop_reason,
                "effective": bool(r.compile_ok and r.gameplay_ok is not False
                                  and r.verdict in ("resolved", "verified")),
            })

    negative = not exp
    discovery = {
        "expected": len(exp), "found": len(findings),
        "tp": len(matched), "fp": len(fp), "fn": len(fn),
        "precision": round(len(matched) / max(1, len(matched) + len(fp)), 4),
        "recall": round(len(matched) / max(1, len(exp)), 4) if exp else None,
        "matched": matched, "false_positive": fp, "missed": fn,
        "extra": extra_valid, "extra_n": len(extra_valid),
    }
    # 归因质量（I10）：Top1 是否命中 + 证据引用覆盖率；ECE 需历史结局，n<30 不报
    top1 = findings[0] if findings else None
    top1_hit = bool(top1 and any(m["finding_id"] == top1.finding_id
                                 for m in matched))
    refs_ratio = (round(sum(1 for f in findings if f.evidence_refs)
                        / len(findings), 4) if findings else None)
    attribution = {"top1_hit": top1_hit if top1 else None,
                   "evidence_refs_ratio": refs_ratio,
                   "n": len(findings), "ece": None,
                   "note": "ECE 需本项目历史结局；n<30 不报趋势"}
    repair = {
        "matched": len(matched),
        "compiled": len({r["finding_id"] for r in repair_rows if r["compile_ok"]}),
        "runnable": len({r["finding_id"] for r in repair_rows if r["runnable"]}),
        "effective": len({r["finding_id"] for r in repair_rows if r["effective"]}),
        "attempts": len(run.repairs),
        "rows": repair_rows,
    }
    # 可复现：同一协议指纹、且在本考卷生成之后的多次运行，findings 位置集合是否一致
    since = _norm_ts(paper.get("created_at") or "")
    same = [r for r in load_runs(run.project_id)
            if r.run_id != run.run_id
            and r.protocol_fingerprint
            and r.protocol_fingerprint == run.protocol_fingerprint
            and (not since or _norm_ts(r.created_at) >= since)]
    if same:
        def key(r):
            return {(a.file, a.method) for f in r.findings for a in f.anchors}
        sets = [key(r) for r in same] + [key(run)]
        reproducible = all(s == sets[0] for s in sets)
        repro = {"runs": len(sets), "consistent": reproducible}
    else:
        repro = {"runs": 1, "consistent": None}

    return {"evaluated": True,
            "dimensions": {"discovery": discovery,
                           "attribution": attribution,
                           "no_false_positive": (not fp) if negative else None,
                           "repair": repair,
                           "reproducible": repro},
            "negative_control": negative,
            "negative_ok": (len(findings) == 0) if negative else None,
            "run": {"run_id": run.run_id, "mode": run.mode,
                    "protocol_fingerprint": run.protocol_fingerprint,
                    "created_at": run.created_at,
                    "stop_reason": run.stop_reason}}
