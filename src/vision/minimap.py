"""小地圖定位：在小地圖上找出角色（黃點）與其他標記。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _CV_AVAILABLE = True
except ImportError:
    cv2 = None  # type: ignore
    np = None  # type: ignore
    _CV_AVAILABLE = False


@dataclass
class Rect:
    left: int
    top: int
    width: int
    height: int


class MinimapLocator:
    """小地圖定位器。

    參數 config 對應 settings.yaml 的 vision.minimap 區塊（HSV 顏色範圍）。
    小地圖座標系是 routine 走位的依據。
    """

    def __init__(self, config=None):
        config = config or {}
        self.player_lower = tuple(config.get("player_color_lower", [24, 180, 180]))
        self.player_upper = tuple(config.get("player_color_upper", [40, 255, 255]))
        self.other_lower = tuple(config.get("other_color_lower", [0, 0, 200]))
        self.other_upper = tuple(config.get("other_color_upper", [180, 40, 255]))
        self._minimap_rect: Optional[Rect] = None

    def _require_cv(self):
        if not _CV_AVAILABLE:
            raise RuntimeError("尚未安裝 opencv-python / numpy。請執行： pip install opencv-python numpy")

    def locate_minimap(self, frame):
        """在整張畫面中找出小地圖區域，回傳 Rect 或 None。

        TODO: 以模板匹配（小地圖左上角圖示）或邊框偵測定位。目前回傳快取值，
              代表呼叫端可先自行裁切好小地圖影像再傳入 locate_player()。
        """
        return self._minimap_rect

    def _find_color(self, minimap_bgr, lower, upper):
        """回傳指定 HSV 顏色範圍的重心座標 (x, y)；找不到回傳 None。"""
        self._require_cv()
        hsv = cv2.cvtColor(minimap_bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(lower), np.array(upper))
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return None
        return (int(xs.mean()), int(ys.mean()))

    def locate_player(self, minimap_bgr):
        """回傳角色在小地圖上的座標 (x, y)；找不到回傳 None。"""
        return self._find_color(minimap_bgr, self.player_lower, self.player_upper)

    def locate_others(self, minimap_bgr):
        """回傳其他玩家標記的重心座標（供判斷是否有人、換頻決策）。

        TODO: 改為回傳多個座標清單；目前僅回傳單一重心作為示意。
        """
        return self._find_color(minimap_bgr, self.other_lower, self.other_upper)
