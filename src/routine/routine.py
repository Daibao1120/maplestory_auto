"""routine（路線）資料模型與載入器。

一條 routine 由多個 Point 組成；抵達每個 Point 後，依序執行其 commands
（動作指令，對應 command book）。支援從 YAML 載入（見 config/routines/example.yaml）。

parse_routine() 不需要 PyYAML，可直接以 dict 建立 Routine（方便測試）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
    _YAML_AVAILABLE = True
except ImportError:
    yaml = None  # type: ignore
    _YAML_AVAILABLE = False


@dataclass
class Point:
    """路線上的一個定位點。"""
    label: str
    x: int
    y: int
    commands: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Routine:
    """一條完整路線。"""
    name: str = "unnamed"
    map: str = ""
    points: List[Point] = field(default_factory=list)
    options: Dict[str, Any] = field(default_factory=dict)

    def label_index(self, label):
        """依 label 找到對應點的索引；找不到回傳 None。"""
        for i, p in enumerate(self.points):
            if p.label == label:
                return i
        return None


def parse_routine(data):
    """把 dict（通常來自 YAML）轉成 Routine 物件。"""
    data = data or {}
    points = [
        Point(
            label=p.get("label", f"P{i}"),
            x=int(p.get("x", 0)),
            y=int(p.get("y", 0)),
            commands=p.get("commands", []),
        )
        for i, p in enumerate(data.get("points", []))
    ]
    return Routine(
        name=data.get("name", "unnamed"),
        map=data.get("map", ""),
        points=points,
        options=data.get("options", {}),
    )


def load_routine(path):
    """從 YAML 檔載入 routine。"""
    if not _YAML_AVAILABLE:
        raise RuntimeError("尚未安裝 PyYAML。請執行： pip install PyYAML")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return parse_routine(data)
