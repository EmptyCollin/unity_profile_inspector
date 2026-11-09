"""候选根因检索（第 3 步前置，AGENTS §1.3 Diagnose）。

分工：本模块只**生成候选**（root_cause 描述 + 代码锚点），判定与打分归
factor_judge + attribution 公式——生成与判定分离，防自问自答（M2 收口
时定下的纪律，factor_judge 同样不生成候选）。

方法（= C+ 扫描器的种子，D14 前 3 模式的代码侧最小实现）：
  注册表 anchor_patterns（机制级）× 夹具源码文本特征 → 候选列表。
  特征匹配只定位"机制是否存在、在哪一行"，不做任何严重性判断。

签名纪律（§8.3）：root_cause 用机制级模板；code_anchors 允许具体
  文件:行——它是给 Fix Agent 与人看的落点，不进三重签名。
"""
from __future__ import annotations

import re
from pathlib import Path

from upi.config.project_profile import active_profile
from upi.core.detector import Detector


# 机制 anchor → 文本特征。每项 = (正则, 机制级根因模板)
# 只收录"存在即候选"的硬特征；语义与阈值判断留给判定链。
PATTERNS: dict[str, list[tuple[str, str]]] = {
    "per_frame_heap_alloc_in_hot_path": [
        (r"new\s+List<", "热路径每帧新建 List（可复用缓冲）"),
        (r"new\s+Dictionary<", "热路径每帧新建 Dictionary（可复用+Clear）"),
    ],
    "linq_enumerator_closure_per_frame": [
        (r"\.Where\(", "每帧 LINQ 链（枚举器+闭包分配）"),
    ],
    "repeated_getcomponent_in_update": [
        (r"GetComponent<", "每帧 GetComponent 重复查找（可 Awake 缓存）"),
    ],
    "enum_tostring_concat_per_frame": [
        (r'"\s*:\s*"\s*\+\s*\w+\.state', "枚举 ToString+字符串拼接每帧执行（可预生成表）"),
    ],
    "per_renderer_material_instantiation": [
        (r"\.material\s*=", "按渲染器实例化材质（破坏共享，可共享材质+MPB）"),
        (r"new Material\(", "运行时 new Material（材质实例膨胀）"),
    ],
    "runtime_material_property_write": [
        (r"material\.Set", "每帧材质属性写入（触发实例化，可 MPB）"),
    ],
    "sync_resources_load_in_start": [
        (r"Resources\.Load<", "Start 同步全量加载（可分帧异步）"),
    ],
    "resident_buffer_without_release": [
        (r"new byte\[[^\]]+\]", "大块常驻缓冲无释放路径"),
    ],
    "cross_layer_state_query_per_frame": [
        (r"GetCurrentAnimatorStateInfo\(", "每帧跨层状态查询（可缓存分层）"),
    ],
    "param_write_without_equal_check": [
        (r"\.SetBool\(|\.SetFloat\(|\.SetInteger\(|\.ResetTrigger\(",
         "动画参数无判等全量写入（可写入前判等）"),
    ],
}

_UPDATE_BLOCK = re.compile(r"private\s+void\s+Update\s*\(\)", re.M)
_START_BLOCK = re.compile(r"private\s+void\s+Start\s*\(\)", re.M)


def _fixture_dir_of(det: Detector) -> Path | None:
    """det.fixture（如 F-CPU-01）→ 对应资产目录。CLEAN 变体无目录=无候选，合法。

    阶段 7：目录根与"机制编号→目录名"的转换风格都由 profile 给出
    （原写法 FIXTURE_ASSETS / name.replace("-","_") 把工程位置和下划线
    约定一起钉死在代码里；接一个用别的命名风格的工程就得改这里）。
    """
    d = active_profile().subject_asset_dir(det.fixture)
    return d if d.is_dir() else None


# "每帧"类 anchor：命中行必须在 Update/Start 方法声明之后才算候选热路径
# （种子级粗粒度：不做方法边界闭区间识别——那需要真 AST，M3.5 完整版再做。
#  如实标注：Update 之后的成员方法/字段行会误入候选，判定链靠证据收紧）
_HOT_PATH_ANCHORS = {"per_frame_heap_alloc_in_hot_path",
                     "linq_enumerator_closure_per_frame",
                     "repeated_getcomponent_in_update",
                     "enum_tostring_concat_per_frame",
                     "cross_layer_state_query_per_frame",
                     "param_write_without_equal_check",
                     "sync_resources_load_in_start"}


def _hot_floor(lines: list[str]) -> int:
    for i, ln in enumerate(lines):
        if _UPDATE_BLOCK.search(ln) or _START_BLOCK.search(ln):
            return i
    return 10 ** 9      # 无 Update/Start 声明 ⇒ 热路径类特征全部不收（保守）


def retrieve_candidates(issue, det: Detector) -> list[tuple[str, list[str]]]:
    """Issue × Detector → 候选根因 [(root_cause, [anchor...])]。

    root_cause 带机制级描述；code_anchors=文件:行（供 Fix 与人看，
    不进三重签名，§8.3）。同特征同文件合成一条，锚点上限 5（防刷）。
    """
    fdir = _fixture_dir_of(det)
    if fdir is None:
        return []
    out: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for anchor in det.anchor_patterns:
        for rx, tmpl in PATTERNS.get(anchor, []):
            pat = re.compile(rx)
            for cs in sorted(fdir.rglob("*.cs")):
                if "Editor" in cs.parts:
                    continue  # 构建器/编辑器脚本不是运行时热路径
                lines = cs.read_text(encoding="utf-8", errors="replace").splitlines()
                floor = _hot_floor(lines) if anchor in _HOT_PATH_ANCHORS else 0
                hits = [i for i, ln in enumerate(lines)
                        if pat.search(ln) and i >= floor]
                rc_key = f"{tmpl}@{cs.name}"
                if not hits or rc_key in seen:
                    continue
                seen.add(rc_key)
                # ⚠️ 基准是档案的 git_root（阶段 7 可移植性实测撞出）：
                #   工程不在仓库内时 relative_to(ROOT) 直接 ValueError，
                #   表现为"接入外部工程后候选检索整条崩"。与 diff 的 file 字段、
                #   snapshot(root=)、apply_diffs(root=) 共用同一个基准，
                #   锚点写法才能和工单里"要改哪个文件:哪一行"对得上。
                rel = str(cs.relative_to(
                    Path(str(active_profile().git_root)))).replace("\\", "/")
                anchors = [f"anchor:{rel}:{i+1}" for i in hits[:5]]
                out.append((f"{tmpl}（机制 anchor: {anchor}）", anchors))
    return out
