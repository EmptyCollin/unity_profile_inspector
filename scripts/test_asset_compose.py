"""资产 overlay 组合测试：路径独立、.meta 必须、同路径冲突拒绝。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.core.case_overlay import apply_cases, tree_hash  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" -> {detail}"))


tmp = Path(tempfile.mkdtemp(prefix="upi_asset_combo_"))
base = tmp / "base"
(base / "Assets").mkdir(parents=True)
(base / "Assets" / "A.asset").write_text("a", encoding="utf-8")
(base / "Assets" / "A.asset.meta").write_text("guid: a", encoding="utf-8")
h = tree_hash(base)


def make_case(cid: str, files: dict[str, str], meta: bool = True):
    d = tmp / cid
    ov = d / "overlay" / "Assets"
    ov.mkdir(parents=True)
    for name, body in files.items():
        (ov / name).write_text(body, encoding="utf-8")
        if name.endswith((".asset", ".prefab", ".unity")) and meta:
            (ov / (name + ".meta")).write_text("guid: " + cid + "-" + name, encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({
        "case_id": cid,
        "overlay": ["Assets/" + n for n in files],
        "base_hash": h,
    }), encoding="utf-8")
    return d


c1 = make_case("c1", {"B.asset": "b1"})
c2 = make_case("c2", {"C.asset": "c2"})
out = tmp / "out"
try:
    run = apply_cases(base, [c1, c2], out)
    ok("两资产叠加", run.case_ids == ["c1", "c2"]
       and (out / "Assets/B.asset").read_text(encoding="utf-8") == "b1"
       and (out / "Assets/C.asset").read_text(encoding="utf-8") == "c2")
    ok("meta 同时复制", (out / "Assets/B.asset.meta").exists()
       and (out / "Assets/C.asset.meta").exists())
    ok("base 不修改", (base / "Assets/A.asset").read_text(encoding="utf-8") == "a")
    c3 = make_case("c3", {"B.asset": "b3"})
    try:
        apply_cases(base, [c1, c3], tmp / "conflict")
        ok("同路径资产冲突拒绝", False)
    except ValueError:
        ok("同路径资产冲突拒绝", True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# 缺 meta 拒绝
tmp2 = Path(tempfile.mkdtemp(prefix="upi_asset_nometa_"))
try:
    b2 = tmp2 / "base"
    (b2 / "Assets").mkdir(parents=True)
    (b2 / "Assets" / "A.asset").write_text("a", encoding="utf-8")
    h2 = tree_hash(b2)
    d = tmp2 / "c1"
    ov = d / "overlay" / "Assets"
    ov.mkdir(parents=True)
    (ov / "B.asset").write_text("b", encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({
        "case_id": "c1", "overlay": ["Assets/B.asset"], "base_hash": h2}), encoding="utf-8")
    try:
        apply_cases(b2, [d], tmp2 / "out")
        ok("缺 meta 拒绝", False)
    except ValueError:
        ok("缺 meta 拒绝", True)
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

print(f"\n==== 资产组合测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
