"""血條偵測：讀取 HP／MP 條的剩餘比例，並（選用）以血條顏色定位角色。

read_hp_ratio / read_mp_ratio 演算法：
    取 HP／MP 條的 ROI → HSV inRange 取出「填充色」遮罩 →
    以「有填充的欄數 / 條總寬度」估算剩餘百分比 (0.0–1.0)。
ROI 座標與顏色門檻皆可由 settings 設定；未設定 ROI 或缺套件時回傳 1.0
（安全預設：視為滿，避免誤觸喝水）。
"""
from __future__ import annotations

from typing import Optional

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _CV_AVAILABLE = True
except ImportError:
    cv2 = None
    np = None
    _CV_AVAILABLE = False


class HealthBarDetector:
    """HP／MP 條偵測器。

    參數 config 對應 settings.yaml 的 vision.health_bar 區塊：
        hp_bar_roi / mp_bar_roi:   [left, top, width, height]
        hp_color_lower/upper:      HP 條填充色 HSV 範圍（預設紅）
        mp_color_lower/upper:      MP 條填充色 HSV 範圍（預設藍）
    """

    def __init__(self, config=None):
        config = config or {}
        self.hp_roi = config.get("hp_bar_roi")
        self.mp_roi = config.get("mp_bar_roi")
        self.hp_lower = tuple(config.get("hp_color_lower", [0, 120, 120]))
        self.hp_upper = tuple(config.get("hp_color_upper", [10, 255, 255]))
        self.mp_lower = tuple(config.get("mp_color_lower", [100, 120, 120]))
        self.mp_upper = tuple(config.get("mp_color_upper", [130, 255, 255]))

    def read_hp_ratio(self, frame):
        """讀取 HP 剩餘比例 (0.0–1.0)；未設定 ROI 或缺套件回傳 1.0。"""
        return self._bar_ratio(frame, self.hp_roi, self.hp_lower, self.hp_upper)

    def read_mp_ratio(self, frame):
        """讀取 MP 剩餘比例 (0.0–1.0)；未設定 ROI 或缺套件回傳 1.0。"""
        return self._bar_ratio(frame, self.mp_roi, self.mp_lower, self.mp_upper)

    def find_player(self, frame):
        """（選用）以 HP 填充色在畫面中取重心，粗略定位角色；找不到回傳 None。

        注意：畫面左下的 HP 條也是同色，實務上請傳入已裁切的角色區域，
        或改用 minimap 定位；此函式主要用於進階校準。
        """
        if not _CV_AVAILABLE or np is None or frame is None:
            return None
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(self.hp_lower, dtype=np.uint8),
                           np.array(self.hp_upper, dtype=np.uint8))
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return None
        return (int(xs.mean()), int(ys.mean()))

    # ---- 內部 ----
    def _bar_ratio(self, frame, roi, lower, upper):
        if not _CV_AVAILABLE or np is None or frame is None or not roi:
            return 1.0
        l, t, w, h = (int(v) for v in roi)
        if w <= 0 or h <= 0:
            return 1.0
        crop = frame[max(0, t):t + h, max(0, l):l + w]
        if crop.size == 0:
            return 1.0
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(lower, dtype=np.uint8), np.array(upper, dtype=np.uint8))
        # 「有填充的欄數 / 條總寬度」→ 剩餘比例
        filled_cols = int(np.count_nonzero(mask.any(axis=0)))
        return max(0.0, min(1.0, filled_cols / float(w)))
