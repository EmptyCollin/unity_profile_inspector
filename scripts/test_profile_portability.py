"""阶段 7 完成判据：用第二个 profile 走全链，**不改代码**即可切换工程。

这份测试要证明的不是"profile 模块能 import"，而是三件容易假装通过的事：

 ① 切换真的生效：每个消费点取到的路径必须跟着档案变。
    做法是抓"传给下游的实参"（snapshot/fixer.execute/resample 收到哪个
    project_path/scene_path），而不是只读 profile 对象自己——
    对象改了但调用点还留着旧字面量，是最典型的假接线。

 ② 装载期零读盘：工程目录不存在、缺陷参照缺失时，import 与 detect
    这条链必须照常工作。旧实现在模块导入期 assert 参照内容，一枚参照
    坏掉会连带 patrol / daemon / CLI 全部起不来。

 ③ fail-closed：未知档案 id 不回落内置。回落到 upi-fixture 的表现是
    "你以为在测自己的工程，实际测的是夹具"，数字全都正常而结论全错。

同时钉住一条验收线：内置档案的派生值与阶段 7 之前的硬编码**逐字一致**
（"重构不改行为"），以及一条诚实边界：profile_id 尚未进入协议指纹
（批 2），当前跨工程对比的保护只来自 scene_guid（场景文件内容哈希）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import upi.agents.nodes as N                                  # noqa: E402
from upi.core import events as E                                # noqa: E402
from upi.config import project_profile as PP                  # noqa: E402
from upi.config.project_profile import ProjectProfile         # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'ok' if cond else 'FAIL'}] {name}"
          + ("" if cond else f"  ← {str(detail)[:140]}"))


TMP = Path(tempfile.mkdtemp(prefix="upi_prof_"))
# 真实事件流的初始大小：收尾断言它没被写过。本套件会触发
# profile_fixtures_mismatch / config_read_failed 之类留痕，不隔离就污染仓库
# 留档，而污染**不会让测试变红**（本轮已第五次撞同类，故写成显式自检）。
_REAL_EVENTS = E._p_events()
_real_events_size0 = _REAL_EVENTS.stat().st_size if _REAL_EVENTS.exists() else 0
# 工程必须落在 git_root 之下（snapshot 的 relative_to 硬约束）。
# 这里把 git_root 指成临时目录自己，等价于"工程自己是 git 仓库"的形态。
ALT = TMP / "alt-project"
PID = "port-probe"


def build_alt_project(with_defect: bool = True) -> ProjectProfile:
    """造一个形状与夹具一致、但路径完全不同的第二工程。"""
    (ALT / "Scenes").mkdir(parents=True, exist_ok=True)
    # 机制编号沿用注册表里的 F-CPU-01：本测试要证的是"**同一 detector、
    # 不同工程布局**也能跑通"。自造一个新编号等于绕开了真正要验的
    # "编号→目录/场景"映射，档案换个风格就测不到了。
    code_dir = ALT / "Runtime" / "F-CPU-01"
    code_dir.mkdir(parents=True, exist_ok=True)
    defect = (ROOT / "upi" / "agents" / "_refs" / "F_CPU_01_defect.cs").read_text(
        encoding="utf-8")
    (code_dir / "SkillRunner.cs").write_text(defect if with_defect else "class X{}\n",
                                             encoding="utf-8")
    (ALT / "Scenes" / "Scene-F-CPU-01.unity").write_text("%YAML 1.1\n",
                                                          encoding="utf-8")
    return ProjectProfile(
        profile_id=PID, display_name="第二工程（可移植性探针）",
        project_path=ALT, git_root=ALT,
        scenes_dir="Scenes", scene_template="Scene-{subject}.unity",   # 故意换命名风格
        assets_root="Runtime", subject_dir_style="hyphen",             # 目录名不转下划线
        subjects=["F-CPU-01"], clean_subject="CLEAN-99",
        # subject_file 相对 **git_root**（本例 git_root=ALT），所以写工程内片段；
        # 写成 "alt-project/Runtime/..." 会多拼一层，apply 报"命中 0 次"
        # ——看着像模板坏了，其实是基准选错。
        subject_file="Runtime/F-CPU-01/SkillRunner.cs",
        fix_subject="F-CPU-01",
        defect_signature="var ready = new List<SkillRuntime>();",
        quality_tier="built-in-medium")


def captured_nodes(run_dir: Path):
    """跑一遍 fix 节点，抓它传给 snapshot / fixer.execute 的实参。"""
    seen = {}

    class FakeSnap:
        """替身快照：节点会读 .git_ref 与 .to_dict()，两个都得有。
        （上一版写成 dict 子类，只有 to_dict 没有 git_ref 属性，
        fix 节点取 snap.git_ref 时 AttributeError——测试替身没还原真实形状，
        测的是想象中的契约。）"""
        git_ref = "upi/attempt-probe"

        def to_dict(self):
            return {"git_ref": self.git_ref}

    def fake_snapshot(tag, proj, scene, fp, root=None, metadata=None):
        seen["snapshot"] = (str(proj), str(scene), fp)
        seen["snapshot_root"] = str(root) if root is not None else None
        return FakeSnap()

    def fake_execute(plan, snap, *, project_path=None, scene_path=None,
                     root=None, human_approved=False, approval_note=""):
        seen["execute"] = (str(project_path), str(scene_path))
        return {"status": "applied", "playbook_id": plan.playbook_id,
                "tier": plan.permission_tier}

    run_dir.mkdir(parents=True, exist_ok=True)   # 不建目录 write_text 直接 FileNotFoundError
    orig_snap, orig_rb = N.snapmod.snapshot, N.snapmod.rollback
    orig_fix, orig_rs, orig_gc = N.fixer.execute, N.resample, N.gc_floor_measure
    N.snapmod.snapshot = fake_snapshot
    N.snapmod.rollback = lambda *a, **k: {"ok": True}
    N.fixer.execute = fake_execute
    N.resample = lambda fx, tag, **k: {"_path": str(run_dir / "束.json")}
    (run_dir / "束.json").write_text('{"fixtures": {}}', encoding="utf-8")
    N.gc_floor_measure = lambda fx, **k: {}
    try:
        nodes = N.make_nodes(run_dir, live_llm=False,
                             approval_note="探针批准（测试替身边界）")
        st = {"issue": {"fixture": "F-CPU-01", "detector": "DET-CPU-GC-FRAMEALLOC",
                        "metric": "gc_alloc_per_frame", "magnitude": 1.0,
                        "evidence": {}, "state": "confirmed", "anchor": ""},
              "hypotheses": [{"root_cause": "热路径每帧新建 List（可复用缓冲）",
                              "confidence": 0.9, "code_anchors": []}],
              "edit_plan": None, "records": [], "attempts": 0,
              "budget": {"max_review_rounds": 3, "max_attempts_per_issue": 3,
                         "wallclock_s": 3600, "max_tokens": 60000},
              "quality_tier": "built-in-medium", "spent": {"wallclock_s": 0, "tokens": 0}}
        ep = nodes["plan"](st)
        seen["plan"] = ep
        nodes["fix"]({**st, **ep})
    finally:
        N.snapmod.snapshot, N.snapmod.rollback = orig_snap, orig_rb
        N.fixer.execute, N.resample, N.gc_floor_measure = orig_fix, orig_rs, orig_gc
    return seen


def main() -> int:
    E.isolate(TMP / "runs")     # 留痕/命令/状态/配置全部指向临时区
    print("== ⑦ 可移植性：不改代码切换到第二个工程 ==")

    # ── ⓪ 验收线：内置档案派生值必须与阶段 7 前的硬编码逐字一致 ──
    b = PP.load_profile(PP.DEFAULT_PROFILE_ID)
    ok("内置档案 = 旧硬编码（重构不改行为）",
       str(b.abs_project) == str(ROOT / "fixtures" / "upi-fixture")
       and str(b.scene_of("F-CPU-01")).replace("\\", "/").endswith(
           "Assets/UpiFixtures/Scenes/Scene_F-CPU-01.unity")
       and b.subject_file == ("fixtures/upi-fixture/Assets/UpiFixtures"
                              "/F_CPU_01/CombatSkillManager.cs")
       and b.subject_asset_dir("F-CPU-01").name == "F_CPU_01"
       and [b.clean_subject] + b.subjects == ["CLEAN", "F-CPU-01", "F-GPU-01",
                                              "F-ANIM-01", "F-LOAD-01"]
       and b.quality_tier == "urp-balanced",
       f"{b.subjects} / {b.subject_dir_style}")

    # ── ① 装载期零读盘：工程不在、参照缺失也不能拖垮导入与 detect ──
    missing = ProjectProfile(profile_id="ghost", project_path=TMP / "nope",
                             git_root=TMP / "nope", subjects=["F-X-01"],
                             fix_subject="F-X-01", assets_root="Runtime",
                             scene_template="Scene-{subject}.unity",
                             subject_file="Runtime/F-X-01/A.cs",
                             defect_signature="不该存在的特征串")
    PP.register_profile(missing, activate=False)
    ok("档案指向不存在的工程 ⇒ 构造期不炸（校验形状不校验存在）", True)
    PP.use_profile("ghost")
    try:
        N._playbook_map()
        ok("缺陷参照缺失 ⇒ 拒绝出方案而不是静默空写", False, "竟然返回了模板")
    except N.TemplateUnavailable as e:
        ok("缺陷参照缺失 ⇒ 只拦出方案这一条链", True, str(e)[:60])
    # detect 链不碰参照：能跑到"没有检测产物"的正常 needs_human
    orig_run = N._run_detect
    N._run_detect = lambda: None
    try:
        _n = N.make_nodes(TMP / "run-ghost", live_llm=False)
        d = _n["detect"]({"fixture": "F-X-01"})
        ok("detect 链在无参照工程下照常工作", d.get("route") == "needs_human",
           str(d)[:80])
    finally:
        N._run_detect = orig_run
    PP.unregister_profile("ghost")

    # ── ② fail-closed：未知 id 不回落内置 ──
    try:
        PP.load_profile("绝不存在的档案")
        ok("未知档案 ⇒ 抛错不回落内置", False, "竟然回落了")
    except PP.ProfileError as e:
        ok("未知档案 ⇒ 抛错不回落内置（拒测胜过测错）", True, str(e)[:50])

    # ── ③ 环境变量指定工程（不改代码、不传参的接入方式）──
    # 走**真实产品路径**：projects/<id>.json + 全新子进程。
    # 上一版只 register 到内存再拉子进程——内存档案对新进程不可见，
    # 子进程只能读文件，于是必然失败（失败原因与被测能力无关）。
    alt = build_alt_project()
    PP.register_profile(alt, activate=False)
    pdir = ROOT / "projects"
    pfile = pdir / f"{PID}.json"
    pdir.mkdir(exist_ok=True)
    pfile.write_text(json.dumps({
        "profile_id": PID, "display_name": alt.display_name,
        "project_path": str(alt.project_path), "git_root": str(alt.git_root),
        "scenes_dir": alt.scenes_dir, "scene_template": alt.scene_template,
        "assets_root": alt.assets_root, "subject_dir_style": alt.subject_dir_style,
        "subjects": alt.subjects, "clean_subject": alt.clean_subject,
        "subject_file": alt.subject_file, "fix_subject": alt.fix_subject,
        "defect_signature": alt.defect_signature,
        "quality_tier": alt.quality_tier}, ensure_ascii=False), encoding="utf-8")
    try:
        env = dict(os.environ, UPI_PROFILE=PID)
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys;sys.path.insert(0,%r);"
             "from upi.config.project_profile import active_profile;"
             "p=active_profile();print(p.abs_project.name, p.scene_of('F-CPU-01').name)"
             % str(ROOT)],
            capture_output=True, text=True, timeout=120, env=env,
            encoding="utf-8", errors="replace")
        ok("UPI_PROFILE + projects/*.json：新进程按档案选工程",
           r.stdout.strip().startswith("alt-project") and "Scene-F-CPU-01.unity" in r.stdout,
           (r.stdout + r.stderr)[:140])
    finally:
        pfile.unlink(missing_ok=True)   # 临时档案文件不留进仓库
    ok("临时档案文件已清（不污染 projects/）", not pfile.exists(), str(pfile))

    # ── ④ 切换生效：所有消费点取到的路径都跟着档案变 ──
    PP.use_profile(PID)
    try:
        prof = PP.active_profile()
        ok("切换后 scene_of 用新模板（连字符风格）",
           prof.scene_of("F-CPU-01").name == "Scene-F-CPU-01.unity",
           prof.scene_of("F-CPU-01").name)
        ok("切换后目录映射用新风格（不转下划线）",
           prof.subject_asset_dir("F-CPU-01").name == "F-CPU-01",
           prof.subject_asset_dir("F-CPU-01").name)

        # 候选检索真的在新工程里找到缺陷（不是回夹具里翻）
        from upi.core.candidate_retrieval import _fixture_dir_of, retrieve_candidates
        from upi.core.detector import DEFAULT_REGISTRY
        det = next(d for d in DEFAULT_REGISTRY if d.id == "DET-CPU-GC-FRAMEALLOC")
        fdet = _fixture_dir_of(det)
        ok("候选扫描根指向第二工程（夹具目录被换掉）",
           fdet is not None and str(fdet).startswith(str(ALT)), str(fdet)[:70])
        cands = retrieve_candidates(None, det)
        ok("在第二工程里检索到缺陷候选", len(cands) >= 1, f"{len(cands)} 条")
        # 锚点路径的**基准**必须跟着档案走。上一版这里只断言"有候选"，
        # 于是把锚点基准退回仓库根时测试是靠 ValueError 崩溃暴露的
        # ——崩溃也算发现，但整条套件后面的断言全部不跑，等于丢覆盖。
        # 显式钉住格式：相对 git_root 的工程内片段，不含绝对路径也不含仓库目录名。
        _anch = [a for _rc, an in cands for a in an]
        ok("候选锚点以 git_root 为基准（工程内相对片段）",
           _anch and all(a.startswith("anchor:Runtime/F-CPU-01/") for a in _anch),
           str(_anch[:2])[:110])

        seen = captured_nodes(TMP / "run-alt")
        proj_s, scene_s, fp_s = seen["snapshot"]
        ok("快照收到第二工程的 project_path",
           proj_s.replace("\\", "/").endswith("alt-project"), proj_s[:70])
        ok("快照收到按新模板拼出的 scene",
           scene_s.endswith("Scene-F-CPU-01.unity"), scene_s[-40:])
        ok("fixer.execute 同样收到新工程路径",
           seen["execute"][0].replace("\\", "/").endswith("alt-project"),
           str(seen["execute"])[:80])
        ep = (seen["plan"].get("edit_plan") or {})
        touched = [d["file"] for d in ep.get("diffs", [])]
        # subject_file 语义是"相对 git_root"，所以路径里不该出现目录名
        # alt-project——断言要看的是"新档案声明的那个文件"，不是字符串里
        # 有没有工程目录名（我一开始就是这么写错的，反而把正确行为判成失败）。
        # diffs 是两条（Awake 段 + Update 段）且都落在同一个文件，
        # 所以 touched 是两条相同路径 ⇒ 用集合比，不能假设长度为 1
        # （我上一版就是按长度 1 写的，把正确行为判成失败）。
        ok("EditPlan 的目标文件来自新档案（未残留夹具路径）",
           set(touched) == {"Runtime/F-CPU-01/SkillRunner.cs"}
           and bool(touched) and not any("UpiFixtures" in x for x in touched),
           str(touched)[:90])
        ok("touched_assets 与 diffs 同源（无第二份文件路径）",
           ep.get("touched_assets") == touched, str(ep.get("touched_assets"))[:80])

        # 夹具清单优先级：配置文件显式声明 > 档案缺省（用户意图优先，
        # 与 enabled 同规则）。档案只在没写的时候兜缺省。
        # ⚠️ 但换工程时 yaml 里遗留的旧清单会让守护去采不存在的机制，
        #    所以不一致必须当场告警——这条断言测的就是这个告警。
        import upi.agents.daemon as D
        conf = D.load_runtime_conf()
        ok("守护夹具清单：yaml 显式声明优先于档案（不静默推翻用户配置）",
           conf.get("fixtures") == ["CLEAN", "F-CPU-01", "F-GPU-01",
                                    "F-ANIM-01", "F-LOAD-01"],
           str(conf.get("fixtures"))[:70])
        mism = [e for e in (json.loads(l) for l in
                            E.EVENTS.read_text(encoding="utf-8").splitlines()
                            if l.strip())
                if e.get("kind") == "profile_fixtures_mismatch"]
        ok("遗留清单与档案不一致 ⇒ 当场告警（换工程不提示是真陷阱）",
           bool(mism) and mism[-1].get("profile") == PID,
           f"{len(mism)} 条 {mism[-1].get('extra') if mism else ''}")
        ok("档案缺省真正生效（配置文件没写时）",
           PP.load_profile(PID).fixture_universe() == ["CLEAN-99", "F-CPU-01"],
           str(PP.load_profile(PID).fixture_universe())[:70])

        # 体检表规格：派生必须在工程内（不是仓库根）
        ok("夹具规格路径按档案字段在工程内派生",
           prof.fixture_spec == ALT / prof.fixture_spec_rel,
           str(prof.fixture_spec)[:70])
        # ⚠️ 边界如实记录：checklist.FIXTURE_SPEC 是**模块属性**，import 时
        #   取一次值（scripts/check_fixture_split.py 靠覆盖它做隔离，改成
        #   函数就没这个入口）。所以同进程内切档案**不会**带动体检表。
        #   体检表实际作为独立进程/CLI 运行，档案在启动时固定 ⇒ 行为正确。
        #   上一版这条断言必挂：它在 ③ 结尾把 projects/<PID>.json 删掉之后才跑，
        #   子进程根本读不到档案，比对自然是 False——不是产品错，是测试时序错。
        _tmp_pf = ROOT / "projects" / f"{PID}.json"
        _tmp_pf.write_text(json.dumps({
            "profile_id": PID, "project_path": str(ALT), "git_root": str(ALT),
            "scenes_dir": "Scenes", "scene_template": "Scene-{subject}.unity",
            "assets_root": "Runtime", "subject_dir_style": "hyphen",
            "subjects": ["F-CPU-01"], "clean_subject": "CLEAN-99",
            "subject_file": "Runtime/F-CPU-01/SkillRunner.cs",
            "fix_subject": "F-CPU-01", "quality_tier": "built-in-medium"},
            ensure_ascii=False), encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, "-c",
                                "import sys;sys.path.insert(0,%r);"
                                "from upi.config.project_profile import load_profile;"
                                "from upi.tooling.checklist import FIXTURE_SPEC;"
                                "print(FIXTURE_SPEC == load_profile(%r).fixture_spec)"
                                % (str(ROOT), PID)],
                               capture_output=True, text=True, timeout=120,
                               env=dict(os.environ, UPI_PROFILE=PID),
                               encoding="utf-8", errors="replace")
            ok("体检表规格随**子进程启动**时的档案走（非热切换，边界已声明）",
               r.stdout.strip() == "True", (r.stdout + r.stderr)[:120])
        finally:
            _tmp_pf.unlink(missing_ok=True)
    finally:
        PP.unregister_profile(PID)
        PP.reset_profile()

    # ── ⑤ 还原内置后行为与切换前一致（不残留脏值）──
    back = PP.active_profile()
    ok("还原内置档案（无跨用例脏值）",
       back.profile_id == PP.DEFAULT_PROFILE_ID
       and back.scene_of("F-CPU-01").name == "Scene_F-CPU-01.unity",
       back.profile_id)

    # ── ⑥ 阶段 7 批 2：profile 归属进了协议指纹与 G7 比较闸 ──
    # 保护的场景是"其余协议字段全对得上、但数据来自 A 工程而标定来自 B 工程"：
    # 噪声带量的是另一个工程的抖动分布，拿它判本工程 = 无意义比较。
    # 中间那层必须调**产品的比对函数**——测试自己复刻一遍推导等于没测
    # （复刻的那份不是产品，产品改了清单测试照样绿）。
    from upi.core.models import Fingerprint

    ok("profile_id 已进协议指纹字段", "profile_id" in Fingerprint.model_fields)
    import scripts.m1_detect as MD
    ok("G7 比较清单含 profile_id（不是只加了字段没参与比对）",
       "profile_id" in MD.G7_COMPARE_FIELDS, str(MD.G7_COMPARE_FIELDS))

    _base = dict(scene_guid="sha256:same", replay_script_hash="none-v0",
                 quality_tier="urp-balanced", warmup_samples=8, window_samples=20,
                 n_samples=4, poll_rate=1.0, data_tier="A", editor_mode="gui",
                 environment_fingerprint="env-equal", scale_factor=1.0)
    ok("同一工程的两份标定判为可比",
       MD.g7_mismatches(Fingerprint(**_base, profile_id="A"),
                        Fingerprint(**_base, profile_id="A")) == [])
    _m = MD.g7_mismatches(Fingerprint(**_base, profile_id="A"),
                          Fingerprint(**_base, profile_id="B"))
    ok("跨工程套用标定被拦（且报出的就是归属这一项）", _m == ["profile_id"], str(_m))

    # 历史产物兼容：批 2 之前仓库里所有标定都没有 profile 键，只可能来自内置
    # 夹具工程，所以"缺键 ⇒ 内置"是事实判定而非猜默认（I1）。这条一旦写歪，
    # 现成的 baselines/ 会被判成"归属未知"，G7 直接把检测全拦停。
    ok("无 profile 键的历史标定归内置档案（历史 38 轮次不作废）",
       PP.owner_profile_id({}) == PP.DEFAULT_PROFILE_ID
       and PP.owner_profile_id({"profile": "ext-game"}) == "ext-game",
       "缺键或显式值判错")

    # 环境指纹的标签位：内置档案 id 与工程目录名同值 ⇒ 历史 env_fingerprint
    # 逐字节不变（若这里变了，跨 profile 改造会把旧数据全判成环境漂移）。
    from upi.tooling.trace import environment_fingerprint as EF
    _ip = PP.load_profile(PP.DEFAULT_PROFILE_ID)
    ok("内置档案的 env 指纹与按目录名计算逐字节相同",
       EF(str(_ip.abs_project), profile_id=_ip.profile_id)
       == EF(str(_ip.abs_project)), "标签改动污染了历史指纹")

    # projects/ 里不许留下临时档案：③ 与体检表那处都用 try/finally 删了，
    # 但这类"临时文件残留"不会让任何断言变红，只会让仓库多出一个未跟踪文件
    # （实际发生过：手工探针脚本漏清，git status 一直挂着 projects/*.json）。
    _left = sorted(x.name for x in (ROOT / "projects").glob("*.json")) \
        if (ROOT / "projects").is_dir() else []
    ok("projects/ 无临时档案残留", not _left, str(_left)[:80])

    _sz1 = _REAL_EVENTS.stat().st_size if _REAL_EVENTS.exists() else 0
    ok("真实事件流零写入（测试噪音不落仓库）", _sz1 == _real_events_size0,
       f"{_real_events_size0} → {_sz1}")

    print(f"\n==== 工程档案可移植性: {len(PASS)} 通过, {len(FAIL)} 失败"
          + (f" {FAIL}" if FAIL else ""))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
