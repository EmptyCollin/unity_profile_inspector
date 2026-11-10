"""floor_check 单测：下限表模型、加载、判据的数值正确性（批 1）。

下限判据是纯数值逻辑，最容易错也最不能错——op 方向搞反、分位算错、
"未评估"被当成"达标"，都会让"提高下限"这件事的结论整体失真。
这里逐条钉死，并特别覆盖两条设计文档承诺的硬约束：
  · 无出处的表加载期拒收（不是运行时警告）；
  · 数据不足 ⇒ unevaluated 单列，绝不从 breached 为空反推达标。
用 tempfile，不碰仓库任何真实产物目录。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.floor_check import (check_floors, load_floor_table,   # noqa: E402
                                   window_series, _percentile, FloorTarget)
from upi.config.project_profile import ProjectProfile, ProfileError        # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"   [{'ok' if cond else 'FAIL'}] {name}" + (f"  ← {detail}" if not cond else ""))


def main() -> int:
    # ── 1. FloorTarget 构造期校验 ──
    ok("负 value 拒收", _raises(lambda: FloorTarget(metric="m", op="<=", value=-1)))
    ok("空 metric 拒收", _raises(lambda: FloorTarget(metric="", op="<=", value=1)))
    ok("非法 op 拒收", _raises(lambda: FloorTarget(metric="m", op="<>", value=1)))
    ok("value=0 合法（要求零批处理是有效下限）",
       FloorTarget(metric="batches", op="<=", value=0).value == 0)

    # ── 2. 分位函数（小样本行为必须诚实，不是精度承诺）──
    ok("n=1 分位=该值本身", _percentile([100.0], 0.95) == 100.0)
    ok("n=2 p95≈靠尾值", abs(_percentile([1.0, 2.0], 0.95) - 1.95) < 1e-9,
       str(_percentile([1.0, 2.0], 0.95)))
    ok("n=4 p99 = 接近 max（线性插值定义）",
       abs(_percentile([10.0, 20.0, 30.0, 40.0], 0.99) - 39.7) < 1e-9,
       str(_percentile([10.0, 20.0, 30.0, 40.0], 0.99)))
    ok("中位数与分位数区分（[1,2,100] 中位 2、p95≈90+）",
       _median([1.0, 2.0, 100.0]) == 2.0 and _percentile([1.0, 2.0, 100.0], 0.95) > 50)

    # ── 3. evaluate 四个 op 的判向 ──
    # <=：中位超线才算破
    t = FloorTarget(metric="drawCalls", op="<=", value=1500)
    ok("<= 未破线（中位 1200≤1500）", t.evaluate([1000, 1200, 1400])["breached"] is False)
    ok("<= 破线（中位 1600>1500）", t.evaluate([1500, 1600, 1700])["breached"] is True)
    # p95<=：尾部超线即破（中位可能达标）
    t95 = FloorTarget(metric="frame", op="p95<=", value=16.7)
    r95 = t95.evaluate([8.0, 9.0, 10.0, 50.0])   # 中位 ~9.5 达标，但尾部 50 破
    ok("p95<= 抓的是尾部不是中位（中位达标仍判破）",
       r95["breached"] is True and r95["stat"] > 16.7, str(r95))
    # >=：反向指标，低于线才破
    tge = FloorTarget(metric="hitrate", op=">=", value=0.9)
    ok(">= 低于线判破", tge.evaluate([0.8, 0.85, 0.82])["breached"] is True)
    ok(">= 达标不判破", tge.evaluate([0.95, 0.97])["breached"] is False)
    ok("空序列 ⇒ evaluate 返回 None（不是通过）", t.evaluate([]) is None)

    # ── 4. window_series：从原始束条目抽逐窗口序列 ──
    fx = {"windows": [
        {"metrics": {"drawCalls": {"median": 1200}, "batches": {"median": 30}}},
        {"metrics": {"drawCalls": {"median": 1600}, "batches": {"median": 31}}},
        {"metrics": {"drawCalls": {"median": 1400}}},
    ]}
    ser = window_series(fx)
    ok("window_series 抽出逐窗口中位", ser.get("drawCalls") == [1200.0, 1600.0, 1400.0],
       str(ser))
    ok("window_series 缺字段机制短序列不报错", ser.get("batches") == [30.0, 31.0])
    ok("window_series 空条目 ⇒ 空字典", window_series({}) == {}
       and window_series(None) == {})

    # ── 5. 表加载：缺文件=None，坏内容=ProfileError，无出处=拒收 ──
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        ok("缺文件 ⇒ load 返回 None（不启用，非空表通过）",
           load_floor_table(d / "nope.json") is None)
        bad_src = d / "bad.json"
        bad_src.write_text(json.dumps({"source": "", "targets": []}), encoding="utf-8")
        ok("无出处 ⇒ 拒收（无出处的绝对线=手调）", _raises(lambda: load_floor_table(bad_src)))
        broken = d / "broken.json"
        broken.write_text("{不是 json", encoding="utf-8")
        ok("内容坏 ⇒ ProfileError（不静默当空表 = 防假绿）",
           _raises_profile(lambda: load_floor_table(broken)))
        good = d / "good.json"
        good.write_text(json.dumps({
            "source": "性能预算 v3 · 60fps",
            "targets": [{"metric": "drawCalls", "op": "<=", "value": 1500, "unit": "calls"},
                        {"metric": "frame", "op": "p95<=", "value": 16.7, "unit": "ms",
                         "subject": "F-GPU-01"}]}, ensure_ascii=False), encoding="utf-8")
        tbl = load_floor_table(good)
        ok("好表 ⇒ 模型化加载", tbl is not None and len(tbl.targets) == 2)
        # 6. applicable：全局条目 + 机制限定条目
        ok("applicable(F-GPU-01) 含全局与该机制专属",
           [t.metric for t in tbl.applicable("F-GPU-01")] == ["drawCalls", "frame"])
        ok("applicable(F-CPU-01) 只含全局（frame 限定给 F-GPU-01）",
           [t.metric for t in tbl.applicable("F-CPU-01")] == ["drawCalls"])
        # 7. content_hash：同内容同哈希、改线变哈希
        h1 = tbl.content_hash()
        tbl2 = load_floor_table(good)
        ok("同内容 ⇒ 同哈希（重存不误伤可比性）", tbl2.content_hash() == h1)
        tbl.targets[0].value = 999
        ok("改目标线 ⇒ 哈希变（提标进指纹，与旧轮次划清）", tbl.content_hash() != h1)

    # ── 8. check_floors：命中/达标/未评估三态，unevaluated 不冒充通过 ──
    with tempfile.TemporaryDirectory() as td:
        good = Path(td) / "t.json"
        # fx 的 drawCalls 逐窗口中位 = [1200,1600,1400] ⇒ median 1400。
        # 线设 1300 ⇒ 1400>1300 真破线；triangles 本轮没采到 ⇒ 进 unevaluated。
        good.write_text(json.dumps({"source": "x", "targets": [
            {"metric": "drawCalls", "op": "<=", "value": 1300},
            {"metric": "triangles", "op": "<=", "value": 999999}]}), encoding="utf-8")
        tbl = load_floor_table(good)
        # drawCalls 破线、triangles 本轮没采到
        res = check_floors("F-GPU-01", fx, tbl)
        ok("破线项进 breached", [b["metric"] for b in res["breached"]] == ["drawCalls"],
           str(res["breached"]))
        ok("未采到的指标进 unevaluated 而非通过",
           [u["metric"] for u in res["unevaluated"]] == ["triangles"], str(res["unevaluated"]))
        ok("breached 为空 ≠ 全达标：unevaluated 仍要单列（I1/I2）",
           len(res["evaluated"]) == 1 and len(res["unevaluated"]) == 1)
        ok("floor finding 带 basis=floor_target（与相对检出物理分开）",
           res["breached"][0]["basis"] == "floor_target")

    # ── 9. profile 侧：floor_targets_rel 校验 + floor_targets_path 派生 ──
    base = dict(profile_id="p1", project_path=Path(tempfile.gettempdir()) / "p1",
                git_root=Path(tempfile.gettempdir()))
    ok("floor_targets_rel 空 ⇒ floor_targets_path None（未启用）",
       ProjectProfile(**base).floor_targets_path is None)
    ok("越界 floor_targets_rel 拒收", _raises(lambda: ProjectProfile(
        **base, floor_targets_rel="../etc/passwd")))
    ok("非 .json 后缀拒收", _raises(lambda: ProjectProfile(
        **base, floor_targets_rel="targets.txt")))
    prof = ProjectProfile(**base, floor_targets_rel="bench/floor.json")
    ok("floor_targets_path 相对工程根派生",
       prof.floor_targets_path == prof.abs_project / "bench" / "floor.json",
       str(prof.floor_targets_path))
    # 内置档案必须不启用下限表（否则既有回归的行为基线会漂移）
    from upi.config.project_profile import load_profile, DEFAULT_PROFILE_ID
    ok("内置档案不启用下限表（零行为变化基线）",
       load_profile(DEFAULT_PROFILE_ID).floor_targets_path is None)

    print(f"\n==== floor_check: {len(PASS)} 通过, {len(FAIL)} 失败"
          + (f" {FAIL}" if FAIL else ""))
    return 1 if FAIL else 0


def _median(vals):
    import statistics
    return statistics.median(vals)


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:  # noqa: BLE001
        return True


def _raises_profile(fn) -> bool:
    try:
        fn()
        return False
    except ProfileError:
        return True
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    sys.exit(main())
