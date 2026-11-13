"""全自动考试编排（默认只打印计划；--execute 才真正接管）。

步骤：
  1. 退出 test_project 的 Unity（只操作该工程，不按名字杀进程）
  2. 清空习得数据（runs/projects/baselines/calibration/knowledge db）
  3. 启动考卷台，生成白纸考卷（c01–c11）
  4. 连通性检查
  5. 重建 test_project、init git、注册工程
  6. 打开 Unity、开场景、装探针、进 Play
  7. 跑 exam_live.py --baselines 3
  8. 输出得分/链路/标签统计

用法：
  python scripts/exam_full_run.py            # 只打印计划
  python scripts/exam_full_run.py --execute  # 真正执行
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "test_project"
EDITOR = Path(r"D:\unity_editor\6000.6.7f1\Editor\Unity.exe")
LAB = "http://127.0.0.1:8787"
CASES = [f"c{i:02d}" for i in range(1, 12)]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sh(cmd: list[str], *, timeout: int = 1800, check: bool = True,
       cwd: Path | None = None) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    p = subprocess.run(cmd, cwd=str(cwd or ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    if p.stdout.strip():
        log(p.stdout.strip()[-800:])
    if p.stderr.strip():
        log("stderr: " + p.stderr.strip()[-500:])
    if check and p.returncode != 0:
        raise SystemExit(f"命令失败（{p.returncode}）：{' '.join(cmd)}")
    return p


def unity(*args: str, timeout: int = 120) -> dict:
    p = subprocess.run(["unity", "command", *args,
                        "--project-path", str(PROJECT), "--format", "json"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"success": False, "raw": p.stdout[-300:]}


def wait_editor(timeout_s: int = 900) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if unity("editor status", timeout=30).get("success"):
            log("Unity 就绪")
            return
        time.sleep(4)
    raise SystemExit("Unity 启动超时")


def ensure_lab() -> None:
    try:
        urllib.request.urlopen(LAB + "/", timeout=3)
        log("考卷台已运行")
        return
    except Exception:
        log("考卷台未运行，启动中")
    subprocess.Popen([sys.executable, str(ROOT / "scripts" / "i8_lab.py"),
                      "--port", "8787"], cwd=str(ROOT),
                     creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    for _ in range(40):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(LAB + "/", timeout=2)
            log("考卷台就绪")
            return
        except Exception:
            continue
    raise SystemExit("考卷台启动失败")


def post(path: str, body: dict, timeout: int = 300) -> dict:
    req = urllib.request.Request(
        LAB + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_json(path: str, timeout: int = 60) -> dict:
    with urllib.request.urlopen(LAB + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def clean_learned() -> None:
    for name in ("runs", "projects", "baselines", "calibration"):
        d = ROOT / name
        if d.exists():
            for child in d.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except OSError:
                        pass
    for db in (ROOT / "knowledge").glob("*.db"):
        try:
            db.unlink()
        except OSError:
            pass
    sh(["git", "checkout", "--", "knowledge"], check=False)
    log("习得数据已清空")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true",
                    help="真正执行；不加只打印计划")
    a = ap.parse_args()
    log("计划：")
    for line in [
        "退出 test_project 的 Unity",
        "清空 runs/projects/baselines/calibration/knowledge db",
        "启动考卷台并生成 c01–c11 白纸考卷",
        "LLM 连通性检查",
        "重建 test_project / git init / 注册工程",
        f"打开 Unity（{EDITOR}）并等待就绪",
        "打开 00_Bootstrap 场景、装探针、进 Play",
        "运行 exam_live.py --baselines 3",
        "输出得分、链路、待审标签",
    ]:
        log("  - " + line)
    if not a.execute:
        log("未执行。确认后加 --execute。")
        return 0

    # 1) 退出该工程编辑器
    log("步骤 1/8：退出 test_project 的 Unity")
    unity("editor.playmode stop", timeout=60)
    time.sleep(1)
    unity("editor quit", timeout=60)
    time.sleep(6)

    # 2) 清空习得数据 + 删工程
    log("步骤 2/8：清空习得数据与白纸工程")
    clean_learned()
    if PROJECT.exists():
        shutil.rmtree(PROJECT, ignore_errors=True)
    if PROJECT.exists():
        raise SystemExit("test_project 删除失败，停止（不触碰其它文件）")

    # 3) 考卷台 + 生成考卷
    log("步骤 3/8：启动考卷台并生成考卷")
    ensure_lab()
    r = post("/api/paper", {"cases": CASES}, timeout=300)
    if not r.get("ok"):
        raise SystemExit("生成考卷失败：" + str(r)[:300])
    log("考卷已生成")

    # 4) 连通性
    log("步骤 4/8：LLM 连通性")
    sys.path.insert(0, str(ROOT))
    from upi.config.connectivity import run_check
    from upi.config.settings import load_config
    cr = run_check(load_config(ROOT / "llm_config.yaml"),
                   "deepseek-official", "deepseek-flash")
    if not cr.get("all_passed"):
        raise SystemExit("连通性未通过：" + str(cr)[:300])
    log("连通性通过")

    # 5) git / 注册
    log("步骤 5/8：重建工程元数据并注册")
    (PROJECT / ".gitignore").write_text(
        "Library/\nTemp/\nLogs/\nUserSettings/\nobj/\n.vs/\n", encoding="utf-8")
    sh(["git", "-C", str(PROJECT), "init"])
    sh(["git", "-C", str(PROJECT), "add", "-A"])
    sh(["git", "-C", str(PROJECT), "-c", "user.email=exam@local",
        "-c", "user.name=exam", "commit", "-m", "baseline"])
    from upi.config.project_registry import ProjectRegistry
    reg = ProjectRegistry()
    e = reg.add(project_path=str(PROJECT), display_name="Paper",
                default_scene="Assets/Scenes/00_Bootstrap.unity")
    reg.activate(e.project_id)
    log(f"工程已注册：{e.project_id}")

    # 6) Unity
    log("步骤 6/8：打开 Unity")
    subprocess.Popen(["unity", "open", str(PROJECT), "-e", str(EDITOR),
                      "--format", "json", "--no-banner"], cwd=str(ROOT),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_editor()
    log("打开场景")
    sc = unity("scene open", "--path", "Assets/Scenes/00_Bootstrap.unity")
    if not sc.get("success"):
        raise SystemExit("场景打开失败：" + str(sc)[:200])
    log("安装探针")
    from upi.tooling import probe_control as PC
    pi = PC.install(PROJECT)
    if not pi.get("ok"):
        raise SystemExit("探针安装失败：" + str(pi)[:200])
    log("进入 Play")
    if not unity("editor.playmode start").get("success"):
        raise SystemExit("进入 Play 失败")
    time.sleep(4)

    # 7) 考试
    log("步骤 7/8：运行考试（3 基线）")
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "exam_live.py"),
                        "--replay", "runs/i8_lab/paper_replay.json",
                        "--project", "test_project",
                        "--project-id", "test_project",
                        "--baselines", "3"],
                       cwd=str(ROOT), text=True, encoding="utf-8",
                       errors="replace")
    if p.returncode != 0:
        raise SystemExit(f"考试失败（{p.returncode}）")

    # 8) 结果
    log("步骤 8/8：读取结果")
    try:
        score = get_json("/api/score", timeout=60)
        print(json.dumps({
            "run": (score.get("run") or {}).get("run_id"),
            "dimensions": score.get("dimensions"),
            "chain": score.get("chain"),
        }, ensure_ascii=False, indent=2)[:4000])
    except Exception as e:  # noqa: BLE001
        log(f"读取考卷台失败：{e}")
    log("完成。可打开 " + LAB + " 查看链路与标签。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
