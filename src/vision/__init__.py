"""影像辨識層：模板匹配、小地圖定位、血條偵測、怪物偵測。"""
from src.vision.template_matcher import TemplateMatcher, Match
from src.vision.minimap import MinimapLocator, Rect
from src.vision.health_bar import HealthBarDetector
from src.vision.monster import MonsterDetector, Detection
from src.vision.synthetic import build_demo, DemoScene

__all__ = [
    "TemplateMatcher", "Match",
    "MinimapLocator", "Rect",
    "HealthBarDetector",
    "MonsterDetector", "Detection",
    "build_demo", "DemoScene",
]
