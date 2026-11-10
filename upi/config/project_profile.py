"""ProjectProfile：把"和哪个 Unity 工程协作"从代码里的硬编码变成一份可注入的档案。

阶段 7 的第一块。为什么要有它：工程路径、场景目录、场景命名模板、夹具资产根、
缺陷态参照、质量档这些事实此前散在 8+ 个文件里各写一遍（nodes / fixer / verify /
candidate_retrieval / orchestrator / __main__ / m1_multiwin / m1_detect），
换工程要改遍全仓，且任何一处漏改的表现都是"看着跑通了，其实测的是夹具"。

设计取舍（都写清楚，避免后来者以为是疏忽）：

1. 内置 profile 定义在**代码里**而不是 projects/upi-fixture.json。
   理由：克隆仓库就要能跑，不能因为少一个 json 就起不来；而 projects/<id>.json
   是给"接入陌生工程"用的，存在则覆盖内置。两者结构同形，便于对照。

2. fail-closed：档案 id 未知、或档案文件损坏 ⇒ 抛 ProfileError，
   **不回落内置**。这一条与 load_runtime_conf 的 fail-closed 同源——
   回落到 upi-fixture 意味着"你以为在测自己的工程，实际测的是夹具"，
   而所有数字看起来都正常。错档案必须当场报错，不能静默跑。

3. 只声明**当下真被消费**的字段。权限策略、标定引用、探针路径、感知层白名单
   等留到后续批次随接入点一起加（AGENTS 全局教训：写了字段没有消费者
   等于没写，而且会诱导别人以为已接线）。

4. subject（原 fixture）语义：机制编号，不是场景文件名。场景文件名由
   scene_template 拼出来——这样"机制级标识"与"工程里的文件命名约定"分开，
   接新工程只改模板，不动判据层（§8.3 签名禁止具体标识符的前提）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]

#: 未显式指定时的档案 id。内置档案与之一一对应。
DEFAULT_PROFILE_ID = "upi-fixture"
#: 环境变量名：让 daemon / CLI / 测试在同一进程里指定工程，不改代码。
PROFILE_ENV = "UPI_PROFILE"


class ProfileError(RuntimeError):
    """档案缺失/损坏/未知。刻意不做任何回落（见模块 docstring 第 2 条）。"""


class ProjectProfile(BaseModel):
    """一个工程的上下文。project_path 之外一律是**相对工程根**的片段。"""

    profile_id: str
    display_name: str = ""
    project_path: Path                      # Unity 工程根（含 Assets/ ProjectSettings/）

    # 场景与资产布局（工程侧命名约定，判据层不感知）
    scenes_dir: str = "Assets/UpiFixtures/Scenes"
    scene_template: str = "Scene_{subject}.unity"
    assets_root: str = "Assets/UpiFixtures"  # 候选代码扫描根
    subject_dir_style: str = "underscore"    # F-CPU-01 → F_CPU_01 目录名映射方式

    # 测哪些机制、谁是同期对照（原散在 llm_config/fixtures 清单里）
    subjects: list[str] = Field(default_factory=list)
    clean_subject: str = "CLEAN"

    # 修复模板锚定的主目标文件。⚠️ 语义是**相对 git_root**（不是相对工程根、
    # 也不是相对本仓库根）——因为 diff 的 file 字段、touched_assets、
    # apply_diffs(root)/snapshot(root) 全都按 git 相对路径解释，
    # 一处换基准就会让"改的文件"与"申报影响的文件"对不上。
    # 内置档案：git_root=仓库根 ⇒ "fixtures/upi-fixture/Assets/..." 与旧字面量逐字一致。
    # 外部工程自己当 git 根 ⇒ 直接写工程内相对路径（如 "Runtime/F-CPU-99/A.cs"）。
    subject_file: str = ""
    # 缺陷态参照目录：相对**本仓库根**（参照是管线自带资产，不在被优化的工程里）。
    # 允许写绝对路径，给"参照放在仓库外"的部署留口子。
    defect_refs_dir: str = "upi/agents/_refs"

    # 夹具规格（tuning/holdout 切分的唯一依据，§8.1）。相对工程根。
    # 为什么进 profile 而不是留在 checklist.py 里写死：留出集定义是**工程
    # 侧的数据**，接新工程时它必然在别的位置；写死会让体检表读不到 spec、
    # 把三项判据报成"未计算"，看起来像过拟合检查通过了其实没查。
    fixture_spec_rel: str = "Assets/UpiFixtures/Runtime/fixture_spec.json"

    # 本 profile 的**修复能力覆盖哪一个机制**。当前每个 profile 只带一套
    # playbook 模板（nodes.PLAYBOOK_DIFFS 是手工按机制写的，不是生成的），
    # 所以必须有个字段说明"这套模板是给谁用的"。与 detector 注册表的
    # auto_fix.fixtures 口径一致：其余机制检出后进工单队列转人工，不是缺陷。
    fix_subject: str = ""
    # 缺陷态参照里必须出现的特征串。校验它不是为了"文件对不对"，而是为了
    # 挡住一类静默假成功：若参照其实是修复态内容，diff 的 find 段会与文件
    # 现存内容逐字相同 ⇒ apply 报成功但原地空写（比报错危险得多）。
    # 换机制/换工程时这个串必然不同，所以它是 profile 数据而非代码常量。
    defect_signature: str = ""

    quality_tier: str = "urp-balanced"

    # 快照/回滚所依赖的 git 仓库根。⚠️ 这不是可选装饰，而是硬边界：
    # snapshot.workdir_state() 用 project_path.relative_to(git_root) 算脏区，
    # 回滚也只在 git_root 里打 tag。所以"与任意 Unity 工程协作"的真实条件是
    # ① 工程在本仓库工作树内（git_root=仓库根，内置档案即此形态），或
    # ② 工程自己是 git 仓库（git_root=工程根）。
    # 把这条写进 profile 而不是埋在函数里，是因为接新工程的人必须在配置阶段
    # 就看见它——否则表现为"档案配好了，一落地就 relative_to 报错"。
    git_root: Path = Field(default_factory=lambda: ROOT)

    @model_validator(mode="after")
    def _paths_must_be_usable(self):
        # 只校验"形状"，不校验"存在"：CI 里档案可以指向还没 checkout 的工程，
        # 真要用时由调用方（status 自检 / 采样前体检）报缺，导入期不炸盘。
        p = Path(str(self.project_path))
        if not p.is_absolute():
            raise ValueError(f"profile {self.profile_id}: project_path 必须是绝对路径，"
                             f"收到 {p}")
        # ⚠️ 判"是不是绝对路径"不能用 is_absolute()：实测 Windows 上
        #   Path("/etc").is_absolute() == False（缺盘符），但它一拼到工程根上
        #   就跳出工程去了盘符当前位置——用形状判断会漏，靠派生结果兜不住。
        #   root/drive 是纯词法判据（不读盘、跨平台一致）：正常相对片段两者皆空。
        for name in (self.scenes_dir, self.assets_root):
            q = Path(name)
            if q.root or q.drive or ".." in q.parts:
                raise ValueError(
                    f"profile {self.profile_id}: {name!r} 必须是工程内相对片段"
                    "（不得带根/盘符或 ..，防越界读写工程外文件）")
        if not self.scene_template or "{subject}" not in self.scene_template:
            raise ValueError(f"profile {self.profile_id}: scene_template 必须含 "
                             "{{subject}} 占位符，收到 {self.scene_template!r}")
        # 模板只允许是**文件名**：带分隔符或 .. 的模板会让 scene_of 跳出
        # scenes_dir（例如 "{subject}/../../evil"），校验形状时必须一并堵掉
        tq = Path(self.scene_template)
        if tq.root or tq.drive or len(tq.parts) != 1 or ".." in tq.parts:
            raise ValueError(
                f"profile {self.profile_id}: scene_template 必须是单个文件名，"
                f"收到 {self.scene_template!r}")
        if self.clean_subject and self.clean_subject in self.subjects:
            # CLEAN 是"同期对照"，不该同时出现在被测清单里：
            # 那样它会既当尺子又当对象，检出率分母失真（§8.1）
            raise ValueError(f"profile {self.profile_id}: clean_subject={self.clean_subject} "
                             "不能同时出现在 subjects 里")
        # 工程必须落在快照根工作树内，否则 snapshot.workdir_state() 的
        # relative_to 会当场抛 ValueError（不是"稍后失败"，是第一次 attempt 就失败）。
        # 在这里校验，报错才说得出"是档案配错了"，而不是一个看不懂的相对路径异常。
        try:
            p.relative_to(Path(str(self.git_root)))
        except ValueError:
            raise ValueError(
                f"profile {self.profile_id}: project_path={p} 不在 git_root="
                f"{self.git_root} 之下，无法建立 attempt 快照（G4/I4 的回滚只依赖 git）。"
                "要么把工程放进该仓库工作树，要么把 git_root 指向工程自己的 git 根") from None
        return self

    # ── 派生路径（全部现算，不缓存：与总线 I9 同纪律）──
    @property
    def abs_project(self) -> Path:
        return Path(str(self.project_path))

    def scene_of(self, subject: str) -> Path:
        """机制编号 → 场景文件绝对路径。找不到模板变量就如实报错，不猜。"""
        return self.abs_project / self.scenes_dir / self.scene_template.format(
            subject=subject)

    def scene_rel(self, subject: str) -> str:
        """机制编号 → **工程内相对路径**（posix 分隔）。

        为什么要两种形态而不是只留绝对路径：`unity command "scene open"`
        这类 CLI 参数一直传的是工程内相对路径（verify/m1_multiwin 实测用法），
        而 snapshot 的脏区判定要绝对路径。两种都由同一模板派生，
        就不存在"改了模板忘改另一处"——此前这两处各写一遍字面量。
        """
        return f"{self.scenes_dir}/{self.scene_template.format(subject=subject)}"

    @property
    def fixture_spec(self) -> Path:
        """夹具规格文件绝对路径（tuning/holdout 切分的唯一依据，§8.1）。"""
        return self.abs_project / self.fixture_spec_rel

    def subject_asset_dir(self, subject: str) -> Path:
        """机制编号 → 该机制的代码目录。CLEAN 无目录是合法的（无候选）。"""
        name = subject.replace("-", "_") if self.subject_dir_style == "underscore" \
            else subject
        return self.abs_project / self.assets_root / name

    @property
    def subject_file_abs(self) -> Optional[Path]:
        """主修复目标文件的绝对路径（= git_root / subject_file）。

        未配置 ⇒ None（调用方据此转 needs_human）。基准是 git_root 而不是 ROOT：
        否则"工程自己当 git 根"的外部形态会把文件拼到本仓库里，
        表现为 apply 报"命中 0 次"——像模板坏了，其实是基准选错。
        """
        if not self.subject_file:
            return None
        q = Path(self.subject_file)
        return q if q.is_absolute() else Path(str(self.git_root)) / q

    def defect_ref(self, subject: str) -> Path:
        """机制编号 → 钉定的缺陷态参照文件（diff 模板 find 段的来源）。

        命名约定与 upi/agents/_refs 现状一致：F-CPU-01 → F_CPU_01_defect.cs。
        ⚠️ 本方法只拼路径、不读盘：档案装载期不碰工作区（阶段 7 计划要求），
        读盘与内容校验由消费方（nodes 的模板构造）按需做，失败只影响
        "出修复方案"这一条链，不拖垮 detect/patrol 等无关入口。
        """
        base = Path(self.defect_refs_dir)
        if not base.is_absolute():
            base = ROOT / base
        return base / f"{subject.replace('-', '_')}_defect.cs"

    def fixture_universe(self) -> list[str]:
        """本轮要采的机制清单：对照在前，其后是被测机制。

        为什么做成方法而不是各调用点各拼一遍（clean + subjects）：守护缺省
        清单、可移植性测试、界面呈现都可能读它；两处拼法不同就会出现
        "界面显示 5 个、守护实际采 4 个"——而且它不会被任何测试发现，
        因为少采只是安静地没数据，看起来像"这轮没问题"。
        clean_subject 为空 ⇒ 不占位（无同期对照的工程，§8.1 的检出判据
        本就不成立，由标定覆盖检查兜住，不在这里假装清单完整）。
        """
        return ([self.clean_subject] if self.clean_subject else []) + list(self.subjects)

    def fingerprint_id(self) -> str:
        """进协议指纹的稳定标识。用 id 而不是目录名：同目录改名或两份工程
        同名目录时，指纹必须仍然区分得开。"""
        return self.profile_id


def owner_profile_id(record: dict) -> str:
    """一份产物（bundle / 标定文件 / state.json）属于哪个工程档案。

    没有 profile 键 ⇒ 归内置档案。**这条判断全仓库只许写在这一处**：
    检测链、聚合层、体检表都要问"这两份数据是不是同一个工程采的"，
    各处自己写一遍 `rec.get("profile") or "upi-fixture"` 就会漂移
    ——漂移的表现是"检测说可比、报告说跨工程"，两份输出互相打脸，
    而排查的人不知道该信哪一个（本轮在配置面/待批队列已各撞过一次）。

    ⚠️ "缺键 ⇒ 内置"是有事实依据的归属判定，不是猜默认值（I1）：
      profile_id 是阶段 7 批 2 才并入指纹的，在此之前仓库里所有产物
      都只可能来自内置夹具工程（接入外部工程的能力是本次批 2 才开始有的）。
      反过来，一旦数据里显式写了 profile 键，就必须以它为准——
      这时缺省回落到内置会把"别人工程的标定"读成"夹具的标定"。
    """
    if not isinstance(record, dict):
        return DEFAULT_PROFILE_ID
    v = record.get("profile_id") or record.get("profile")
    if isinstance(v, dict):                 # 兼容 protocol 块里嵌套写法
        v = v.get("id")
    s = str(v or "").strip()
    return s or DEFAULT_PROFILE_ID


#: 内置档案：值与阶段 7 之前散在各处的硬编码**逐字一致**，
#: 所以接入后既有回归不会变（这是"重构不改行为"的验收线）。
_BUILTIN: dict[str, ProjectProfile] = {
    DEFAULT_PROFILE_ID: ProjectProfile(
        profile_id=DEFAULT_PROFILE_ID,
        display_name="内置夹具工程（UPI demo）",
        project_path=ROOT / "fixtures" / "upi-fixture",
        scenes_dir="Assets/UpiFixtures/Scenes",
        scene_template="Scene_{subject}.unity",
        assets_root="Assets/UpiFixtures",
        subject_dir_style="underscore",
        subjects=["F-CPU-01", "F-GPU-01", "F-ANIM-01", "F-LOAD-01"],
        clean_subject="CLEAN",
        subject_file="fixtures/upi-fixture/Assets/UpiFixtures/F_CPU_01/CombatSkillManager.cs",
        defect_refs_dir="upi/agents/_refs",
        fix_subject="F-CPU-01",
        defect_signature="var ready = new List<SkillRuntime>();",
        quality_tier="urp-balanced",
    )
}


def builtin_ids() -> list[str]:
    return sorted(_BUILTIN)


def profile_file(pid: str) -> Path:
    """自定义档案的落点：projects/<id>.json（ROOT/projects，与总线 PROJECTS 同源）。"""
    return ROOT / "projects" / f"{pid}.json"


def load_profile(pid: Optional[str] = None) -> ProjectProfile:
    """按 id 取档案。优先级：显式参数 > 环境变量 UPI_PROFILE > 内置默认。

    ⚠️ 磁盘档案存在但读不出来 ⇒ 抛 ProfileError，绝不退回内置档案：
      静默回落的表现是"你以为在跑自己的工程，实际跑的是夹具"，
      所有数字都正常、结论全是错的（危害远大于报错）。
    """
    pid = pid or os.environ.get(PROFILE_ENV) or DEFAULT_PROFILE_ID
    if pid in _BUILTIN:
        return _BUILTIN[pid]
    fp = profile_file(pid)
    if not fp.exists():
        known = ", ".join(builtin_ids())
        raise ProfileError(
            f"未知工程档案 {pid!r}：既不是内置档案（{known}），也没有 {fp}。"
            f"接入新工程请建 {fp}（形状参照内置 upi-fixture）。")
    try:
        raw = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ProfileError(f"工程档案 {fp} 读不出来（{type(e).__name__}: "
                           f"{str(e)[:120]}）——已拒绝，不回落内置档案") from e
    raw.setdefault("profile_id", pid)
    try:
        prof = ProjectProfile(**raw)
    except Exception as e:  # noqa: BLE001 — pydantic 校验失败要转成同一种错型
        raise ProfileError(f"工程档案 {fp} 不合格：{str(e)[:200]}") from e
    if prof.profile_id != pid:
        raise ProfileError(f"工程档案 {fp} 的 profile_id={prof.profile_id!r} "
                           f"与请求的 {pid!r} 不一致（防张冠李戴）")
    return prof


_active: Optional[ProjectProfile] = None


def active_profile() -> ProjectProfile:
    """当前进程使用的档案（惰性解析一次）。

    为什么允许"进程级缓存"而不违反 I9：I9 管的是**运行态与控制信号**
    （必须外部可观测、跨进程一致）；档案是启动时决定的只读配置，一个进程
    服务一个工程，中途换档案会让已建立的快照/基线/签名全部失去意义。
    换工程的方式是重启进程（或由 register 动作引导重启），不是热切换。
    """
    global _active
    if _active is None:
        _active = load_profile()
    return _active


def use_profile(pid: Optional[str]) -> ProjectProfile:
    """显式切换当前档案（测试与 dev 通道用）。解析失败照样抛，不留旧值。"""
    global _active
    p = load_profile(pid)
    _active = p
    return p


def register_profile(prof: ProjectProfile, *, activate: bool = True) -> ProjectProfile:
    """登记一个**内存构造**的档案（不经 projects/*.json）。

    用途有两类，都必须是正当的：
      ① 测试把扫描根指向缺陷态参照副本——候选检索原来靠覆盖模块常量
        FIXTURE_ASSETS 注入，profile 化之后那个常量没了；正确的注入方式就是
        登记一个 project_path 指向临时目录的档案（阶段 7 的
        test_profile_portability 走的也是这条路，测试与产品共用同一入口，
        不会出现"测试能注入、生产注入不了"的假通过）。
      ② dev 通道在档案文件落地前先导一版试跑。

    ⚠️ activate=False 只登记不切换，配合 snapshot_profile()/restore_profile()
      成对使用，避免用例之间串工程。
    """
    global _active
    _BUILTIN[prof.profile_id] = prof
    if activate:
        _active = prof
    return prof


def snapshot_profile() -> Optional[ProjectProfile]:
    """取当前档案引用（None=还没解析过），供测试收尾还原。"""
    return _active


def restore_profile(prof: Optional[ProjectProfile]) -> None:
    """还原到 snapshot_profile() 取回的值；None ⇒ 重置成惰性解析。"""
    global _active
    _active = prof


def unregister_profile(pid: str) -> None:
    """移除登记的档案（测试收尾必调，否则同名 id 会残留到下一条用例）。"""
    global _active
    _BUILTIN.pop(pid, None)
    if _active is not None and _active.profile_id == pid:
        _active = None


def reset_profile() -> None:
    """清缓存（测试收尾必调，否则上一条用例的档案会串到下一条）。"""
    global _active
    _active = None
