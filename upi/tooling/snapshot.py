"""attempt 级快照与回滚（M3，AGENTS I4 / G4 / G9 / DESIGN §4 回滚颗粒度）。

三条硬规矩在代码层的落点：
- G4 无快照禁写入：execute 前必须有 snapshot() 返回的句柄，且 HEAD 必须仍
  指向快照 tag（工作区停在快照点上），否则拒绝——防止"上一个 attempt 的
  残留被这次顺手的写入盖掉"。
- G9 回滚只依赖 git：回滚路径不调用任何 Editor 命令。改出编译错误时
  Editor 进 Safe Mode、Pipeline 包不加载，eval/command 全部连不上——
  那时唯一还通的路就是 git。回滚后再重启 Editor 验证（调用方职责）。
- G7 入快照的 asset_manifest_hash 与 scene 哈希：回滚后必须校验资产面复位
  （Unity 导入会重写 Library/.meta，光看 git 不够）。

范围纪律：脏区检测、clean 只盯**夹具工程路径**（runs/ 等产物目录本就是脏的，
不纳入判定也不被清除）。root 参数化，单测用临时 git 仓库即可跑通。
"""
from __future__ import annotations

import hashlib
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str, root: Path) -> str:
    p = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                       text=True, timeout=120, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {p.stdout[:150]}{p.stderr[:150]}")
    return p.stdout.strip()


def asset_manifest_hash(project_path: Path) -> str:
    """资产清单哈希 = 工程内 Assets/** 与 ProjectSettings/** 内容摘要。

    取舍（实测教训驱动）：Library/ 排除——导入过程会重写它，纳入则回滚后
    必然"哈希不等"，制造假阳性。"""
    h = hashlib.sha256()
    for sub in ("Assets", "ProjectSettings"):
        base = Path(project_path) / sub
        if not base.exists():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file():
                continue
            h.update(str(f.relative_to(base)).replace("\\", "/").encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def scene_guid_of(scene_path: Path) -> str:
    """场景内容哈希代用作 scene_guid（Unity 真 guid 需 Editor 查询；
    回滚校验用内容哈希同样成立且可离线——来源方式如实标注）。"""
    return "filehash:" + hashlib.sha256(Path(scene_path).read_bytes()).hexdigest()[:16]


@dataclass
class Snapshot:
    attempt_id: str
    git_ref: str            # tag 名
    head_before: str        # 快照时的 HEAD sha
    asset_manifest_hash: str
    scene_guid: str
    protocol_fingerprint: str
    created_at: str
    root: str = str(ROOT)   # 回滚在哪棵树上执行
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"attempt_id": self.attempt_id, "git_ref": self.git_ref,
                "head_before": self.head_before,
                "asset_manifest_hash": self.asset_manifest_hash,
                "scene_guid": self.scene_guid,
                "protocol_fingerprint": self.protocol_fingerprint,
                "created_at": self.created_at, "root": self.root,
                "metadata": self.metadata}


class SnapshotError(RuntimeError):
    pass


def workdir_state(project_path: Path, root: Path = ROOT) -> tuple[bool, list[str]]:
    """夹具工程路径内是否含未提交改动（产物目录不算）。"""
    rel = str(Path(project_path).relative_to(root)).replace("\\", "/")
    out = _git("status", "--porcelain", "--", rel, root=root)
    files = [ln.strip()[3:] for ln in out.splitlines() if ln.strip()]
    return (len(files) > 0), files


def snapshot(attempt_id: str, project_path: Path, scene_path: Path,
             protocol_fingerprint: str, metadata: Optional[dict] = None,
             root: Optional[Path] = None) -> Snapshot:
    """建立 attempt 快照：夹具路径必须干净（脏区先提交或中止——把残留带进
    快照等于把它焊进回滚目标）。快照 = 空提交 + tag upi/attempt-<id>。"""
    root = root or ROOT
    dirty, files = workdir_state(project_path, root)
    head = _git("rev-parse", "HEAD", root=root)
    if dirty:
        raise SnapshotError(
            f"夹具路径含未提交改动 {len(files)} 项（{files[:3]}…），拒绝建立快照——"
            "残留改动被写进回滚目标是 I4 不允许的")
    amh = asset_manifest_hash(project_path)
    sg = scene_guid_of(scene_path)
    tag = f"upi/attempt-{attempt_id}"
    # 空提交承载 tag 点位（树不变）——避免污染真实历史语义
    _git("commit", "--allow-empty", "-q", "-m",
         f"[upi-snapshot] attempt {attempt_id} head={head[:8]} amh={amh}", root=root)
    _git("tag", "-f", tag, root=root)
    return Snapshot(attempt_id=attempt_id, git_ref=tag, head_before=head,
                    asset_manifest_hash=amh, scene_guid=sg,
                    protocol_fingerprint=protocol_fingerprint,
                    created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                    root=str(root), metadata=metadata or {})


def pre_execute_check(snap: Snapshot, project_path: Optional[Path] = None) -> None:
    """G4 运行时形态：HEAD 仍指向快照 tag **且**夹具路径无未登记改动。
    只比 HEAD 不够——快照后新增未提交文件时 HEAD 不动（实测单测抓出），
    那正是"上一个 attempt 的残留被这次写入盖掉"的入口。"""
    root = Path(snap.root)
    head = _git("rev-parse", "HEAD", root=root)
    tagsha = _git("rev-parse", snap.git_ref, root=root)
    if head != tagsha:
        raise SnapshotError(
            f"HEAD({head[:8]}) 已不在快照 {snap.git_ref}({tagsha[:8]}) 上——"
            "快照后发生过未登记的写入或回滚，禁止继续（I4）")
    if project_path is not None:
        dirty, files = workdir_state(project_path, root)
        if dirty:
            raise SnapshotError(
                f"快照后夹具路径出现 {len(files)} 项未登记改动（{files[:3]}…）——"
                "禁止在其上继续写入（I4/G4）")


def rollback(snap: Snapshot, project_path: Path, scene_path: Path) -> dict:
    """G9：只依赖 git。reset --hard 到快照 tag + 只清夹具路径的未跟踪文件。
    返回回滚校验报告；哈希未复位就如实报 fail（不粉饰）。"""
    root = Path(snap.root)
    _git("reset", "--hard", snap.git_ref, root=root)
    rel = str(Path(project_path).relative_to(root)).replace("\\", "/")
    _git("clean", "-fd", "--", rel, root=root)
    amh_now = asset_manifest_hash(project_path)
    sg_now = scene_guid_of(scene_path)
    return {"rolled_back_to": snap.git_ref,
            "head_after": _git("rev-parse", "HEAD", root=root),
            "asset_manifest_restored": amh_now == snap.asset_manifest_hash,
            "scene_restored": sg_now == snap.scene_guid,
            "amh_expected": snap.asset_manifest_hash, "amh_after": amh_now,
            "editor_touched": False}   # G9：本函数从未调用任何 Editor 命令
