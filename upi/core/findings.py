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

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

    @field_validator("anchors")
    @classmethod
    def _needs_anchor(cls, v: list[Anchor]) -> list[Anchor]:
        if not v:
            raise ValueError("ToolFinding 至少需要一个 anchor（禁止无位置结论）")
        return v


class RepairRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_id: str = ""
    plan_id: str = ""
    files: list[str] = Field(default_factory=list)
    compile_ok: bool = False
    gameplay_ok: Optional[bool] = None
    verdict: str = ""            # resolved | not_significant | regressed | unverified
    metric_delta: Optional[float] = None


class RunFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    project_id: str
    mode: Literal["resident", "regression"] = "resident"
    protocol_fingerprint: str = ""
    created_at: str
    findings: list[ToolFinding] = Field(default_factory=list)
    repairs: list[RepairRecord] = Field(default_factory=list)

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

def _split_anchor(anchor: str) -> tuple[str, str]:
    """oracle 的 `Assets/.../File.cs::Method` → (file, method)。"""
    if "::" in anchor:
        f, m = anchor.split("::", 1)
        return f.strip().replace("\\", "/"), m.strip()
    return anchor.strip().replace("\\", "/"), ""


def _anchor_matches(a: Anchor, file: str, method: str) -> bool:
    if a.file.replace("\\", "/") != file:
        return False
    if method:
        return bool(a.method) and a.method.split(".")[-1] == method.split(".")[-1]
    # oracle 没有方法名时，退化为行区间重叠（都未给行号则只按文件算命中，但需 state 明确）
    if a.start_line and a.end_line:
        return True
    return bool(a.method)


def score_run(paper: dict, run: RunFindings) -> dict:
    """按能力维度判分：发现（位置匹配）/ 误报 / 修复 / 可复现。"""
    expectations = paper.get("expectations") or []
    exp = [(e.get("mechanism", ""), *_split_anchor(str(e.get("anchor", ""))),
            e.get("metric", "")) for e in expectations]
    findings = run.findings
    matched: list[dict] = []
    used: set[int] = set()
    for mech, file, method, metric in exp:
        hit = None
        for i, f in enumerate(findings):
            if i in used:
                continue
            if any(_anchor_matches(a, file, method) for a in f.anchors):
                hit = (i, f)
                break
        if hit:
            used.add(hit[0])
            matched.append({"mechanism": mech, "anchor": f"{file}::{method}",
                            "metric": metric, "finding_id": hit[1].finding_id,
                            "finding_mechanism": hit[1].mechanism})
    fn = [{"mechanism": m, "anchor": f"{f}::{meth}", "metric": mt}
          for (m, f, meth, mt) in exp
          if not any(x["mechanism"] == m and x["anchor"] == f"{f}::{meth}"
                     for x in matched)]
    fp = [{"finding_id": f.finding_id, "mechanism": f.mechanism,
           "anchors": [f"{a.file}::{a.method}" for a in f.anchors]}
          for i, f in enumerate(findings) if i not in used]

    repairs = {r.finding_id: r for r in run.repairs if r.finding_id}
    repair_rows = []
    for m in matched:
        r = repairs.get(m["finding_id"])
        repair_rows.append({
            "finding_id": m["finding_id"],
            "plan_id": (r.plan_id if r else ""),
            "compile_ok": bool(r and r.compile_ok),
            "gameplay_ok": (r.gameplay_ok if r else None),
            "verdict": (r.verdict if r else ""),
            "effective": bool(r and r.compile_ok and r.gameplay_ok is not False
                              and r.verdict in ("resolved", "verified")),
        })

    negative = not exp
    discovery = {
        "expected": len(exp), "found": len(findings),
        "tp": len(matched), "fp": len(fp), "fn": len(fn),
        "precision": round(len(matched) / max(1, len(findings)), 4),
        "recall": round(len(matched) / max(1, len(exp)), 4) if exp else None,
        "matched": matched, "false_positive": fp, "missed": fn,
    }
    repair = {
        "matched": len(matched),
        "compiled": sum(1 for r in repair_rows if r["compile_ok"]),
        "effective": sum(1 for r in repair_rows if r["effective"]),
        "rows": repair_rows,
    }
    # 可复现：同一协议指纹的多次运行，findings 位置集合是否一致
    same = [r for r in load_runs(run.project_id)
            if r.run_id != run.run_id
            and r.protocol_fingerprint
            and r.protocol_fingerprint == run.protocol_fingerprint]
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
                           "no_false_positive": (not fp) if negative else None,
                           "repair": repair,
                           "reproducible": repro},
            "negative_control": negative,
            "negative_ok": (len(findings) == 0) if negative else None,
            "run": {"run_id": run.run_id, "mode": run.mode,
                    "protocol_fingerprint": run.protocol_fingerprint,
                    "created_at": run.created_at}}
