"""devsim UI E2E：可复现的 ExecuteEvents 拖拽交换 + 关键界面截图（证据落在 runs/）。

说明：本机 Unity 的 `runtime simulate_pointer` 在无 GUI 焦点的自动化会话里不投递事件
（实测 dragEvents=0），因此拖拽用 EventSystem 的 ExecuteEvents 直接派发，
走的是与真实指针完全相同的 handler 链路（TileDrag.OnBeginDrag/OnDrag/OnEndDrag）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "sim" / "project"
OUT = ROOT / "runs"

CASE = {
    "case_id": "ui_e2e",
    "seed": 12345,
    "autoplay": False,
    "round_seconds": 30.0,
    "round_seconds_min": 5.0,
    "target_base": 600,
    "board_start": 10,
    "board_min": 6,
    "board_step_rounds": 2,
    "preview_seconds": 2.5,
    "shop_every": 3,
    "shop_refresh_cost": 40,
    "stack_chance_base": 0.0,
    "stack_chance_per_tier": 0.0,
}


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
        raise RuntimeError(f"eval failed: {json.dumps(r, ensure_ascii=False)[-800:]}")
    return res.get("result")


def state() -> dict:
    return json.loads(eval_js(
        "var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();"
        "return g.DebugState();"))


def capture(name: str) -> str:
    path = OUT / name
    r = unity("command", "capture game_view", "--output-path", str(path),
              "--width", "1280", "--height", "720")
    if not r.get("success"):
        print("capture failed:", name, r.get("errors") or r)
    return str(path)


def drag_events(stage: str) -> str:
    return eval_js(
        "var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();"
        "var go = g.DebugTileObject(0,0);"
        "var es = UnityEngine.EventSystems.EventSystem.current;"
        "var ped = new UnityEngine.EventSystems.PointerEventData(es);"
        "ped.button = UnityEngine.EventSystems.PointerEventData.InputButton.Left;"
        "ped.position = new Vector2(Screen.width*0.5f, Screen.height*0.5f);"
        "ped.delta = new Vector2(45f, 0f);"
        + stage)


def main() -> int:
    tmp = Path(tempfile.gettempdir()) / "devsim_case.json"
    tmp.write_text(json.dumps(CASE, ensure_ascii=False), encoding="utf-8")
    checks = []
    shots = []

    def ok(name, cond, detail=""):
        checks.append({"name": name, "ok": bool(cond), "detail": str(detail)[:400]})
        print(f"  [{'ok' if cond else 'FAIL'}] {name}"
              + ("" if cond else f" -> {str(detail)[:200]}"))

    try:
        unity("command", "editor.playmode stop")
        r = unity("command", "scene open", "--path", "Assets/Scenes/00_Bootstrap.unity")
        if not r.get("success"):
            print("scene open 失败:", r.get("errors") or r)
            return 1
        unity("command", "editor.playmode start")
        time.sleep(2.0)

        eval_js("var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();"
                "g.DebugLoadConfig(" + json.dumps(json.dumps(CASE)) + ");"
                "g.DebugStartRun();"
                "return \"ok\";")
        time.sleep(0.4)
        shots.append(capture("devsim_v03_game_10x10.png"))
        # 原生 1:1 截图（Game View 实际像素），用于确认文字/边缘清晰度没有被缩放影响。
        natural = OUT / "devsim_v03_game_native.png"
        unity("command", "capture game_view", "--output-path", str(natural))
        shots.append(str(natural))

        col = [(r * 2 + c) % 5 for r in range(6) for c in range(6)]
        col[0] = 1
        col[1] = 0
        col[6 * 1 + 1] = 1
        col[6 * 2 + 1] = 1
        layers = [1] * 36
        eval_js("var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();"
                "g.DebugSetBoard(6, new int[]{" + ",".join(map(str, col)) + "},"
                "new int[]{" + ",".join(map(str, layers)) + "});"
                "return \"ok\";")
        time.sleep(0.2)
        shots.append(capture("devsim_v03_game_6x6.png"))

        before = state()
        drag_events("UnityEngine.EventSystems.ExecuteEvents.Execute(go,ped,"
                    "UnityEngine.EventSystems.ExecuteEvents.beginDragHandler);"
                    "UnityEngine.EventSystems.ExecuteEvents.Execute(go,ped,"
                    "UnityEngine.EventSystems.ExecuteEvents.dragHandler);"
                    "UnityEngine.EventSystems.ExecuteEvents.Execute(go,ped,"
                    "UnityEngine.EventSystems.ExecuteEvents.dragHandler);"
                    "return \"ok\";")
        time.sleep(0.2)
        mid = state()
        shots.append(capture("devsim_v03_drag_highlight.png"))
        drag_events("UnityEngine.EventSystems.ExecuteEvents.Execute(go,ped,"
                    "UnityEngine.EventSystems.ExecuteEvents.endDragHandler);"
                    "return \"ok\";")
        time.sleep(0.30)
        shots.append(capture("devsim_v03_drop_inflight.png"))
        time.sleep(1.1)
        after = state()
        ok("execute_events_drag_swaps_and_clears",
           after["score"] > before["score"] or after["lastCleared"] > 0,
           f"score {before['score']}->{after['score']} cleared={after['lastCleared']}")
        ok("drag_message_is_not_failure", "没有形成消除" not in after["lastMessage"],
           after["lastMessage"])
        shots.append(capture("devsim_v03_after_drag.png"))

        # 回合预告
        eval_js("var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();"
                "g.DebugJumpToRound(1); g.DebugSetScore(99999); g.DebugAdvance(31f);"
                "return g.DebugState();")
        time.sleep(0.2)
        st = state()
        ok("preview_visible", st["phase"] == "Preview", st["phase"])
        shots.append(capture("devsim_v03_preview.png"))

        # 商店（每 3 轮）+ 售罄状态
        eval_js("var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();"
                "g.DebugJumpToRound(3); g.DebugSetScore(99999); g.DebugAdvance(31f);"
                "g.DebugSetCoins(600); return g.DebugState();")
        time.sleep(0.2)
        st = state()
        ok("shop_visible", st["phase"] == "Shop" and st["shopVisits"] == 1, st["phase"])
        shots.append(capture("devsim_v03_shop.png"))
        eval_js("var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();"
                "g.DebugBuy(0); return g.DebugState();")
        time.sleep(0.2)
        shots.append(capture("devsim_v03_shop_sold.png"))

        # BONUS：同色 6 个
        col2 = [(r * 2 + c) % 5 for r in range(6) for c in range(6)]
        for c in range(3):
            col2[0 * 6 + c] = 3
            col2[1 * 6 + c] = 3
        eval_js("var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();"
                "g.DebugSetBoard(6, new int[]{" + ",".join(map(str, col2)) + "},"
                "new int[]{" + ",".join(["1"] * 36) + "});"
                "return g.DebugResolveBoard();")
        time.sleep(0.3)
        st = state()
        ok("bonus_visible", st["phase"] == "Bonus",
           st["phase"] + " same=" + str(st["lastSameColorCount"]))
        shots.append(capture("devsim_v03_bonus.png"))

        # 道具拖拽：真实 ItemDrag 事件链（高亮 + 松手生效 + 消耗）
        eval_js("var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();"
                "g.DebugSetBoard(6, new int[]{" + ",".join(map(str, col2)) + "},"
                "new int[]{" + ",".join(["1"] * 36) + "});"
                "g.DebugGrantItem(0,1);"
                "var d = JsonUtility.FromJson<DevSim.ChromaGame.DebugDragDto>(g.DebugScreenPosOfCell(2,3));"
                "var slot = g.DebugItemSlot(0);"
                "var es = UnityEngine.EventSystems.EventSystem.current;"
                "var ped = new UnityEngine.EventSystems.PointerEventData(es);"
                "ped.button = UnityEngine.EventSystems.PointerEventData.InputButton.Left;"
                "ped.position = new Vector2(d.dx, d.dy);"
                "ped.delta = new Vector2(6f, 6f);"
                "UnityEngine.EventSystems.ExecuteEvents.Execute(slot, ped,"
                "UnityEngine.EventSystems.ExecuteEvents.beginDragHandler);"
                "UnityEngine.EventSystems.ExecuteEvents.Execute(slot, ped,"
                "UnityEngine.EventSystems.ExecuteEvents.dragHandler);"
                "return \"ok\";")
        time.sleep(0.2)
        st = state()
        ok("item_drag_starts", st["phase"] == "Playing" and st["inventory"][0] == 1,
           st["phase"] + " inv=" + str(st["inventory"][0]))
        shots.append(capture("devsim_v03_item_drag_highlight.png"))

        inv_before = state()["inventory"][0]
        eval_js("var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();"
                "var d = JsonUtility.FromJson<DevSim.ChromaGame.DebugDragDto>(g.DebugScreenPosOfCell(2,3));"
                "var slot = g.DebugItemSlot(0);"
                "var es = UnityEngine.EventSystems.EventSystem.current;"
                "var ped = new UnityEngine.EventSystems.PointerEventData(es);"
                "ped.button = UnityEngine.EventSystems.PointerEventData.InputButton.Left;"
                "ped.position = new Vector2(d.dx, d.dy);"
                "UnityEngine.EventSystems.ExecuteEvents.Execute(slot, ped,"
                "UnityEngine.EventSystems.ExecuteEvents.endDragHandler);"
                "return \"ok\";")
        time.sleep(1.2)
        st = state()
        ok("item_drag_drop_consumes_and_clears",
           st["inventory"][0] == inv_before - 1 and st["lastCleared"] > 0,
           "inv=" + str(st["inventory"][0]) + " cleared=" + str(st["lastCleared"]))
        shots.append(capture("devsim_v03_item_after_drop.png"))

        # 现场 bug 复现：倒计时在消除动效途中归零 + 该步触发 BONUS。
        # 期望：先等动效落完，只弹 BONUS；选完奖励后才进下一轮预告，不会两套遮罩死锁。
        col4 = [(r * 2 + c) % 5 for r in range(6) for c in range(6)]
        col4[3 * 6 + 0] = 2
        col4[3 * 6 + 1] = 2
        col4[3 * 6 + 2] = 1
        col4[3 * 6 + 3] = 2
        col4[2 * 6 + 2] = 2
        col4[4 * 6 + 2] = 2
        col4[5 * 6 + 2] = 2
        eval_js("var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();"
                "g.DebugSetBoard(6, new int[]{" + ",".join(map(str, col4)) + "},"
                "new int[]{" + ",".join(["1"] * 36) + "});"
                "g.DebugSetScore(99999); g.DebugSetTime(0.15f);"
                "var go = g.DebugTileObject(2,2);"
                "var es = UnityEngine.EventSystems.EventSystem.current;"
                "var ped = new UnityEngine.EventSystems.PointerEventData(es);"
                "ped.button = UnityEngine.EventSystems.PointerEventData.InputButton.Left;"
                "ped.position = new Vector2(Screen.width*0.5f, Screen.height*0.5f);"
                "ped.delta = new Vector2(0f, -45f);"
                "UnityEngine.EventSystems.ExecuteEvents.Execute(go,ped,"
                "UnityEngine.EventSystems.ExecuteEvents.beginDragHandler);"
                "UnityEngine.EventSystems.ExecuteEvents.Execute(go,ped,"
                "UnityEngine.EventSystems.ExecuteEvents.dragHandler);"
                "UnityEngine.EventSystems.ExecuteEvents.Execute(go,ped,"
                "UnityEngine.EventSystems.ExecuteEvents.dragHandler);"
                "UnityEngine.EventSystems.ExecuteEvents.Execute(go,ped,"
                "UnityEngine.EventSystems.ExecuteEvents.endDragHandler);"
                "return \"ok\";")
        time.sleep(2.6)
        st = state()
        overlays = sum(1 for k in ("previewOpen", "shopOpen", "bonusOpen", "overOpen") if st[k])
        ok("timeup_during_animation_bonus_no_deadlock",
           st["bonusOpen"] and overlays == 1 and not st["previewOpen"],
           "phase=" + st["phase"] + " overlays=" + str(overlays)
           + " preview=" + str(st["previewOpen"]) + " bonus=" + str(st["bonusOpen"]))
        shots.append(capture("devsim_v03_pending_bonus_only.png"))

        eval_js("var g = UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();"
                "return g.DebugChooseBonus(0);")
        time.sleep(0.4)
        st2 = state()
        overlays2 = sum(1 for k in ("previewOpen", "shopOpen", "bonusOpen", "overOpen") if st2[k])
        ok("bonus_then_preview_after_deferred_round_end",
           st2["previewOpen"] and overlays2 == 1 and not st2["bonusOpen"],
           "phase=" + st2["phase"] + " overlays=" + str(overlays2)
           + " round=" + str(st2["round"]))
        shots.append(capture("devsim_v03_after_bonus_preview.png"))

        OUT.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = OUT / f"devsim_ui_e2e_{ts}.json"
        report = {"ok": all(c["ok"] for c in checks), "checks": checks, "shots": shots}
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n==== devsim UI E2E: {sum(c['ok'] for c in checks)}/{len(checks)}；证据 {path}")
        return 0 if report["ok"] else 1
    finally:
        unity("command", "editor.playmode stop")
        try:
            tmp.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
