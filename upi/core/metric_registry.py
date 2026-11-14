"""I21：可配置指标注册表。核心不内置项目指标别名。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class MetricSpec:
    id: str
    unit: str = ""
    aliases: tuple[str, ...] = ()
    aggregation: str = "median"
    higher_is_better: bool = False
    guard: bool = True
    source: str = "counters"


@dataclass
class MetricRegistry:
    specs: dict[str, MetricSpec] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str) -> "MetricRegistry":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        reg = cls()
        for item in data.get("metrics") or []:
            spec = MetricSpec(
                id=str(item["id"]),
                unit=str(item.get("unit", "")),
                aliases=tuple(str(x) for x in item.get("aliases") or []),
                aggregation=str(item.get("aggregation", "median")),
                higher_is_better=bool(item.get("higher_is_better", False)),
                guard=bool(item.get("guard", True)),
                source=str(item.get("source", "counters")),
            )
            reg.specs[spec.id] = spec
        return reg

    def get(self, metric: str) -> Optional[MetricSpec]:
        return self.specs.get(metric)

    def require(self, metric: str) -> MetricSpec:
        spec = self.get(metric)
        if spec is None:
            raise KeyError(f"未注册指标：{metric}")
        return spec

    def aliases(self, metric: str) -> tuple[str, ...]:
        spec = self.get(metric)
        return (spec.aliases if spec and spec.aliases else (metric,))

    def series(self, data: dict, metric: str) -> list[float]:
        counters = data.get("counters") if isinstance(data, dict) else None
        if not isinstance(counters, dict):
            return []
        for name in self.aliases(metric):
            node = counters.get(name)
            if isinstance(node, dict):
                vals = node.get("series")
                if isinstance(vals, list) and vals:
                    return [float(x) for x in vals]
        return []


_DEFAULT: Optional[MetricRegistry] = None


def default_registry() -> MetricRegistry:
    global _DEFAULT
    if _DEFAULT is None:
        root = Path(__file__).resolve().parents[2]
        _DEFAULT = MetricRegistry.load(root / "config" / "metrics.json")
    return _DEFAULT
