"""I21：按 module:Class 加载项目适配器；核心不反向依赖具体项目。"""
from __future__ import annotations

import importlib
from pathlib import Path

from upi.core.project_adapter import ProjectAdapter


def load_adapter(spec: str, project: Path | str) -> ProjectAdapter:
    if ":" not in spec:
        raise ValueError("adapter 必须是 module:Class")
    module_name, cls_name = spec.split(":", 1)
    mod = importlib.import_module(module_name)
    cls = getattr(mod, cls_name)
    adapter = cls(project)
    if not isinstance(adapter, ProjectAdapter):
        raise TypeError(f"{spec} 未实现 ProjectAdapter 协议")
    return adapter
