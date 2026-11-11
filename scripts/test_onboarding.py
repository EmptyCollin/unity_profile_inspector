"""接入向导逻辑层测试（阶段 8 #23）：答案→档案映射、缺项判断、下限表编辑。

只测 upi/tooling/onboarding 的纯逻辑，不起编辑器、不发命令、不碰真实 projects/。
重点钉一件事：**带噪声基准的新工程能注册**。这是真工程实测撞出的死路——
scan 会把基准场景也扫进 subjects，而 ProjectProfile 硬校验 clean ∉ subjects，
于是只要用户填了 clean_subject，向导 100% 被产品模型拒收。修之前这条必红。

现场全用临时目录；commit_profile 落盘被 patch 到临时 projects/，绝不污染仓库。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.config import project_profile as PP           # noqa: E402
from upi.config.project_profile import ProjectProfile  # noqa: E402
from upi.config import replay_conformance as RC        # noqa: E402
from upi.core import events as E                        # noqa: E402
from upi.tooling import onboarding as OB               # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'ok' if cond else 'FAIL'}] {name}"
          + ("" if cond else f"  ← {str(detail)[:140]}"))


def make_proj(base: Path, scenes: list[str], template="{subject}.unity",
              scenes_dir="Assets/Scenes"):
    """造一个只有场景文件的假工程（不建 git，git_root 由测试显式给）。"""
    d = base / scenes_dir
    d.mkdir(parents=True, exist_ok=True)
    for s in scenes:
        name = template.format(subject=s)
        (d / name).write_text("%YAML 1.1\n", encoding="utf-8")
    return base


def answers(base: Path, **over):
    a = {"profile_id": "proj-x", "project_path": str(base),
         "git_root": str(base)}      # 显式给 git 根，避免依赖临时区是否在仓库内
    a.update(over)
    return a


def make_replay_markers(base: Path):
    """在假工程 Assets/ 里写一个含 RS-2/3/4 静态标记的 C#，让 probe 扫得到。
    文件名与 ReplayDriver.cs 无关——探测认内容标记，不认文件名。"""
    (base / "Assets").mkdir(parents=True, exist_ok=True)
    (base / "Assets" / "FakeReplay.cs").write_text(
        "class X { void M(){ UnityEngine.Random.InitState(7);"
        " UnityEngine.Time.fixedDeltaTime = 0.02f;"
        " var c = \"arm:100:10:12\"; var o = \"load:7\";"
        " int origin = 0; } }\n", encoding="utf-8")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="upi_onb_"))
    # 全程隔离总线：commit_profile / set_active_profile_id / floor_write 的落点
    # 都经 projects_dir()→E.PROJECTS，isolate 后一律指向临时区，绝不写真实仓库
    # （上一轮注册向导一跑就把 projects/.active 写进了仓库，就是这么污染的）。
    E.isolate(tmp / "runs")
    os.environ.pop("UPI_PROFILE", None)   # 指针回读测的是"无 env、无显式 id"链
    try:
        # ── ① 核心回归：带噪声基准的工程能注册 ──
        print("== ① 带噪声基准的注册（撞过的死路）==")
        p1 = make_proj(tmp / "p1", ["Baseline", "SampleScene"])
        prof, probs = OB.build_profile(answers(p1, clean_subject="Baseline"))
        ok("填了 clean_subject 也能出档案（修前必被产品模型拒收）",
           prof is not None, probs)
        if prof:
            ok("噪声基准已从被测清单摘出（clean ∉ subjects）",
               prof.subjects == ["SampleScene"] and prof.clean_subject == "Baseline",
               f"subjects={prof.subjects} clean={prof.clean_subject}")

        # 不填基准 ⇒ 两个场景都是被测（不误删）
        prof2, _ = OB.build_profile(answers(p1))
        ok("未填基准时全部场景都算被测",
           prof2 is not None and prof2.subjects == ["Baseline", "SampleScene"],
           prof2.subjects if prof2 else None)

        # ── ② 场景反解与 git 根 ──
        print("== ② 场景扫描与 git 根 ==")
        p3 = make_proj(tmp / "p3", ["Foo", "Bar"], template="Scene_{subject}.unity")
        got = OB.scan_scene_subjects(p3, "Assets/Scenes", "Scene_{subject}.unity")
        ok("Scene_{subject}.unity 模板能反解机制名", got == ["Bar", "Foo"], got)
        ok("模板不匹配的文件不误收", OB.scan_scene_subjects(
            p3, "Assets/Scenes", "X_{subject}.unity") == [])
        ok("不存在的场景目录 ⇒ 空列表不抛", OB.scan_scene_subjects(
            p3, "Nope", "{subject}.unity") == [])
        gp, gp_probs = OB.build_profile(answers(tmp / "notgit", subjects=["A"]))
        ok("工程不在 git 内 ⇒ 拦下并说清要 git init",
           gp is None and any("git" in x for x in gp_probs), gp_probs)

        # ── ③ 答案校验：缺什么必须问，绝不静默兜默认 ──
        print("== ③ 非法答案一律回问题清单（profile=None 时清单必非空）==")
        for label, ans in (
            ("空 id", answers(p1, profile_id="")),
            ("相对路径", {"profile_id": "q", "project_path": "relative/dir"}),
            ("路径不存在", answers(p1, project_path=str(tmp / "ghost"))),
            ("scenes_dir 越界", answers(p1, scenes_dir="../../etc")),
            ("模板无占位符", answers(p1, scene_template="Scene.unity")),
            ("束目录绝对路径", answers(p1, clean_subject="Baseline",
                                       cal_dir_rel="/etc/cal")),
        ):
            pr, pb = OB.build_profile(ans)
            ok(f"{label} ⇒ 拒收且给出原因",
               pr is None and len(pb) >= 1, f"profile={pr is not None} probs={pb[:1]}")

        # ── ④ 下一步指引按实况（不抄固定清单）──
        print("== ④ 注册后的下一步 ==")
        prof3, _ = OB.build_profile(answers(p1, clean_subject="Baseline"))
        steps = OB.calibration_next_steps(prof3)
        ok("有噪声基准 ⇒ 提示采束须同轮在场（L2 前提）",
           any("同轮在场" in s for s in steps), steps)
        ok("未配下限表 ⇒ 明说该腿不启用",
           any("下限" in s for s in steps), steps)
        ok("未指定可修复机制 ⇒ 明说一律转人工",
           any("转人工" in s for s in steps), steps)
        prof4, _ = OB.build_profile(answers(p1))     # 无基准
        ok("无基准 ⇒ 指到 L0 而非假装能出相对检出",
           any("L0" in s for s in OB.calibration_next_steps(prof4))
           if prof4 else False)

        # ── ⑤ 下限表编辑（校验走产品模型，UI 不复刻规则）──
        print("== ⑤ 下限表编辑 ==")
        tbl, _ = OB.floor_view(prof3)
        ok("未配表 ⇒ floor_view 给 (None, 说明) 而不是空表",
           tbl is None, tbl)
        base_tbl = OB.FloorTable(source="预算 v1", targets=[
            OB.FloorTarget(metric="drawCalls", op="<=", value=100)])
        nt, msg = OB.floor_apply(prof3, base_tbl, "add",
                                 {"metric": "frame_time_ms", "op": "p95<=",
                                  "value": "16.7", "unit": "ms"})
        ok("add 合法条目 ⇒ 多一条且未写入（返回新表对象）",
           nt is not None and len(nt.targets) == 2, msg)
        bad, badmsg = OB.floor_apply(prof3, base_tbl, "add",
                                     {"metric": "x", "op": "<>", "value": 1})
        ok("非法 op ⇒ 校验拒收（复用 FloorTarget 本体，不在 UI 复刻）",
           bad is None and "校验" in badmsg, badmsg)
        badneg, bn = OB.floor_apply(prof3, base_tbl, "add",
                                    {"metric": "x", "op": "<=", "value": -5})
        ok("负线值 ⇒ 拒收", badneg is None, bn)
        del_t, dm = OB.floor_apply(prof3, base_tbl, "del", {"index": "1"})
        ok("del 命中序号 ⇒ 少一条", del_t is not None and len(del_t.targets) == 0, dm)
        del_bad, db2 = OB.floor_apply(prof3, base_tbl, "del", {"index": "9"})
        ok("del 越界序号 ⇒ 拒收并给合法范围", del_bad is None, db2)
        src_clear, sc_msg = OB.floor_apply(prof3, base_tbl, "source", {"source": "  "})
        ok("出处清空 ⇒ 拒收（无出处=手调纪律）", src_clear is None, sc_msg)

        # ── ⑥ 落盘出口：总线已隔离 ⇒ 真回读 + 指针真生效 ──
        # 上一版在这里 monkeypatch OB.profile_file，结果档案写进临时区、
        # 激活指针仍经 projects_dir()→E.PROJECTS 落进**真实仓库**（污染就是这么
        # 来的），而且因为 patch 没覆盖读路径，只能"验文件内容"不敢回读。
        # 现在所有落点都经 projects_dir()，起点 E.isolate 一处隔离，读写同源。
        print("== ⑥ 注册落盘与指针生效（总线隔离，读写同源）==")
        ok("指针落点在隔离区（不写真实仓库 projects/）",
           str(PP.active_pointer_path()).startswith(str(tmp)),
           str(PP.active_pointer_path()))
        written = OB.commit_profile(prof3, activate=True)
        ok("commit_profile 落盘成功", written.exists(), str(written))
        ok("激活指针值为新工程 id",
           PP.get_active_profile_id() == "proj-x", PP.get_active_profile_id())
        # 真回读走产品读盘路径（无显式 id、无 env ⇒ 按指针解析）。
        # 上一版"不敢回读"是假回读；读写同源之后这条才测得到注册的真效果。
        back = PP.load_profile()
        ok("指针真生效：默认解析落到新工程（含 subjects/clean）",
           back.profile_id == "proj-x" and back.subjects == ["SampleScene"]
           and back.clean_subject == "Baseline",
           f"{back.profile_id}/{back.subjects}/{back.clean_subject}")
        ok("回读档案与原对象逐字段等价",
           back.model_dump() == prof3.model_dump(), "字段有差")
        # activate=False ⇒ 只写档案不动指针（防止"注册即切工程"这种越权副作用）
        _pointer_bak = PP.get_active_profile_id()
        prof_alt = prof3.model_copy(update={"profile_id": "proj-y",
                                            "display_name": "proj-y"})
        OB.commit_profile(prof_alt, activate=False)
        ok("activate=False 只落档案，不抢激活指针",
           PP.get_active_profile_id() == _pointer_bak
           and PP.profile_file("proj-y").exists(),
           f"指针={PP.get_active_profile_id()}")
        # floor_write：路径未配 ⇒ 拒绝，不猜位置
        try:
            OB.floor_write(prof3, base_tbl)      # prof3 没配 floor_targets_rel
            wrote = "no-raise"
        except PP.ProfileError:
            wrote = "raised"
        ok("未配 floor_targets_rel ⇒ floor_write 拒绝（不猜写到哪）",
           wrote == "raised", wrote)
        # 配了路径的工程：写入后可被 load_floor_table 读回
        (p1 / "upi_floor.json").write_text(json.dumps(
            {"source": "s", "targets": []}), encoding="utf-8")
        prof5, _ = OB.build_profile(answers(p1, clean_subject="Baseline",
                                            floor_targets_rel="upi_floor.json"))
        wp = OB.floor_write(prof5, base_tbl)
        rdtbl, _note = OB.floor_view(prof5)
        ok("下限表写入 ⇒ floor_view 读回同一条目",
           wp.exists() and rdtbl is not None and len(rdtbl.targets) == 1
           and rdtbl.targets[0].metric == "drawCalls", wp)
        # 无出处的表**根本构造不出来**（守卫在 FloorTable 构造期，不是 floor_write
        # 里再判一次——那样写永远是死代码，本批实测删掉）。这里钉构造期拒收。
        try:
            OB.FloorTable(source="", targets=[])
            nosrc = "no-raise"
        except Exception:  # noqa: BLE001 — pydantic 校验错即预期
            nosrc = "raised"
        ok("无出处表在构造期即拒收（落盘入口拿不到坏表）",
           nosrc == "raised", nosrc)

        # ── ⑦ 与手工注册档案的字段级一致（选法 1 验收线）──
        print("== ⑦ 向导产出的档案 = 手工注册档案（同磁盘现场）==")
        prof_manual = ProjectProfile(
            profile_id="proj-x", display_name="proj-x", project_path=p1,
            git_root=p1, scenes_dir="Assets/Scenes", scene_template="{subject}.unity",
            assets_root="Assets", subject_dir_style="hyphen",
            subjects=["SampleScene"], clean_subject="Baseline",
            quality_tier="urp-balanced", floor_targets_rel="")
        keyf = ("subjects", "clean_subject", "scenes_dir", "scene_template",
                "assets_root", "floor_targets_rel", "quality_tier")
        diffs = [k for k in keyf
                 if prof3 is not None and getattr(prof3, k) != getattr(prof_manual, k)]
        ok("向导与手工注册的字段逐字一致（零行为差）",
           not diffs, diffs)

        # ── ⑧ 回放准入定档（#31）：注册当场探测、full 必须带合格产物、fail-closed ──
        print("== ⑧ 配合度定档（探测→降档闭环→预览）==")
        p_conf = make_proj(tmp / "pc", ["SampleScene"])
        # (a) 裸工程（只有场景文件，无回放标记）⇒ partial：
        #     RS-1 过、RS-2/3/4 无证据 ⇒ 慢性病可修可验、发作类只走免责工单
        prof_a, _ = OB.build_profile(answers(p_conf, subjects=["SampleScene"]))
        ok("无回放标记 ⇒ 定档 partial（不是 none——场景可脚本化是够的）",
           prof_a is not None and prof_a.conformance_tier == "partial",
           prof_a.conformance_tier if prof_a else None)
        ok("条款证据逐条落档（工单页眉引用要有据）",
           prof_a is not None
           and prof_a.conformance_clauses["RS-1"]["pass"] is True
           and prof_a.conformance_clauses["RS-3"]["pass"] is False)
        # (b) 带回放标记但**无验证产物** ⇒ 仍是 partial（full 的降档闭环在
        #     probe 返回值里做完，不留"字段 full、行为 partial"两份真相）
        make_replay_markers(p_conf)
        prof_b, _ = OB.build_profile(answers(p_conf, subjects=["SampleScene"]))
        ok("四条款静态证据齐但无对齐产物 ⇒ 当场降 partial",
           prof_b is not None and prof_b.conformance_tier == "partial"
           and prof_b.conformance_clauses["RS-3"]["pass"] is True,
           prof_b.conformance_tier if prof_b else None)
        # (c) 有合格产物（final=aligned）⇒ full 成立。live_ref 用绝对路径：
        #     pathlib 拼绝对串覆盖左轴（实测），临时区产物可被 _live_check 读到
        art = tmp / "aligned.json"
        art.write_text(json.dumps({"final": "aligned"}), encoding="utf-8")
        prof_c = ProjectProfile(**{
            **(prof_b.model_dump() if prof_b else {}),
            "conformance_live_ref": str(art)})
        rc_c = RC.probe(prof_c)
        ok("静态证据齐 + 产物 aligned ⇒ full（一条龙解锁）",
           rc_c["tier"] == "full" and rc_c["live_verified"] is True, rc_c["tier"])
        # (d) 产物结论不对 ⇒ 照样不是 full（不拿"文件存在"冒充"验证通过"）
        art_bad = tmp / "diverged.json"
        art_bad.write_text(json.dumps({"final": "diverged"}), encoding="utf-8")
        prof_d = ProjectProfile(**{
            **(prof_b.model_dump() if prof_b else {}),
            "conformance_live_ref": str(art_bad)})
        ok("产物 final≠aligned ⇒ probe 拒并降档",
           RC.probe(prof_d)["tier"] == "partial")
        # (e) fail-closed：既有档案没这个字段 ⇒ 装载即 none，
        #     "没探测过"绝不读成"探测过且合格"
        prof_e = ProjectProfile(profile_id="old", project_path=p_conf,
                                git_root=p_conf, subjects=["SampleScene"],
                                scenes_dir="Assets/Scenes", clean_subject="")
        ok("无 conformance 字段的档案缺省 none（fail-closed）",
           prof_e.conformance_tier == "none")
        # (f) 预览块在 y 确认前把责任边界说清（三档各有各自的文案）
        pv = OB.conformance_preview(prof_e)
        ok("none 档预览含'只体检开方，管线不动刀'",
           any("不动刀" in x for x in pv), pv[:2])
        pv_b = OB.conformance_preview(prof_b)
        ok("partial 档预览含免责语义（发作类=免责工单）",
           any("免责" in x for x in pv_b), pv_b[:2])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n==== 接入向导: {len(PASS)} 通过, {len(FAIL)} 失败"
          + (f" {FAIL}" if FAIL else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
