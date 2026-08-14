"""小地圖定位：在小地圖上找出角色（黃點）與其他標記。

演算法：把（可選的）小地圖 ROI 從整張畫面裁切出來 → HSV inRange 取出玩家色遮罩
→ 取「最大連通區（connected component）」的質心當作玩家座標。
HSV 範圍、ROI、最小色塊面積都可由 settings 設定。回傳的是「小地圖 ROI 內」的
相對像素座標，與 routine 使用的座標系一致。cv2/numpy 缺席或找不到時回傳 None。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _CV_AVAILABLE = True
except ImportError:
    cv2 = None
    np = None
    _CV_AVAILABLE = False


@dataclass
class Rect:
    left: int
    top: int
    width: int
    height: int


class PlayerTracker:
    """跨圈追蹤小地圖玩家點，過濾「座標亂跳」的誤判（純邏輯，不需 cv2）。

    小地圖上可能同時有多個玩家色色塊（其他黃色標記、閃爍殘影），單看
    「最大塊」偶爾會抓到別的東西：座標瞬間跳走 → 巡邏／換平台朝錯的方向
    走 → 掉出平台。策略與 tools/hold_and_wiggle.py 的 edge-guard 相同：

    - 有上次位置：挑「最接近上次位置」的候選；跳超過 max_jump_px（掛機
      不可能瞬移）就當誤判丟棄，這一圈回報 None（引擎會原地等待）。
    - 連續 miss／丟棄滿 reacquire_misses 圈：當作真的被移動了（死亡回城、
      被拉走），改取最大塊重新定位。
    """

    def __init__(self, max_jump_px=20, reacquire_misses=10):
        self.max_jump_px = int(max_jump_px)
        self.reacquire_misses = int(reacquire_misses)
        self.last = None      # 最近一次確認的玩家座標 (x, y)
        self.misses = 0       # 連續沒有可信候選的圈數

    def reset(self):
        """忘掉目前追蹤的位置，下一圈以最大塊重新定位。

        引擎的「靜止看門狗」用：若送了移動鍵但座標多圈完全不動，很可能
        鎖到靜止的黃色標記（追到後距離永遠 0、永遠贏），reset 是唯一出口。
        """
        self.last = None
        self.misses = 0

    @staticmethod
    def _largest(candidates):
        return max(candidates, key=lambda b: b[2])  # 不信任呼叫端的排序

    def update(self, candidates):
        """candidates: [(x, y, area), ...] → 玩家 (x, y) 或 None。"""
        if not candidates:
            self.misses += 1
            if self.misses >= self.reacquire_misses:
                self.last = None  # 太久沒看到 → 放棄舊位置，下次重新定位
            return None
        if self.last is None:
            bx, by, _area = self._largest(candidates)
            self.last = (bx, by)
            self.misses = 0
            return self.last
        lx, ly = self.last
        bx, by, _area = min(candidates,
                            key=lambda b: (b[0] - lx) ** 2 + (b[1] - ly) ** 2)
        if abs(bx - lx) <= self.max_jump_px and abs(by - ly) <= self.max_jump_px:
            self.last = (bx, by)
            self.misses = 0
            return self.last
        self.misses += 1
        if self.misses >= self.reacquire_misses:
            # 連續丟棄太多圈：位置可能真的變了 → 取最大塊重新定位
            bx, by, _area = self._largest(candidates)
            self.last = (bx, by)
            self.misses = 0
            return self.last
        return None


class MinimapLocator:
    """小地圖定位器。

    參數 config 對應 settings.yaml 的 vision.minimap 區塊：
        roi:                       小地圖在整張畫面中的 [left, top, width, height]；
                                   None = 傳入的影像本身就是小地圖。
        player_color_lower/upper:  玩家黃點的 HSV 範圍。
        min_blob_area:             小於此面積(px)的色塊視為雜訊忽略。
        other_color_lower/upper:   其他玩家標記的顏色範圍。
    """

    def __init__(self, config=None):
        config = config or {}
        self.roi = config.get("roi")  # [left, top, width, height] 或 None
        self.player_lower = tuple(config.get("player_color_lower", [24, 180, 180]))
        self.player_upper = tuple(config.get("player_color_upper", [40, 255, 255]))
        self.other_lower = tuple(config.get("other_color_lower", [0, 0, 200]))
        self.other_upper = tuple(config.get("other_color_upper", [180, 40, 255]))
        self.min_blob_area = int(config.get("min_blob_area", 2))

    # ---- 對外 ----
    def locate_minimap(self, frame):
        """回傳小地圖區域 Rect；未設定 roi 時回傳 None（代表整張輸入即小地圖）。"""
        return Rect(*self.roi) if self.roi else None

    def locate_player(self, frame):
        """回傳玩家在小地圖(ROI)內的座標 (x, y)；找不到或缺套件回傳 None。"""
        if not _CV_AVAILABLE or np is None or frame is None:
            return None
        region = self._crop_roi(frame)
        if region.size == 0:
            return None
        mask = self._mask(region, self.player_lower, self.player_upper)
        return self._largest_blob_centroid(mask)

    def locate_player_candidates(self, frame):
        """回傳所有「玩家色」色塊 [(x, y, area), ...]（小地圖 ROI 座標，面積大到小）。

        小地圖上可能有多個黃色標記（玩家、任務、傳點…），`locate_player` 只回最大塊，
        有時會挑錯。呼叫端可用這份清單自行挑「最接近上次位置」的塊，並過濾誤判。
        缺套件 / 找不到回空 list。
        """
        if not _CV_AVAILABLE or np is None or frame is None:
            return []
        region = self._crop_roi(frame)
        if region.size == 0:
            return []
        mask = self._mask(region, self.player_lower, self.player_upper)
        num, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        out = []
        for lbl in range(1, num):  # 跳過背景
            area = int(stats[lbl, cv2.CC_STAT_AREA])
            if area >= self.min_blob_area:
                cx, cy = centroids[lbl]
                out.append((int(round(cx)), int(round(cy)), area))
        return sorted(out, key=lambda b: -b[2])

    def locate_others(self, frame):
        """回傳其他玩家標記的概略重心 (x, y)；找不到回傳 None。"""
        if not _CV_AVAILABLE or np is None or frame is None:
            return None
        region = self._crop_roi(frame)
        if region.size == 0:
            return None
        mask = self._mask(region, self.other_lower, self.other_upper)
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return None
        return (int(xs.mean()), int(ys.mean()))

    # ---- 內部 ----
    def _crop_roi(self, frame):
        if not self.roi:
            return frame
        l, t, w, h = (int(v) for v in self.roi)
        l, t = max(0, l), max(0, t)
        return frame[t:t + h, l:l + w]

    def _mask(self, region, lower, upper):
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        return cv2.inRange(hsv, np.array(lower, dtype=np.uint8), np.array(upper, dtype=np.uint8))

    def _largest_blob_centroid(self, mask):
        """取遮罩中面積最大的連通區質心；面積不足 min_blob_area 回傳 None。"""
        num, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if num <= 1:  # 只有背景（label 0）
            return None
        best_label, best_area = 0, 0
        for lbl in range(1, num):  # 跳過背景
            area = int(stats[lbl, cv2.CC_STAT_AREA])
            if area > best_area:
                best_area, best_label = area, lbl
        if best_area < self.min_blob_area:
            return None
        cx, cy = centroids[best_label]
        return (int(round(cx)), int(round(cy)))
