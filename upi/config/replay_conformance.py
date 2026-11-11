"""回放准入标准（RS-1~4）的静态探测与定档（#31，契约见 docs/回放准入标准.md）。

背景（设计讨论拍板）：探针驱动的分析-修复路线对"稳定复现"有真依赖——发作类
问题要能重播操作才判得了"修没修好"。与其假装通用回放，把标准写成**工程自愿
遵守的准入契约**：遵守到哪一条，就解锁哪一档流程。

诚实边界（本模块的第一设计约束）：
- 这里做的是**静态证据探测**（扫工程里有没有回放协议件、场景能不能脚本化
  打开、有没有种子/定步长/复位标记），**不等于**条款被真机验证过。
  条款级验证（两遍重放位级一致）属 #35 回放基建 T2 的产物，探测结果必须
  带着 live_verified 的差距如实展示，不许把"文件里有 arm: 命令"说成"可复现"；
- 定档方向保守：证据不足就降档。降档只关"自动修复+验证"这条腿，
  检查-分析-处方腿不受影响（三档语义见契约文档）。

档位语义（tier_from 的判据，与 §34 三档流程一一对应）：
  full     RS-1..4 证据齐 ⇒ 检查→分析→信封工单→序贯修复→回放验证一条龙
  partial  RS-1 过但 RS-3（操作指令列表）缺 ⇒ 慢性病（水位类）照修照验；
           发作类只出"免责工单"，批准=知情接受未复现验证
  none     连场景脚本化入口都缺证据 ⇒ 只体检开方，不动刀
⚠️ 缺省 none 是 fail-closed：既有档案（projects/*.json 里没这个字段）装载
   即 none——"没探测过"不等于"探测过且合格"，谎报 full 会让不配合工程被
   当成可自动修复处理。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CLAUSES = ("RS-1", "RS-2", "RS-3", "RS-4")
CLAUSE_LABEL = {
    "RS-1": "场景可脚本化进入（一条命令载入指定场景）",
    "RS-2": "确定性（固定随机种子 + 固定逻辑步长）",
    "RS-3": "操作指令列表可执行（帧号×动作的脚本协议）",
    "RS-4": "状态可复位（同一起点可重复到达）",
}
TIERS = ("none", "partial", "full")

# 静态标记（与 ReplayDriver 实测协议对齐，允许等价写法；全部大小写敏感匹配
# C# 标识符——扫到即证据，扫不到≠不存在（也许换了名字），所以证据只写"未见"
# 不写"没有"，定档方向保守但措辞如实）
_RS2_SEED = re.compile(r"Random\.InitState\s*\(")
_RS2_STEP = re.compile(r"fixedDeltaTime|Time\.maximumDeltaTime|fixedDt")
# RS-3：文件控制协议命令面（arm:<atRel>:<stride>:<count> 或 load:seed:…）
_RS3_CMD = re.compile(r"\"?(arm|load)\s*:|Substring\s*\(\s*0\s*,\s*3\s*\)\s*==\s*\"arm")
# RS-4：装载/复位命令会重钉 Origin 并重新 InitState（复位到同一起点的标记）
_RS4_RESET = re.compile(r"(reset|load)\b.{0,40}origin|origin\s*=|InitState", re.I)


def _scan_cs(root: Path) -> list[Path]:
    """工程内 .cs 文件（限深、跳过库缓存目录——Library/ 是包缓存不是工程承诺）。"""
    if not root.is_dir():
        return []
    skip = {"Library", "Temp", "obj", "Bin", "Logs", "PackageCache"}
    out: list[Path] = []
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            for p in d.iterdir():
                if p.is_dir():
                    if p.name not in skip:
                        stack.append(p)
                elif p.suffix == ".cs":
                    out.append(p)
        except OSError:
            continue
    return out


def _clause_hits(files: list[Path], pattern: re.Pattern) -> list[str]:
    hits = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if pattern.search(text):
            hits.append(f.name)
    return hits


def probe(profile) -> dict:
    """对 ProjectProfile 做静态探测，返回 {"clauses": {...}, "tier": ...,
    "live_verified": bool, "live_ref": str}。

    profile 用到的字段：abs_project / assets_root / fixture_universe() /
    scene_of(subject) / conformance_live_ref。装载期不碰盘的纪律仍守——
    本函数由调用方（注册向导/状态自检）显式触发，不在模型校验器里跑。
    """
    clauses: dict[str, dict] = {}

    # RS-1：场景可脚本化进入——按档案模板能解析出存在的场景文件（至少 clean+1 机制）
    universe = profile.fixture_universe()
    found_scenes = []
    for s in universe:
        try:
            fp = profile.scene_of(s)
        except Exception:  # noqa: BLE001 模板解析不了=不存在
            continue
        if fp.exists():
            found_scenes.append(fp.name)
    clauses["RS-1"] = {
        "pass": len(found_scenes) >= 1,
        "evidence": (f"场景文件可得 {len(found_scenes)}/{len(universe)}："
                     + ", ".join(found_scenes[:4]) if found_scenes
                     else "未见可解析的场景文件"),
    }

    # 条款 RS-2/3/4 的证据都来自工程侧代码：扫档案声明的 assets_root
    cs_files = _scan_cs(profile.abs_project / profile.assets_root)
    seed_hits = _clause_hits(cs_files, _RS2_SEED)
    step_hits = _clause_hits(cs_files, _RS2_STEP)
    clauses["RS-2"] = {
        "pass": bool(seed_hits) and bool(step_hits),
        "evidence": (f"种子标记 {len(seed_hits)} 处 + 定步长标记 {len(step_hits)} 处"
                     if seed_hits and step_hits else
                     f"种子:{'见' if seed_hits else '未见'} 定步长:{'见' if step_hits else '未见'}"
                     "（未见≠没有，可能换了写法；定档保守）"),
    }

    arm_hits = _clause_hits(cs_files, _RS3_CMD)
    clauses["RS-3"] = {
        "pass": bool(arm_hits),
        "evidence": ("回放协议命令面见于 " + ", ".join(arm_hits[:3])
                     if arm_hits else "未见 arm:/load: 指令协议"),
    }

    rst_hits = _clause_hits(cs_files, _RS4_RESET)
    clauses["RS-4"] = {
        "pass": bool(rst_hits) and bool(arm_hits),
        "evidence": ("复位/重钉起点标记见于 " + ", ".join(rst_hits[:3])
                     if (rst_hits and arm_hits) else
                     "未见与协议配套的复位标记"),
    }

    tier = tier_from(clauses)

    # live_verified：条款级验证产物（#35 T2 对齐跑的落盘）存在且合格才算真。
    # 静态探测看到 arm: 命令只证明"有回放基础设施"，不证明"重放位级一致"——
    # 后者要有 final=aligned 的产物。full 档承诺"回放验证"，所以**必须**两者齐：
    # 四条款过但没对齐产物 ⇒ 当场降 partial，把降档做进返回值里（单点真相）。
    # 这样字段 conformance_tier、注册预览、下游三档流程读到的都是同一个已定稿的
    # 档，不会再出现"字段 full、行为 partial"的两份真相（本仓反复堵的打脸形状）。
    live_ok, live_note = _live_check(profile)
    if tier == "full" and not live_ok:
        tier = "partial"
    return {"clauses": clauses, "tier": tier,
            "live_verified": live_ok, "live_note": live_note}


def tier_from(clauses: dict) -> str:
    """定档判据单点（注册/自检/测试都走这里，两处各写迟早漂移）。"""
    if not clauses["RS-1"]["pass"]:
        return "none"
    if not clauses["RS-3"]["pass"]:
        return "partial"
    if all(clauses[c]["pass"] for c in CLAUSES):
        return "full"
    return "partial"   # RS-3 过了但 2/4 缺证据：操作可脚本但确定性没证据，
                      # 回放验证不可信 ⇒ 与无指令列表同档处置（保守降档）


def _live_check(profile) -> tuple[bool, str]:
    """档案声明的验证产物（conformance_live_ref，相对本仓库根）是否存在且合格。

    合格判据（读产物自身，不猜）：final == "aligned"。#35 之后由对齐跑写入；
    夹具可指向历史实跑 runs/m2a_alignment_report.json（final=aligned，位级一致）。
    """
    ref = str(getattr(profile, "conformance_live_ref", "") or "").strip()
    if not ref:
        return False, "未声明回放验证产物（静态定档待 #35 真机验证）"
    from upi.core import events as E  # 延迟导入：与总线同一 ROOT 基准
    p = E.ROOT / ref
    if not p.exists():
        return False, f"验证产物不存在：{ref}"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False, f"验证产物读不出：{ref}"
    if d.get("final") != "aligned":
        return False, f"验证产物结论为 {d.get('final')!r}（非 aligned）：{ref}"
    return True, ref
