"""角色螢幕定位：用「角色名牌」當錨點。

為什麼需要：鏡頭貼到地圖邊界時會被夾住，角色**不在畫面中央**（實測偏移
達 56×182 px）。若沿用「中央＝角色」的假設，左右判斷與同高度帶過濾全錯，
會導致「偵測得到怪卻一隻都不可打」。

名牌是 UI 文字、每幀渲染一致，實測模板匹配分數 1.00、位置零抖動；且名字
獨一無二，不會跟其他玩家/寵物的名牌混淆。找不到名牌時回傳 None，呼叫端
可退回畫面中央（並自行決定要不要因此停手）。
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _CV_AVAILABLE = True
except ImportError:
    cv2 = None
    np = None
    _CV_AVAILABLE = False


def search_window(prev, frame_w, frame_h, radius=420):
    """回傳這一幀要搜尋的區域 (x1, y1, x2, y2)。

    有上次位置就只搜它周圍（快很多）；沒有就搜全幀。純函式，可測試。
    """
    if prev is None:
        return 0, 0, frame_w, frame_h
    px, py = prev
    x1 = max(0, int(px) - radius)
    y1 = max(0, int(py) - radius)
    x2 = min(frame_w, int(px) + radius)
    y2 = min(frame_h, int(py) + radius)
    if x2 - x1 < 40 or y2 - y1 < 40:          # 退化 → 搜全幀
        return 0, 0, frame_w, frame_h
    return x1, y1, x2, y2


class PlayerAnchor:
    """以名牌模板定位角色（回傳角色「腳底中心」的螢幕座標）。

    參數：
        template_path: 名牌截圖（用 tools/capture_player_tag.py 產生）。
        feet_offset_y: 名牌上緣往上幾 px 視為腳底（實測 6）。
        threshold: 匹配門檻。
    """

    def __init__(self, template_path=None, feet_offset_y=6, threshold=0.75,
                 search_radius=420, part_threshold=0.70):
        self.template_path = template_path
        self.feet_offset_y = int(feet_offset_y)
        self.threshold = float(threshold)
        self.part_threshold = float(part_threshold)
        self.search_radius = int(search_radius)
        self._tmpl = None
        self._parts = []          # [(模板, 該模板左緣→名牌中心的 dx, 門檻)]
        self._prev: Optional[Tuple[int, int]] = None
        self.last_score = 0.0
        self.last_part = ""

    def load(self):
        if self._tmpl is not None or not _CV_AVAILABLE:
            return self._tmpl is not None
        p = self.template_path
        if not p or not os.path.exists(p):
            return False
        img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return False
        self._tmpl = img
        # 抗遮擋：名牌常被鄰近玩家的名牌壓住一半（實機發生過），所以除了
        # 完整名牌，也保留左半／右半當備援——任一半配上就能定位。
        h, w = img.shape[:2]
        half = max(8, w // 2)
        self._parts = [
            ("full", img, w / 2.0, self.threshold),
            ("left", img[:, :half], w / 2.0, self.part_threshold),
            ("right", img[:, w - half:], w - half - (w - half) + w / 2.0 - (w - half),
             self.part_threshold),
        ]
        # 右半：匹配到的 x 是「名牌左緣 + (w-half)」→ 中心 dx = w/2 - (w-half)
        self._parts[2] = ("right", img[:, w - half:], w / 2.0 - (w - half),
                          self.part_threshold)
        return True

    @property
    def available(self):
        return self.load()

    def find(self, frame):
        """回傳角色腳底 (x, y)；找不到回傳 None。

        依序試「完整名牌 → 左半 → 右半」，任一過門檻即採用（抗部分遮擋）。
        """
        if frame is None or not self.load():
            return None
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = search_window(self._prev, w, h, self.search_radius)
        region = frame[y1:y2, x1:x2]
        best = None
        for name, tmpl, center_dx, thr in self._parts:
            th, tw = tmpl.shape[:2]
            if region.shape[0] <= th or region.shape[1] <= tw:
                continue
            res = cv2.matchTemplate(region, tmpl, cv2.TM_CCOEFF_NORMED)
            _mn, mx, _ml, ml = cv2.minMaxLoc(res)
            if mx >= thr and (best is None or mx > best[1]):
                best = (name, float(mx), ml, center_dx)
            if name == "full" and mx >= thr:
                break                        # 完整名牌命中就不必試半邊
        if best is None:
            self.last_score = 0.0
            if self._prev is not None:       # 局部搜尋失敗 → 全幀再試一次
                self._prev = None
                return self.find(frame)
            self.last_part = ""
            return None
        name, score, ml, center_dx = best
        self.last_score, self.last_part = score, name
        cx = int(x1 + ml[0] + center_dx)
        top = y1 + ml[1]
        self._prev = (cx, top)
        return (cx, top - self.feet_offset_y)
