"""血條偵測：以角色頭上血條定位角色，並讀取 HP/MP 剩餘比例。"""
from __future__ import annotations

from typing import Optional

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _CV_AVAILABLE = True
except ImportError:
    cv2 = None  # type: ignore
    np = None  # type: ignore
    _CV_AVAILABLE = False


class HealthBarDetector:
    """血條偵測器。

    兩個用途：
    1. find_player()：以角色頭上血條顏色，在遊戲畫面中定位角色像素座標。
    2. read_hp_ratio()/read_mp_ratio()：讀取畫面左下角 HP/MP 條的剩餘比例。
    """

    def __init__(self, config=None):
        config = config or {}
        self.hp_lower = tuple(config.get("hp_color_lower", [0, 120, 120]))
        self.hp_upper = tuple(config.get("hp_color_upper", [10, 255, 255]))

    def _require_cv(self):
        if not _CV_AVAILABLE:
            raise RuntimeError("尚未安裝 opencv-python / numpy。請執行： pip install opencv-python numpy")

    def find_player(self, frame):
        """以血條顏色在畫面中定位角色，回傳 (x, y) 或 None。

        TODO: 目前以 HSV 顏色遮罩取重心作為粗略定位；
              實務上需再用血條形狀（細長矩形）過濾雜訊，並取血條正下方為角色位置。
        """
        self._require_cv()
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(self.hp_lower), np.array(self.hp_upper))
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return None
        return (int(xs.mean()), int(ys.mean()))

    def read_hp_ratio(self, frame):
        """讀取 HP 條剩餘比例 (0.0~1.0)。

        TODO: 依 settings 的 HP 條 ROI 與滿條顏色，計算填充長度比例。
              目前回傳 1.0（視為滿血）作為安全預設，避免誤觸補水。
        """
        return 1.0

    def read_mp_ratio(self, frame):
        """讀取 MP 條剩餘比例 (0.0~1.0)。TODO 同 read_hp_ratio，目前回傳 1.0。"""
        return 1.0
