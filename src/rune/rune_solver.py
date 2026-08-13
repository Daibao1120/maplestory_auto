"""rune（天使符文）偵測與解謎。

經典版的 rune 解謎為「畫面出現四個方向鍵箭頭，依序按對應方向」。
本檔提供介面與流程骨架，實際的箭頭方向辨識以 TODO 佔位。
"""
from __future__ import annotations

from typing import List

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _CV_AVAILABLE = True
except ImportError:
    cv2 = None  # type: ignore
    np = None  # type: ignore
    _CV_AVAILABLE = False


class RuneDetector:
    """偵測畫面上是否出現待解的 rune（或需要解 rune 的提示）。"""

    def __init__(self, config=None):
        config = config or {}
        self.enabled = config.get("enabled", True)
        self.detect_interval = config.get("detect_interval", 5.0)

    def detect(self, frame):
        """回傳畫面上是否存在待解的 rune（bool）。

        TODO: 可用兩種線索之一：
              1) 小地圖上的 rune 標記（特定顏色）。
              2) 畫面中央出現「請解除封印」等 UI／方向箭頭框。
              目前回傳 False（尚未實作）。
        """
        return False


class RuneSolver:
    """解 rune：辨識四個方向箭頭並依序按下。"""

    # 方向 → 對應鍵位
    DIRECTION_KEYS = {"up": "up", "down": "down", "left": "left", "right": "right"}

    def recognize_arrows(self, frame):
        """辨識畫面中的方向箭頭序列，回傳如 ['up', 'left', 'down', 'right']。

        TODO: 常見作法為對每個箭頭 ROI 做模板匹配（四方向各一模板），
              或以方向梯度／輪廓判斷朝向。目前回傳空清單。
        """
        return []

    def solve(self, frame, controller):
        """解一次 rune：辨識箭頭 → 依序送鍵。回傳是否成功（bool）。"""
        arrows = self.recognize_arrows(frame)
        if not arrows:
            return False
        for direction in arrows:
            key = self.DIRECTION_KEYS.get(direction)
            if key:
                controller.tap(key)
        return True
