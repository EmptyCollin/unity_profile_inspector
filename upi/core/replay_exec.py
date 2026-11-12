"""项目 replay 执行器：把命令序列映射到运行中的 Unity 项目。"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from upi.core.replay_spec import ReplayCommand, ReplaySpec

SUPPORTED = {"start_game", "load_scene", "swap", "wait", "open_shop", "stop"}


class UnsupportedCommand(ValueError):
    pass


class ReplayExecutor:
    def __init__(self, project: Path | str = "sim/project"):
        self.project = str(Path(project).resolve())

    def _unity(self, *args: str) -> dict:
        p = subprocess.run(["unity", "command", *args, "--project-path", self.project,
                            "--format", "json"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        try:
            d = json.loads(p.stdout)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"unity output not json: {p.stdout[-400:]}") from e
        if not d.get("success"):
            raise RuntimeError(json.dumps(d.get("errors") or d, ensure_ascii=False)[:500])
        return d

    def eval(self, code: str, timeout: int = 60) -> Any:
        d = self._unity("script eval", "--code", code, "--timeout", str(timeout))
        r = ((d.get("data") or {}).get("result") or {})
        if not r.get("success"):
            raise RuntimeError(json.dumps(r.get("errors") or r, ensure_ascii=False)[:500])
        return r.get("result")

    def _game(self) -> str:
        return ('var g=UnityEngine.Object.FindFirstObjectByType<DevSim.ChromaGame>();'
                'if(g==null) throw new System.Exception("ChromaGame not found");')

    def start_game(self) -> Any:
        cfg = json.dumps({"seed": 20261112, "autoplay": False,
                          "round_seconds": 30}, ensure_ascii=False)
        cfg_js = cfg.replace("\\", "\\\\").replace('"', '\\"')
        code = (self._game() +
                f'g.DebugLoadConfig("{cfg_js}"); g.DebugStartRun();'
                'return g.DebugState();')
        return self.eval(code)

    def swap(self, fr: tuple[int, int], to: tuple[int, int]) -> Any:
        code = (self._game() +
                f'return g.DebugSwap({fr[0]},{fr[1]},{to[0]},{to[1]});')
        return self.eval(code)

    def open_shop(self) -> Any:
        code = (self._game() +
                'var m=g.GetType().GetMethod("OpenShop",'
                'System.Reflection.BindingFlags.NonPublic|'
                'System.Reflection.BindingFlags.Instance);'
                'm.Invoke(g,null); return g.DebugState();')
        return self.eval(code)

    def load_scene(self, scene: str) -> None:
        self._unity("scene", "open", "--path", scene)

    def run(self, spec: ReplaySpec) -> list[dict]:
        out: list[dict] = []
        for cmd in spec.commands:
            out.append(self._one(cmd))
        return out

    def _one(self, cmd: ReplayCommand) -> dict:
        if cmd.type not in SUPPORTED:
            raise UnsupportedCommand(cmd.type)
        if cmd.type == "wait":
            time.sleep(float(cmd.seconds or 0))
            return {"type": "wait", "seconds": cmd.seconds}
        if cmd.type == "stop":
            return {"type": "stop"}
        if cmd.type == "load_scene":
            self.load_scene(cmd.scene or "")
            return {"type": "load_scene", "scene": cmd.scene}
        if cmd.type == "start_game":
            return {"type": "start_game", "state": self.start_game()}
        if cmd.type == "swap":
            return {"type": "swap", "state": self.swap(cmd.from_cell, cmd.to_cell)}
        if cmd.type == "open_shop":
            return {"type": "open_shop", "state": self.open_shop()}
        raise UnsupportedCommand(cmd.type)
