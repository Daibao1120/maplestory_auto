"""影像辨識層：模板匹配、小地圖定位、血條偵測、怪物偵測。"""
from src.vision.template_matcher import TemplateMatcher, Match
from src.vision.minimap import (MinimapLocator, PlayerTracker, Rect,
                                find_platform_run)
from src.vision.health_bar import HealthBarDetector
from src.vision.monster import MonsterDetector, Detection
from src.vision.edge_probe import probe_ahead_safe
from src.vision.player_anchor import PlayerAnchor, search_window
from src.vision.ui_calibrate import (find_bar, find_minimap_rect, longest_run,
                                     BarReader, find_exp_bar, exp_text_roi,
                                     exp_text_roi_from_bars,
                                     find_bars_pair)
from src.vision.motion import MotionDetector, split_activity
from src.vision.synthetic import build_demo, DemoScene

__all__ = [
    "TemplateMatcher", "Match",
    "MinimapLocator", "PlayerTracker", "Rect", "find_platform_run",
    "HealthBarDetector",
    "MonsterDetector", "Detection",
    "probe_ahead_safe",
    "PlayerAnchor", "search_window",
    "find_bar", "find_minimap_rect", "longest_run", "BarReader",
    "find_exp_bar", "exp_text_roi", "exp_text_roi_from_bars", "find_bars_pair",
    "MotionDetector", "split_activity",
    "build_demo", "DemoScene",
]
