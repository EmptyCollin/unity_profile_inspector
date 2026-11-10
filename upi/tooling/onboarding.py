"""接入向导的纯逻辑层（阶段 8 #23）：校验答案、拼档案、给下一步。

为什么与 UI 分开（与 ui_actions 同一理由）：向导最容易出错的地方不是"能不能
读一行输入"，而是**答案到档案字段的映射**与"缺什么必须问"。前者埋在 input()
循环里就只能真开终端手点才能验，而手点恰恰测不到拒绝路径（填了越界路径、
填了不存在的场景、把噪声基准同时填进被测清单）。

三条纪律：
1. **不猜**：能从磁盘查出来的一律查（场景文件、git 根），查不出来就问人，
   绝不"看起来像就填上"。接错工程的表现是"所有数字都正常而结论全错"。
2. **校验复用产品模型**：档案构造走 ProjectProfile 本体（含越界/占位符/
   clean∈subjects 等全部硬校验），不在这里复刻一遍规则——复刻的那份不是产品。
3. **纯校验路径不落盘**：build_profile / floor_apply 等只产出对象与问题清单，
   于是向导逻辑能在测试里整条跑遍而不碰仓库（也不会在 import 期读盘拖垮
   别的入口，同阶段 7 的装载期零读盘）。**唯一的落盘出口**是
   commit_profile / floor_write —— 它们只应由 UI 在人工 token 确认之后调用，
   写成模块级函数是为了让"注册到底改了哪几个文件"可被一处审计。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from upi.config.project_profile import (ROOT, ProjectProfile, ProfileError,
                                        profile_file, set_active_profile_id)
from upi.core.floor_check import FloorTable, FloorTarget, load_floor_table


def detect_git_root(project_path: Path) -> Optional[Path]:
    """工程所属 git 根。查不到 ⇒ None（让调用方去问，不猜成仓库根或工程根）。

    为什么不能默认：档案校验要求 project_path 在 git_root 之下，而合法形态有
    两种（内置=仓库根、外部工程=工程自己当根）。猜错的表现是"档案配好了，
    第一次 attempt 快照就 relative_to 崩"——像模板坏了，其实是根认错了。
    """
    p = Path(str(project_path))
    if not p.exists():
        return None
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(p),
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    top = (r.stdout or "").strip()
    return Path(top) if top else None


def scan_scene_subjects(project_path: Path, scenes_dir_rel: str,
                        scene_template: str) -> list[str]:
    """按模板从**真实场景文件**反推机制名清单（存在性以磁盘为准）。

    模板形如 Scene_{subject}.unity 或 {subject}.unity，反解就是去掉前后缀。
    匹配不上的文件忽略——那通常是工程自带的主菜单/启动场景，不属于被测集。
    """
    d = Path(str(project_path)) / scenes_dir_rel
    if not d.is_dir():
        return []
    pre, _, post = scene_template.partition("{subject}")
    out: list[str] = []
    for f in sorted(d.glob("*.unity")):
        n = f.name
        if len(n) <= len(pre) + len(post):
            continue
        if not (n.startswith(pre) and n.endswith(post)):
            continue
        sub = n[len(pre):len(n) - len(post) or None]
        if sub and sub not in out:
            out.append(sub)
    return out


def _sane_rel(s: str) -> bool:
    """工程内相对片段的形状检查（与 ProjectProfile 同判据，先拦一次给人话）。"""
    q = Path(s)
    return bool(s) and not q.root and not q.drive and ".." not in q.parts


def build_profile(answers: dict) -> tuple[Optional[ProjectProfile], list[str]]:
    """把向导答案拼成档案。返回 (profile|None, 人话问题清单)。

    profile=None 时问题清单**必定非空**——不能既不给档案又不说为什么。
    答案缺字段不当错误而是进问题清单：向导是循环问的，"还缺什么"就是下一步。
    """
    problems: list[str] = []
    pid = str(answers.get("profile_id") or "").strip()
    raw_path = str(answers.get("project_path") or "").strip()
    if not pid:
        problems.append("profile_id 不能为空（它是标定归属与协议指纹标签，不是注释）")
    if not raw_path:
        problems.append("工程绝对路径不能为空")
    p: Optional[Path] = Path(raw_path) if raw_path else None
    if p is not None and not p.is_absolute():
        # Windows 上 "D:foo" 带 drive 却不是绝对路径；这种串拼到哪都错。
        # 判据用 is_absolute（这里是"用户填的路径"，不是档案里的相对片段，
        # 与阶段 7 那条"别用 is_absolute 判越界"的教训不冲突——场景不同）。
        problems.append(f"工程路径必须是绝对路径，收到 {raw_path!r}")
        p = None
    if p is not None and not p.exists():
        problems.append(f"工程目录不存在：{p}")
        p = None

    scenes_dir = str(answers.get("scenes_dir") or "Assets/Scenes").strip()
    template = str(answers.get("scene_template") or "{subject}.unity").strip()
    assets_root = str(answers.get("assets_root") or "Assets").strip()
    for name, val in (("scenes_dir", scenes_dir), ("assets_root", assets_root)):
        if not _sane_rel(val):
            problems.append(f"{name}={val!r} 必须是工程内相对片段（不得含根/盘符或 ..）")
    if "{subject}" not in template or Path(template).parts != (template,):
        problems.append(f"scene_template={template!r} 必须是含 {{subject}} 的单个文件名")

    if problems or p is None:
        return None, problems

    # 场景与 git 根以磁盘为证据，不接受"用户说有什么"
    found = scan_scene_subjects(p, scenes_dir, template)
    subjects = [str(s).strip() for s in (answers.get("subjects") or found)
                if str(s).strip()]
    clean = str(answers.get("clean_subject") or "").strip()
    # ⚠️ 噪声基准必须从被测清单里**摘出去**（真工程实测撞出的注册死路）：
    #   scan 会把所有场景都扫进来，含基准场景本身；而 ProjectProfile 硬校验
    #   "clean ∉ subjects"（§8.1：它不能既当尺子又当对象，否则检出率分母失真）。
    #   于是只要用户填了 clean_subject，向导就 100% 被产品模型拒收——
    #   "带噪声基准的新工程根本注册不了"。摘除后 lab 得到 subjects=[SampleScene]、
    #   clean=Baseline，与手工注册的档案逐字一致。
    if clean:
        subjects = [s for s in subjects if s != clean]
        if not (p / scenes_dir / template.format(subject=clean)).exists():
            problems.append(f"噪声基准 {clean!r} 在工程里找不到对应场景：填存在的场景名，"
                            "或留空（留空=无基准 ⇒ 走 L0 只出绝对线）")
    if not subjects:
        return None, problems or [
            f"{p / scenes_dir} 下按模板 {template!r} 没匹配到任何**被测**场景——"
            "先确认场景目录与命名，或直接给出机制清单（噪声基准不计入被测）"]
    git_root = answers.get("git_root") or detect_git_root(p)
    if not git_root:
        return None, ["该工程不在任何 git 仓库内：快照与回滚只依赖 git（G4/G9）。"
                      "先在工程目录 git init，或显式给出可回滚的 git 根"]

    floor_rel = str(answers.get("floor_targets_rel") or "").strip()
    if floor_rel and not _sane_rel(floor_rel):
        problems.append(f"floor_targets_rel={floor_rel!r} 必须是工程内相对片段")
    # 束目录/阈值目录是**仓库内**相对目录（标定数据留在本仓库，不进被优化工程）
    cal_rel = str(answers.get("cal_dir_rel") or "").strip()
    base_rel = str(answers.get("baselines_dir_rel") or "").strip()
    for name, val in (("cal_dir_rel", cal_rel), ("baselines_dir_rel", base_rel)):
        q = Path(val) if val else None
        if q is not None and (q.is_absolute() or q.root or q.drive or ".." in q.parts):
            problems.append(f"{name}={val!r} 必须是仓库内相对目录（不得绝对或含 ..）")
    if problems:
        return None, problems

    kwargs = dict(
        profile_id=pid, display_name=str(answers.get("display_name") or pid),
        project_path=p, git_root=Path(str(git_root)),
        scenes_dir=scenes_dir, scene_template=template, assets_root=assets_root,
        subject_dir_style=str(answers.get("subject_dir_style") or "hyphen"),
        subjects=subjects, clean_subject=clean,
        quality_tier=str(answers.get("quality_tier") or "urp-balanced"),
        floor_targets_rel=floor_rel,
    )
    if cal_rel:
        kwargs["cal_dir_rel"] = cal_rel
    if base_rel:
        kwargs["baselines_dir_rel"] = base_rel
    # 修复能力（fix_subject/subject_file/defect_signature）默认一律不给：
    # 没有 playbook 模板就没有自动修复，检出后转人工。这里刻意不做"聪明映射"——
    # 把夹具模板悄悄套到陌生机制上，表现是"修得动"，实际改的是不相干代码。
    try:
        return ProjectProfile(**kwargs), []
    except Exception as e:  # noqa: BLE001 — 产品模型的校验错翻译成人话
        return None, [f"档案不合格：{str(e)[:220]}"]


def calibration_next_steps(prof: ProjectProfile) -> list[str]:
    """注册成功后还缺什么才能出可信检出（向导"下一步"就念这个）。

    写成函数而不是固定文案：缺项取决于档案与磁盘实况（有没有本工程阈值、
    有没有噪声基准、下限表文件在不在）。抄清单会出现"提示你去标定其实早标定过"。
    """
    steps: list[str] = []
    bdir = ROOT / prof.baselines_dir_rel
    if list(bdir.glob("thresholds_*.json")):
        steps.append("本工程已有阈值文件：可直接 `upi dev detect` 或等巡检下一轮")
    else:
        steps.append("跑标定：采一轮束并 --merge 生成阈值"
                     "（无标定 ⇒ 相对判定不可信，UI 会停在 uncalibrated，§8.1）")
    if prof.clean_subject:
        steps.append(f"噪声基准 = {prof.clean_subject}：采束时必须同轮在场，"
                     "缺它按采集失败拒判（L2 前提，不降级）")
    else:
        steps.append("未配噪声基准 ⇒ L0：只出绝对下限与发作线，"
                     "误报率 not_measurable（不许留空冒充 0）")
    ftp = prof.floor_targets_path
    if ftp is None:
        steps.append("未配下限目标表 ⇒ 绝对下限腿不启用（[f] 可编辑）")
    elif not ftp.exists():
        steps.append(f"下限表路径已配但文件缺失：{ftp}"
                     "（该腿不评，不会静默当成达标）")
    if not prof.fix_subject:
        steps.append("未指定可修复机制 ⇒ 检出后一律转人工（B3），无自动修复闭环")
    return steps


def commit_profile(prof: ProjectProfile, *, activate: bool = True) -> Path:
    """注册落盘：写 projects/<id>.json，并按需把激活指针指向它。

    本模块其余函数都不写盘（见模块头第 3 条纪律），**只有这个函数写**，
    而且只由 UI 在人工 token 确认之后调用——"确认即落盘"必须是一个点，
    否则中途崩会出现"档案写了半套 / 指针没指"的裂状态。
    先写档案再写指针（顺序反了会出现"指针指向还不存在的 id"，
    下个进程按 fail-closed 直接起不来）。
    落盘走总线 atomic_write（唯一临时名 + WinError 5 退避重试），与其余
    关键文件同一套写法，不在这里再造一份固定 .tmp 模式。
    """
    from upi.core import events as E
    fp = profile_file(prof.profile_id)
    E.atomic_write(fp, json.dumps(prof.model_dump(mode="json"),
                                  ensure_ascii=False, indent=1))
    if activate:
        set_active_profile_id(prof.profile_id)
    return fp


def floor_view(prof: ProjectProfile) -> tuple[Optional[FloorTable], list[str]]:
    """下限表现状：(表|None, 说明行)。不静默——路径配了而文件缺要说明。"""
    ftp = prof.floor_targets_path
    if ftp is None:
        return None, [f"工程 {prof.profile_id} 未启用下限表"
                      "（档案字段 floor_targets_rel 为空 ⇒ 绝对下限腿不评）"]
    if not ftp.exists():
        return None, [f"下限表路径已配但文件不存在：{ftp}"
                      "（该腿不评；新建可走本向导）"]
    return load_floor_table(ftp), [f"表文件：{ftp}"]


def floor_render(tbl: Optional[FloorTable]) -> str:
    if tbl is None:
        return "（无表）"
    lines = [f"出处: {tbl.source}", f"目标线条数: {len(tbl.targets)}"]
    for i, t in enumerate(tbl.targets, 1):
        who = t.subject or "全部机制"
        lines.append(f"  {i}. {t.metric} {t.op} {t.value}{t.unit}  适用={who}"
                     + (f"  注={t.note}" if t.note else ""))
    return "\n".join(lines)


def floor_apply(prof: ProjectProfile, tbl: Optional[FloorTable],
                action: str, fields: dict) -> tuple[Optional[FloorTable], str]:
    """在现有表上做一次编辑并**用产品模型校验**，返回 (新表|None, 人话结果)。

    校验走 FloorTarget/FloorTable 本体（op 枚举、value 非负、source 必填），
    不在 UI 侧复刻规则。本函数不落盘——UI 拿到新表后再问一次"确认写入？"，
    写坏表的表现是所有下限判据静默消失，必须留一次反悔机会。

    ⚠️ 无表的工程（tbl=None）第一次加条目必须**同时给出处**：旧写法
      `tbl or FloorTable(source="", ...)` 会当场在构造期被"source 必填"拒收，
      于是"给新工程配第一条下限"这条最该走得通的路径反而报"校验未通过"——
      措辞还像在骂用户填的值（实测推演抓出，不是假想）。
    """
    acts = (action or "").strip().lower()
    if tbl is None and acts != "add":
        return None, "该工程还没有下限表（无可改条目），先加第一条"
    cur = tbl
    try:
        if acts == "source":
            src = str(fields.get("source") or "").strip()
            if not src:
                return None, "出处不能为空（§8.1：无出处的绝对线=手调，表整体拒收）"
            return cur.model_copy(update={"source": src}), "出处已改（未写入）"
        if acts == "add":
            t = FloorTarget(
                metric=str(fields.get("metric") or "").strip(),
                op=str(fields.get("op") or "").strip(),
                value=float(fields.get("value")),
                unit=str(fields.get("unit") or ""),
                subject=(str(fields["subject"]).strip()
                         if fields.get("subject") else None),
                note=str(fields.get("note") or ""))
            if cur is None:
                src = str(fields.get("source") or "").strip()
                if not src:
                    return None, ("新建下限表必须带出处（§8.1：无出处的绝对线=手调）；"
                                  "请在下一问里填 source")
                cur = FloorTable(source=src, targets=[])
            return cur.model_copy(update={"targets": list(cur.targets) + [t]}), \
                f"已加一条：{t.metric} {t.op} {t.value}（未写入）"
        if acts in ("del", "rm"):
            n = int(fields.get("index") or 0)
            if not (1 <= n <= len(cur.targets)):
                return None, f"序号需在 1-{len(cur.targets)}，收到 {fields.get('index')!r}"
            keep = [t for i, t in enumerate(cur.targets, 1) if i != n]
            return cur.model_copy(update={"targets": keep}), f"已删第 {n} 条（未写入）"
        return None, f"未知操作 {action!r}（可：source / add / del）"
    except Exception as e:  # noqa: BLE001 — 校验错翻译成人话，不抛给界面
        return None, f"校验未通过：{str(e)[:180]}"


def floor_write(prof: ProjectProfile, tbl: FloorTable) -> Path:
    """写表到档案声明的路径。路径没配 ⇒ 报错（不猜位置，猜出来的表没人读）。

    出处不在这里再判一次：FloorTable 构造期已强制 source 非空（§8.1 无出处=
    手调拒收），能传进来的表必然有出处——再写一遍守卫就是永远不会命中的
    死代码（本仓反复堵的那类"看起来有保护其实不执行"）。落盘走总线 atomic_write。
    """
    from upi.core import events as E
    ftp = prof.floor_targets_path
    if ftp is None:
        raise ProfileError(
            f"工程 {prof.profile_id} 未配 floor_targets_rel，无法写下限表；"
            "请在档案里给出工程内相对路径（例如 upi_floor.json）")
    E.atomic_write(ftp, json.dumps(tbl.model_dump(mode="json"),
                                   ensure_ascii=False, indent=2))
    return ftp
