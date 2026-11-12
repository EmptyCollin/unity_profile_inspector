"""劣化必须“肉眼不可辨”：同一盘面下逐项开关 legacy_*，截图做像素级对比。

只验证视觉不变性（劣化是性能问题，不是换皮）；性能差异由 profiler/机会扫描负责。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "sim" / "project"
OUT = ROOT / "runs"

BASE = {
    "case_id": "degrade",
    "seed": 777,
    "autoplay": False,
    "round_seconds": 60.0,
    "round_seconds_min": 10.0,
    "target_base": 600,
    "preview_seconds": 0.0,
    "shop_every": 3,
    "stack_chance_base": 0.0,
    "stack_chance_per_tier": 0.0,
}
FLAGS = ["legacy_ui", "legacy_material", "legacy_match_alloc", "legacy_particle",
         "legacy_canvas", "legacy_texture", "legacy_hint", "legacy_anim"]


def unity(*args: str, timeout: int = 120) -> dict:
    cmd = ["unity", *args, "--project-path", str(PROJECT), "--format", "json"]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"success": False, "raw": (p.stdout or "")[-800:]}


def eval_js(code: str) -> str:
    r = unity("command", "script eval", "--code", code)
    res = (r.get("data") or {}).get("result") or {}
    if not res.get("success"):
        raise RuntimeError(f"eval failed: {json.dumps(r, ensure_ascii=False)[-600:]}")
    return res.get("result")


def capture(name: str) -> Path:
    path = OUT / name
    r = unity("command", "capture game_view", "--output-path", str(path),
              "--width", "1280", "--height", "720")
    if not r.get("success"):
        raise RuntimeError(f"capture failed {name}: {r.get('errors') or r}")
    return path


def scene(flag: str) -> Path:
    cfg = dict(BASE)
    for f in FLAGS:
        cfg[f] = f == flag
    n = 8
    colors = [(r * 2 + c) % 5 for r in range(n) for c in range(n)]
    layers = [1] * (n * n)
    for (r, c, v) in [(1, 2, 2), (3, 5, 3), (6, 1, 2), (4, 4, 4)]:
        layers[r * n + c] = v
    eval_js(
        "var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();"
        "g.DebugLoadConfig(" + json.dumps(json.dumps(cfg)) + ");"
        "g.DebugSetBoard(" + str(n) + ", new int[]{" + ",".join(map(str, colors)) + "},"
        "new int[]{" + ",".join(map(str, layers)) + "});"
        "return \"ok\";")
    time.sleep(0.4)
    return capture(f"degrade_{flag if flag else 'baseline'}.png")


def diff(a: Path, b: Path) -> dict:
    ia = np.asarray(Image.open(a).convert("RGB"), dtype=np.int16)
    ib = np.asarray(Image.open(b).convert("RGB"), dtype=np.int16)
    if ia.shape != ib.shape:
        return {"shape_mismatch": [ia.shape, ib.shape], "mean_abs": 255.0, "frac_over_24": 1.0}
    d = np.abs(ia - ib).max(axis=2)
    return {
        "mean_abs": round(float(d.mean()), 4),
        "frac_over_8": round(float((d > 8).mean()), 6),
        "frac_over_24": round(float((d > 24).mean()), 6),
        "max": int(d.max()),
    }


def main() -> int:
    tmp = Path(tempfile.gettempdir()) / "devsim_case.json"
    tmp.write_text(json.dumps(BASE), encoding="utf-8")
    results = []
    try:
        unity("command", "editor.playmode stop")
        r = unity("command", "scene open", "--path", "Assets/Scenes/00_Bootstrap.unity")
        if not r.get("success"):
            print("scene open 失败:", r.get("errors") or r)
            return 1
        unity("command", "editor.playmode start")
        time.sleep(2.0)
        eval_js("var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();"
                "g.DebugLoadConfig(" + json.dumps(json.dumps(BASE)) + "); g.DebugStartRun(); return \"ok\";")
        time.sleep(0.3)

        base = scene("")
        print("baseline:", base)
        for flag in FLAGS:
            shot = scene(flag)
            d = diff(base, shot)
            # 肉眼不可辨门禁：平均差 < 1/255，明显差异像素占比 < 0.5%
            ok = d["mean_abs"] < 1.0 and d["frac_over_24"] < 0.005
            results.append({"flag": flag, "ok": ok, "diff": d, "shot": str(shot)})
            print(f"  [{'ok' if ok else 'FAIL'}] {flag:20s} mean={d['mean_abs']:.4f} "
                  f"over24={d['frac_over_24']:.4%} max={d['max']}")

        ts = time.strftime("%Y%m%d_%H%M%S")
        path = OUT / f"devsim_degrade_visual_{ts}.json"
        report = {"ok": all(r["ok"] for r in results), "baseline": str(base), "results": results}
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n==== 劣化视觉不可辨: {sum(r['ok'] for r in results)}/{len(results)}；证据 {path}")
        return 0 if report["ok"] else 1
    finally:
        unity("command", "editor.playmode stop")
        try:
            tmp.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
