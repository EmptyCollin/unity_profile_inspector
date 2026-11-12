"""I7b 工程注册表：新增/校验/激活/归档/删除/文件落点。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.config import project_profile as PP       # noqa: E402
from upi.config.project_registry import ProjectRegistry  # noqa: E402
from upi.core import events as E                   # noqa: E402

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="upi_registry_"))
E.isolate(TMP / "runs")


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))


def make_project(name="MyGame"):
    p = TMP / name
    (p / "Assets" / "Scenes").mkdir(parents=True)
    (p / "ProjectSettings").mkdir()
    (p / ".git").mkdir()
    (p / "Assets" / "Scenes" / "Main.unity").write_text("%YAML 1.1\n", encoding="utf-8")
    (p / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 6000.6.7f1\n", encoding="utf-8")
    return p


reg = ProjectRegistry()
p = make_project()
e = reg.add(project_path=p, display_name="My Game",
            default_scene="Assets/Scenes/Main.unity")
ok("新增工程", e.project_id == "my-game" and e.validation.ok, e.validation)
ok("读 Unity 版本", e.unity_version == "6000.6.7f1", e.unity_version)
ok("registry 落盘", (TMP / "projects" / "registry.json").is_file())

e2 = reg.add(project_path=make_project("Other"), display_name="My Game")
ok("同名 id 自动去重", e2.project_id != e.project_id, e2.project_id)

try:
    reg.add(project_path=TMP / "missing", display_name="Missing")
    ok("不存在路径拒绝", False)
except ValueError:
    ok("不存在路径拒绝", True)

e3 = reg.activate(e.project_id)
ok("激活工程", reg.active_id() == e.project_id and e3.validation.ok)
ok("激活指针落 registry 根", (TMP / "projects" / ".active").read_text().strip() == e.project_id)

e4 = reg.update(e.project_id, display_name="My Game 2", default_scene="Assets/Scenes/Main.unity")
ok("更新显示名", e4.display_name == "My Game 2")

e5 = reg.archive(e.project_id)
ok("归档", e5.status == "archived" and reg.active_id() is None)
try:
    reg.activate(e.project_id)
    ok("归档不能激活", False)
except ValueError:
    ok("归档不能激活", True)

try:
    reg.delete(e.project_id, force=False)
    ok("有历史/无历史删除路径可走", reg.get(e.project_id) is None)
except ValueError:
    ok("有历史则拒绝删除", True)

# 无 history 的工程可删
reg2 = ProjectRegistry()
e6 = reg2.add(project_path=make_project("Deletable"), display_name="Deletable")
reg2.delete(e6.project_id, force=True)
ok("强制删除", reg2.get(e6.project_id) is None)

# registry 条目可被 load_profile 解析（同一隔离根）
PP.reset_profile()
p2 = PP.load_profile(e2.project_id)
ok("load_profile 从 registry 解析", p2.profile_id == e2.project_id
   and p2.abs_project == e2.project_path.resolve())

print(f"\n==== 工程注册表测试: {len(PASS)} 通过, {len(FAIL)} 失败")
raise SystemExit(1 if FAIL else 0)
