"""影像辨識層：模板匹配、小地圖定位、血條偵測、怪物偵測。"""
from src.vision.template_matcher import TemplateMatcher, Match
from src.vision.minimap import (MinimapLocator, PlayerTracker, Rect,
                                find_platform_run)
from src.vision.health_bar import HealthBarDetector
from src.vision.monster import MonsterDetector, Detection
from src.vision.edge_probe import probe_ahead_safe
from src.vision.synthetic import build_demo, DemoScene

__all__ = [
    "TemplateMatcher", "Match",
    "MinimapLocator", "PlayerTracker", "Rect", "find_platform_run",
    "HealthBarDetector",
    "MonsterDetector", "Detection",
    "probe_ahead_safe",
    "build_demo", "DemoScene",
]
