"""KnowledgeStore（M2，DESIGN §6 / AGENTS §1.7）。

职责边界：只做**记录与提案**，不做执行——权限矩阵变更必须人工一次性批准（§1.7）。

口径要点（都是文档里定死的，这里逐条落成代码）：
- 三重签名（症状×根因×动作）才算同一条经验；只标签相同不算（§6）。
- n 一律 n_effective = 独立场景 × 档位（§8.2）；同一上下文重复运行只算
  复现验证，不进成功率分母。
- 成功率 = resolved / (resolved + failed)；needs_human 单列不进分母，
  但自动率与它必须成对展示（G15）——只看一个就是自欺。
- 升维四条件（§3 维度三）任一不满足或缺体检表 → blocked，不得提交人工。
- 降级（硬）：1 次"引入新问题被回滚"立即降回 semi 并要求人工复盘。
- 所有新 pattern 一律 semi 起步，无例外。
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path

from upi.core.models import PatternPromotionProposal

_SCHEMA = """
CREATE TABLE IF NOT EXISTS patterns(
  id TEXT PRIMARY KEY,
  symptom_sig TEXT NOT NULL, link TEXT NOT NULL,
  root_cause_sig TEXT NOT NULL, action_sig TEXT NOT NULL,
  playbook_id TEXT, side_effect TEXT,
  tier TEXT NOT NULL DEFAULT 'semi',          -- 新 pattern 一律 semi 起步
  n_new_issue INTEGER NOT NULL DEFAULT 0,
  dormant INTEGER NOT NULL DEFAULT 0,
  needs_review INTEGER NOT NULL DEFAULT 0,
  created_at REAL, updated_at REAL,
  UNIQUE(symptom_sig, root_cause_sig, action_sig)   -- 三重签名 = 同一条经验
);
CREATE TABLE IF NOT EXISTS outcomes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  issue_id TEXT, pattern_id TEXT NOT NULL, run_id TEXT,
  fixture TEXT, quality_tier TEXT, profile TEXT,   -- n_effective 的上下文键（§8.2）
  state TEXT NOT NULL,                        -- resolved|failed|needs_human|rolled_back
  metric_delta REAL, guards_json TEXT,
  realized_over_expected REAL, review_rounds INTEGER,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS attributions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pattern_id TEXT, factor_scores_json TEXT, c_attr REAL, c_act REAL, outcome TEXT
);
CREATE TABLE IF NOT EXISTS detector_stats(
  detector_id TEXT, detector_version TEXT,
  n_alerts INTEGER DEFAULT 0, n_reproduced INTEGER DEFAULT 0, n_fp INTEGER DEFAULT 0,
  PRIMARY KEY(detector_id, detector_version)
);
"""


def wilson_lower_bound(successes: int, total: int, z: float = 1.96) -> float:
    """成功率 Wilson 置信下界（§3 维度三：不用点估计，防"10 次全成"当 100%）。"""
    if total == 0:
        return 0.0
    p = successes / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / denom)


# §8.7 体检表六项：阈值名 → (取值键, 判定函数)。缺项即 blocked。
_CHECKLIST = {
    "tuning_holdout_gap": ("tuning_holdout_gap", lambda v: v <= 0.15),
    "clean_fp_rate": ("clean_fp_rate", lambda v: v <= 0.05),
    "ece_diff": ("ece_diff", lambda v: v <= 0.05),
    "n_eff_ratio": ("n_eff_ratio", lambda v: v >= 0.5),
    "sig_transfer": ("sig_transferability", lambda v: v >= 0.8),
    "verdict_consistency": ("cross_run_verdict_consistency", lambda v: v >= 0.9),
}


# §8.2 的"独立场景"上下文键：**工程档案 × 机制 × 档位**。
# 全仓库只许有这一份定义——n_effective（本文件）、聚合层质量轴
# （aggregate.meta_metrics）、体检表第 4 项（checklist）三处都要算同一个数，
# 各写一遍 SQL 就会漂移；漂移的表现是"提案说证据够、报告说证据不够"，
# 而看的人不知道该信哪一个（本轮在待批队列判据上已经撞过一次）。
#
# 阶段 7 批 2 把 profile 加进键里：两个工程各修一次同一机制，旧口径会算成
# "1 个独立场景"，而 §8.2 要的是"这个动作在几种不同处境下有效"——跨工程
# 复用同一条经验正是它要数进去的那份独立性。
# ⚠️ 用 COALESCE 而不是裸拼接：SQLite 里 || 遇 NULL 结果是 NULL，历史行没填
# profile 时会全部折叠成同一个 NULL，n_effective 静默归零。
N_EFF_CONTEXT_KEY = "COALESCE(profile,'') || '|' || fixture || '|' || quality_tier"


def context_key_expr(db: sqlite3.Connection) -> str:
    """按当前库**有没有 profile 列**返回可用的 §8.2 上下文键表达式。

    为什么读侧不顺手 ALTER 补列：aggregate / checklist 是**出数**入口，不该在
    统计过程中写特征库（aggregate 里还有一路是 mode=ro 只读连接，ALTER 会直接
    报错）。而"列还没有"这件事本身就说明这批样本全部写于 profile 落地之前，
    按归属判据它们同属内置档案——此时候选键里加不加 profile 结果等价，退回
    旧键既正确又不需要动库。列一旦被 KnowledgeStore 建出来，这里自动切到含
    工程档案的新键。
    """
    cols = {r[1] for r in db.execute("PRAGMA table_info(outcomes)")}
    return N_EFF_CONTEXT_KEY if "profile" in cols else "fixture || '|' || quality_tier"


class KnowledgeStore:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path))
        self.db.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """补 outcomes.profile 列（阶段 7 批 2），并把历史行回填成内置档案。

        两个坑都在这一步：
         · `CREATE TABLE IF NOT EXISTS` 对**已存在**的表不会加列。不 ALTER 的话
           老库一写入就 "no column named profile"，而临时目录新建库永远测不到
           ——只有真实仓库里那份 patterns.db 会炸，正好是最不该炸的地方。
         · 口径是 `COUNT(DISTINCT fixture||'|'||quality_tier||'|'||profile)`，
           而 SQLite 里字符串拼接遇到 NULL 结果就是 NULL。历史行若留 NULL，
           所有老样本会被折叠成同一个 NULL 组，n_effective 掉到接近 0，
           升维提案集体变成"证据不足"。这种失真不报错，只让结论悄悄反转，
           所以必须显式回填，不能靠 DEFAULT 蒙混（DEFAULT 只对 INSERT 生效）。
        回填走 owner_profile_id({})：与检测链、聚合层同一条归属判据，
        不在这里另写一份 "upi-fixture" 字面量（两处规则迟早漂移）。
        """
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(outcomes)")}
        if "profile" not in cols:
            self.db.execute("ALTER TABLE outcomes ADD COLUMN profile TEXT")
        from upi.config.project_profile import owner_profile_id
        self.db.execute("UPDATE outcomes SET profile=? WHERE profile IS NULL",
                        (owner_profile_id({}),))
        self.db.commit()

    def close(self):
        self.db.close()

    # ---------- 写入 ----------

    def upsert_pattern(self, pattern_id: str, symptom_sig: str, link: str,
                       root_cause_sig: str, action_sig: str,
                       playbook_id: str = "", side_effect: str = "unknown") -> None:
        """三重签名相同 = 同一条经验；不同 = 新条目，semi 起步。"""
        now = time.time()
        cur = self.db.execute(
            "SELECT id FROM patterns WHERE symptom_sig=? AND root_cause_sig=? AND action_sig=?",
            (symptom_sig, root_cause_sig, action_sig))
        row = cur.fetchone()
        if row:
            self.db.execute("UPDATE patterns SET updated_at=? WHERE id=?", (now, row[0]))
        else:
            self.db.execute(
                "INSERT INTO patterns(id, symptom_sig, link, root_cause_sig, action_sig,"
                " playbook_id, side_effect, tier, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?, 'semi', ?, ?)",
                (pattern_id, symptom_sig, link, root_cause_sig, action_sig,
                 playbook_id, side_effect, now, now))
        self.db.commit()

    def record_outcome(self, pattern_id: str, run_id: str, fixture: str,
                       quality_tier: str, state: str, *,
                       metric_delta: float | None = None,
                       realized_over_expected: float | None = None,
                       review_rounds: int | None = None,
                       profile: str | None = None,
                       introduced_new_issue_rolled_back: bool = False) -> None:
        """attempt 结果入账。引入新问题被回滚 → 立即降 semi + 复盘标记（硬降级，§1.7）。

        profile 缺省取**调用时**的当前档案，不写成 `= DEFAULT_PROFILE_ID`：
        默认参数在 def 时求值一次，那样切了档案再记账仍会写 upi-fixture
        （同一个坑本轮在配置面已撞过一次）。
        """
        if profile is None:
            from upi.config.project_profile import active_profile
            profile = active_profile().profile_id
        self.db.execute(
            "INSERT INTO outcomes(issue_id, pattern_id, run_id, fixture, quality_tier,"
            " profile, state, metric_delta, guards_json, realized_over_expected,"
            " review_rounds, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (None, pattern_id, run_id, fixture, quality_tier, profile, state,
             metric_delta, None, realized_over_expected, review_rounds, time.time()))
        if introduced_new_issue_rolled_back:
            self.db.execute(
                "UPDATE patterns SET tier='semi', n_new_issue=n_new_issue+1,"
                " needs_review=1, updated_at=? WHERE id=?", (time.time(), pattern_id))
        self.db.commit()

    def bump_detector_stat(self, detector_id: str, detector_version: str,
                           *, alert: bool = False, reproduced: bool = False,
                           false_positive: bool = False) -> None:
        """FP 统计按 detector.id × version 分桶（§1.1）；重采样不复现记一次 FP。"""
        self.db.execute(
            "INSERT OR IGNORE INTO detector_stats(detector_id, detector_version) VALUES(?,?)",
            (detector_id, detector_version))
        if alert:
            self.db.execute("UPDATE detector_stats SET n_alerts=n_alerts+1"
                            " WHERE detector_id=? AND detector_version=?",
                            (detector_id, detector_version))
        if reproduced:
            self.db.execute("UPDATE detector_stats SET n_reproduced=n_reproduced+1"
                            " WHERE detector_id=? AND detector_version=?",
                            (detector_id, detector_version))
        if false_positive:
            self.db.execute("UPDATE detector_stats SET n_fp=n_fp+1"
                            " WHERE detector_id=? AND detector_version=?",
                            (detector_id, detector_version))
        self.db.commit()

    # ---------- 口径 ----------

    def n_effective(self, pattern_id: str) -> int:
        """独立场景 × 档位的去重计数（§8.2）。同一上下文重复运行只算 1。

        上下文键用模块级 N_EFF_CONTEXT_KEY（含工程档案），不在这里重写拼接。
        """
        cur = self.db.execute(
            f"SELECT COUNT(DISTINCT {N_EFF_CONTEXT_KEY}) FROM outcomes"
            " WHERE pattern_id=?", (pattern_id,))
        return int(cur.fetchone()[0])

    def rates(self, pattern_id: str) -> dict:
        """成功率/自动率/needs_human 占比——三者必须成对展示（G15）。"""
        cur = self.db.execute(
            "SELECT state, COUNT(*) FROM outcomes WHERE pattern_id=? GROUP BY state",
            (pattern_id,))
        c = dict(cur.fetchall())
        resolved = c.get("resolved", 0)
        failed = c.get("failed", 0) + c.get("rolled_back", 0)
        needs_human = c.get("needs_human", 0)
        denom = resolved + failed
        ro = self.db.execute(
            "SELECT AVG(realized_over_expected) FROM outcomes"
            " WHERE pattern_id=? AND state='resolved'", (pattern_id,)).fetchone()[0]
        return {"n_success": resolved, "n_failed": failed, "n_needs_human": needs_human,
                "success_rate": (resolved / denom) if denom else None,
                "wilson_lb": wilson_lower_bound(resolved, denom),
                "mean_realized_over_expected": ro,
                "n_contexts_effective": self.n_effective(pattern_id)}

    def fp_rate(self, detector_id: str, detector_version: str) -> dict | None:
        cur = self.db.execute(
            "SELECT n_alerts, n_reproduced, n_fp FROM detector_stats"
            " WHERE detector_id=? AND detector_version=?", (detector_id, detector_version))
        row = cur.fetchone()
        if not row:
            return None
        a, r, fp = row
        return {"n_alerts": a, "n_reproduced": r, "n_fp": fp,
                "fp_rate": (fp / a) if a else None}

    # ---------- 升维提案（只提案，不执行）----------

    def propose_promotion(self, pattern_id: str, requested_tier: str,
                          overfit_checklist: dict | None,
                          n_min: int = 10) -> PatternPromotionProposal:
        """§3 维度三四条件 + §8.7 体检表。任一不满足或缺项 → status='blocked'。"""
        cur = self.db.execute("SELECT n_new_issue, tier FROM patterns WHERE id=?", (pattern_id,))
        row = cur.fetchone()
        if not row:
            raise KeyError(f"unknown pattern {pattern_id}")
        n_new_issue, cur_tier = row
        rates = self.rates(pattern_id)
        n_eff = rates["n_contexts_effective"]
        n_trials = sum(1 for _ in self.db.execute(
            "SELECT 1 FROM outcomes WHERE pattern_id=?", (pattern_id,)))

        checklist_ok: dict[str, bool] = {}
        blocked_reasons: list[str] = []
        if not overfit_checklist:
            blocked_reasons.append("缺 §8.7 过拟合体检表（G14：缺项即 blocked）")
        else:
            for rule_key, (field, pred) in _CHECKLIST.items():
                if field not in overfit_checklist:
                    blocked_reasons.append(f"体检表缺项 {field}")
                    checklist_ok[rule_key] = False
                else:
                    ok = bool(pred(float(overfit_checklist[field])))
                    checklist_ok[rule_key] = ok
                    if not ok:
                        blocked_reasons.append(f"体检表不达标 {field}={overfit_checklist[field]}")

        if n_eff < n_min:
            blocked_reasons.append(f"n_effective={n_eff} < n_min={n_min}（口径 §8.2）")
        if rates["wilson_lb"] < 0.9:
            blocked_reasons.append(f"Wilson 下界 {rates['wilson_lb']:.3f} < 0.9")
        if n_new_issue > 0:
            blocked_reasons.append(f"期间引入新问题 {n_new_issue} 次（须为 0）")
        roe = rates["mean_realized_over_expected"]
        if roe is None or roe < 0.8:
            blocked_reasons.append(f"平均 realized_over_expected={roe} < 0.8")

        return PatternPromotionProposal(
            pattern_id=pattern_id, n_effective=n_eff, n_trials=n_trials,
            success_rate_wilson_lower_bound=rates["wilson_lb"],
            requested_tier=requested_tier,
            overfit_checklist=overfit_checklist or {},
            current_tier=cur_tier,
            status="proposal_ready" if not blocked_reasons else "blocked",
            blocked_reasons=blocked_reasons,
            checklist_ok=checklist_ok,
            note="提案只提交人工批准，本模块不执行任何权限变更（AGENTS §1.7）")
