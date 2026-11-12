"""I7f-b：任意使用者工程接入的代理验收（devsim + testlab 两工程切换）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from upi.config.project_profile import load_profile, reset_profile  # noqa: E402
from upi.config.project_registry import ProjectRegistry  # noqa: E402
from upi.core import events as E  # noqa: E402

DEVSIM = ROOT / "sim" / "project"
TESTLAB = ROOT / "testlab" / "upi-lab"
TMP = Path(tempfile.mkdtemp(prefix="upi_onboard_"))
E.isolate(TMP / "runs")
reset_profile()
checks: list[tuple[bool, str, str]] = []


def ok(name: str, cond: bool, detail="") -> None:
    checks.append((bool(cond), name, str(detail)[:300]))
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + ("" if cond else f" -> {str(detail)[:200]}"))


reg = ProjectRegistry(E.PROJECTS)
a = reg.add(project_path=DEVSIM, display_name="Chroma Rogue",
            default_scene="Assets/Scenes/00_Bootstrap.unity")
b = reg.add(project_path=TESTLAB, display_name="UPI Lab",
            default_scene="Assets/Scenes/Baseline.unity")
ok("两个任意工程可注册", a.project_id != b.project_id,
   (a.project_id, b.project_id))

reg.activate(a.project_id)
pa = load_profile(a.project_id)
ok("切到工程 A", reg.active_id() == a.project_id
   and pa.abs_project.resolve() == DEVSIM.resolve(), pa.abs_project)
ok("A 场景映射", pa.fix_subject == "00_Bootstrap", pa.fix_subject)

reg.activate(b.project_id)
pb = load_profile(b.project_id)
ok("切到工程 B", reg.active_id() == b.project_id
   and pb.abs_project.resolve() == TESTLAB.resolve(), pb.abs_project)
ok("B 场景映射", pb.fix_subject == "Baseline", pb.fix_subject)
ok("B 场景文件存在", pb.scene_of(pb.fix_subject).exists(),
   pb.scene_of(pb.fix_subject))

reg.activate(a.project_id)
ok("切回工程 A", load_profile(a.project_id).abs_project.resolve() == DEVSIM.resolve())
ok("最近工程顺序", [e.project_id for e in reg.recent()][:2]
   == [a.project_id, b.project_id], [e.project_id for e in reg.recent()])

bad = 0
for cond, name, detail in checks:
    if not cond:
        bad += 1
print(f"\n==== I7f-b 工程接入代理验收: {len(checks) - bad}/{len(checks)}")
raise SystemExit(1 if bad else 0)
