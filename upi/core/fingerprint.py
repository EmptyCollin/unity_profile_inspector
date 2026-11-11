"""指纹库（I2）：cluster 特征 → 已知 pattern/playbook，命中跳过 LLM。

纪律（AGENTS §8.7 / ADR-0003）：
- 指纹是**路由/缓存**，不是证据；命中仍走权限、验证、回滚；
- 只记机制级特征，不记文件名/类名/函数名；
- 命中/未命中/成功/失败都计数，供“LLM 调用数 / 100 事件”“命中后成功率”用；
- 库是本地 SQLite，单进程读写；默认 `knowledge/fingerprints.db`。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from upi.core.cluster import IssueCluster

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "knowledge" / "fingerprints.db"


class FingerprintFeatures(BaseModel):
    metric: str
    shape: str
    kind: str = "sample"
    data_tier: str = "A2"
    rel_bucket: int = -1          # -1 = 不参与匹配（内置 pattern 用）

    def fingerprint_id(self) -> str:
        raw = json.dumps(self.model_dump(), ensure_ascii=False, sort_keys=True)
        return "fp-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


class FingerprintRecord(BaseModel):
    fingerprint_id: str
    features: dict
    pattern_id: str
    root_cause: str = ""
    playbook_id: str = ""
    confidence: float = 0.0
    n_hits: int = 0
    n_misses: int = 0
    n_success: int = 0
    n_fail: int = 0
    last_seen: str = ""
    source: str = "learned"       # builtin | learned | manual
    status: str = "active"        # active | dormant


class FingerprintHit(BaseModel):
    hit: bool
    record: Optional[FingerprintRecord] = None
    score: float = 0.0
    llm_required: bool = True
    reason: str = ""


def features_from_cluster(c: IssueCluster) -> FingerprintFeatures:
    f = c.features()
    return FingerprintFeatures(
        metric=str(f["metric"]), shape=str(f["shape"]), kind=str(f["kind"]),
        data_tier=str(f["data_tier"]), rel_bucket=int(f["rel_bucket"]),
    )


def _score(a: FingerprintFeatures, b: dict) -> float:
    if (a.metric != b.get("metric") or a.shape != b.get("shape")
            or a.kind != b.get("kind")):
        return 0.0
    score = 0.5 + 0.3
    if a.data_tier == b.get("data_tier"):
        score += 0.1
    rb = int(b.get("rel_bucket", -1))
    if rb == -1:
        score += 0.1
    elif a.rel_bucket == rb:
        score += 0.1
    elif abs(a.rel_bucket - rb) == 1:
        score += 0.04
    return round(min(1.0, score), 4)


class FingerprintStore:
    def __init__(self, path: Optional[Path | str] = None):
        self.path = Path(path) if path else DEFAULT_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS fingerprints (
                  fingerprint_id TEXT PRIMARY KEY,
                  features_json TEXT NOT NULL,
                  pattern_id TEXT NOT NULL,
                  root_cause TEXT DEFAULT '',
                  playbook_id TEXT DEFAULT '',
                  confidence REAL DEFAULT 0,
                  n_hits INTEGER DEFAULT 0,
                  n_misses INTEGER DEFAULT 0,
                  n_success INTEGER DEFAULT 0,
                  n_fail INTEGER DEFAULT 0,
                  last_seen TEXT DEFAULT '',
                  source TEXT DEFAULT 'learned',
                  status TEXT DEFAULT 'active'
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS fingerprint_misses (
                  features_json TEXT NOT NULL,
                  ts TEXT NOT NULL
                )
            """)
            c.commit()

    def _row_to_record(self, row) -> FingerprintRecord:
        return FingerprintRecord(
            fingerprint_id=row["fingerprint_id"],
            features=json.loads(row["features_json"] or "{}"),
            pattern_id=row["pattern_id"], root_cause=row["root_cause"] or "",
            playbook_id=row["playbook_id"] or "", confidence=row["confidence"] or 0,
            n_hits=row["n_hits"] or 0, n_misses=row["n_misses"] or 0,
            n_success=row["n_success"] or 0, n_fail=row["n_fail"] or 0,
            last_seen=row["last_seen"] or "", source=row["source"] or "learned",
            status=row["status"] or "active",
        )

    def upsert(self, rec: FingerprintRecord) -> str:
        with self._conn() as c:
            c.execute("""
                INSERT INTO fingerprints (fingerprint_id, features_json, pattern_id,
                  root_cause, playbook_id, confidence, n_hits, n_misses, n_success,
                  n_fail, last_seen, source, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(fingerprint_id) DO UPDATE SET
                  features_json=excluded.features_json,
                  pattern_id=excluded.pattern_id,
                  root_cause=excluded.root_cause,
                  playbook_id=excluded.playbook_id,
                  confidence=excluded.confidence,
                  last_seen=excluded.last_seen,
                  source=excluded.source,
                  status=excluded.status
            """, (rec.fingerprint_id, json.dumps(rec.features, ensure_ascii=False,
                                                 sort_keys=True),
                  rec.pattern_id, rec.root_cause, rec.playbook_id, rec.confidence,
                  rec.n_hits, rec.n_misses, rec.n_success, rec.n_fail,
                  rec.last_seen, rec.source, rec.status))
            c.commit()
        return rec.fingerprint_id

    def get(self, fid: str) -> FingerprintRecord | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM fingerprints WHERE fingerprint_id=?",
                            (fid,)).fetchone()
        return self._row_to_record(row) if row else None

    def all(self) -> list[FingerprintRecord]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM fingerprints ORDER BY pattern_id").fetchall()
        return [self._row_to_record(r) for r in rows]

    def lookup(self, features: FingerprintFeatures,
               min_score: float = 0.8) -> tuple[FingerprintRecord | None, float, float]:
        """返回 (最佳记录, 最佳分, 次佳分)。次佳用于判“歧义 → 仍需 LLM”。"""
        scored = []
        for rec in self.all():
            if rec.status != "active":
                continue
            s = _score(features, rec.features)
            if s > 0:
                scored.append((s, rec))
        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored:
            return None, 0.0, 0.0
        best_s, best = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        if best_s < min_score:
            return None, best_s, second
        return best, best_s, second

    def route(self, features: FingerprintFeatures) -> FingerprintHit:
        best, s, second = self.lookup(features)
        if best is None:
            self.record_miss(features)
            return FingerprintHit(hit=False, score=s, llm_required=True,
                                  reason="no_match")
        # 两个命中分太近 = 歧义；宁可让 LLM/人工判，不赌
        if second and (s - second) < 0.05:
            self.record_miss(features)
            return FingerprintHit(hit=False, record=best, score=s,
                                  llm_required=True, reason="ambiguous")
        self._bump(best.fingerprint_id, hit=True)
        return FingerprintHit(hit=True, record=best, score=s,
                              llm_required=False, reason="match")

    def _bump(self, fid: str, *, hit: bool):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._conn() as c:
            if hit:
                c.execute("UPDATE fingerprints SET n_hits=n_hits+1, last_seen=? "
                          "WHERE fingerprint_id=?", (now, fid))
            else:
                c.execute("UPDATE fingerprints SET n_misses=n_misses+1, last_seen=? "
                          "WHERE fingerprint_id=?", (now, fid))
            c.commit()

    def record_miss(self, features: FingerprintFeatures):
        with self._conn() as c:
            c.execute("INSERT INTO fingerprint_misses (features_json, ts) VALUES (?,?)",
                      (json.dumps(features.model_dump(), ensure_ascii=False, sort_keys=True),
                       time.strftime("%Y-%m-%dT%H:%M:%S")))
            c.commit()

    def record_outcome(self, fid: str, success: bool):
        with self._conn() as c:
            if success:
                c.execute("UPDATE fingerprints SET n_success=n_success+1 WHERE "
                          "fingerprint_id=?", (fid,))
            else:
                c.execute("UPDATE fingerprints SET n_fail=n_fail+1 WHERE "
                          "fingerprint_id=?", (fid,))
            c.commit()

    def ensure_seed(self) -> int:
        with self._conn() as c:
            n = c.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0]
        if n:
            return 0
        return self.seed_builtin()

    def seed_builtin(self) -> int:
        seeds = [
            ("gc_alloc_per_frame", "step", "sample", "A2",
             "per_frame_heap_alloc_in_hot_path", "PB-REUSE-BUFFER"),
            ("drawCalls", "step", "sample", "A2",
             "per_renderer_material_instantiation", "PB-SHARED-MAT-MPB"),
            ("setPassCalls", "step", "sample", "A2",
             "per_renderer_material_instantiation", "PB-SHARED-MAT-MPB"),
            ("animator_process_animations_ns", "step", "sample", "A2",
             "cross_layer_state_query_per_frame", "PB-PARAM-EQUAL-GUARD"),
            ("totalAllocatedBytes", "spike", "sample", "A",
             "sync_resources_load_in_start", "PB-ASYNC-STAGGER"),
            ("frame_time_ms", "spike", "breach", "A4",
             "frame_time_spike_fact_only", ""),
        ]
        n = 0
        for metric, shape, kind, tier, root, pb in seeds:
            f = FingerprintFeatures(metric=metric, shape=shape, kind=kind,
                                    data_tier=tier, rel_bucket=-1)
            rec = FingerprintRecord(
                fingerprint_id=f.fingerprint_id(), features=f.model_dump(),
                pattern_id=f"builtin:{metric}:{shape}", root_cause=root,
                playbook_id=pb, confidence=0.7, source="builtin",
                last_seen="",
            )
            self.upsert(rec)
            n += 1
        return n

    def stats(self) -> dict:
        recs = self.all()
        with self._conn() as c:
            miss_rows = c.execute("SELECT COUNT(*) FROM fingerprint_misses").fetchone()[0]
        return {
            "total": len(recs),
            "builtin": sum(1 for r in recs if r.source == "builtin"),
            "learned": sum(1 for r in recs if r.source != "builtin"),
            "hits": sum(r.n_hits for r in recs),
            "misses": sum(r.n_misses for r in recs) + miss_rows,
            "success": sum(r.n_success for r in recs),
            "fail": sum(r.n_fail for r in recs),
            "by_pattern": {r.pattern_id: r.n_hits for r in recs if r.n_hits},
        }
