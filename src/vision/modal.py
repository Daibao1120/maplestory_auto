"""置中彈窗偵測：測謊視窗、符文、死亡回城等「需要人處理」的對話框。

為什麼重要：測謊視窗跳出來沒答會被斷線甚至標記，是封號風險最高的一環。
目前只能靠「EXP 停滯 8 分鐘」間接推測，太慢；直接看畫面才來得及。

安全設計：本模組**只負責偵測，不自動作答**——偵測到就立刻停止所有輸入並
通知使用者本人處理。自動答題屬於明確的作弊行為，且答錯的代價比停手高得多。

判別特徵（實測校準）：遊戲 UI 面板是「低飽和 + 中高亮度」的灰白色系，與
世界的高飽和綠棕明顯不同。真正的彈窗還要「夠大」且「置中」——聊天框雖然
水平置中但垂直偏在底部（實測 centrality y=0.44），據此可區分。
"""
from __future__ import annotations

from typing import Optional, Tuple

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _CV_AVAILABLE = True
except ImportError:
    cv2 = None
    np = None
    _CV_AVAILABLE = False


def centrality(rect, frame_w, frame_h):
    """回傳 (水平, 垂直) 偏離畫面中心的比例（0=正中央、0.5=貼邊）。"""
    x, y, w, h = rect
    cx, cy = x + w / 2.0, y + h / 2.0
    return abs(cx - frame_w / 2.0) / frame_w, abs(cy - frame_h / 2.0) / frame_h


def rects_overlap(a, b, tol=0.35):
    """兩個矩形是否大致同一個（用於跨幀確認彈窗持續存在）。"""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if not (abs(aw - bw) <= max(aw, bw) * tol and abs(ah - bh) <= max(ah, bh) * tol):
        return False
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return union > 0 and inter / union >= 0.5


def find_modal(frame, min_area_frac=0.045, max_area_frac=0.55,
               max_cx=0.16, max_cy=0.20, sat_max=60, val_min=110):
    """找畫面正中央的大型 UI 面板，回傳 (x, y, w, h) 或 None。"""
    if not _CV_AVAILABLE or frame is None:
        return None
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    ui = ((hsv[:, :, 1] < sat_max) & (hsv[:, :, 2] > val_min)).astype(np.uint8)
    ui = cv2.morphologyEx(ui, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    num, _l, stats, _c = cv2.connectedComponentsWithStats(ui, connectivity=8)
    area_total = float(h * w)
    best = None
    for i in range(1, num):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        frac = area / area_total
        if frac < min_area_frac or frac > max_area_frac:
            continue
        if bw < w * 0.12 or bh < h * 0.10:
            continue
        cx, cy = centrality((x, y, bw, bh), w, h)
        if cx > max_cx or cy > max_cy:
            continue                     # 不夠置中（聊天框、側邊視窗等）
        if best is None or area > best[0]:
            best = (area, (x, y, bw, bh))
    return None if best is None else best[1]


class ModalWatcher:
    """跨幀確認：同一個彈窗連續出現 persist 次才算數（避免特效誤判）。"""

    def __init__(self, persist=3, **kw):
        self.persist = int(persist)
        self.kw = kw
        self._streak = 0
        self._rect: Optional[Tuple[int, int, int, int]] = None
        self.active: Optional[Tuple[int, int, int, int]] = None

    def update(self, frame):
        """回傳目前確認中的彈窗矩形，沒有則 None。"""
        r = find_modal(frame, **self.kw) if frame is not None else None
        if r is None:
            self._streak = 0
            self._rect = None
            self.active = None
            return None
        if self._rect is not None and rects_overlap(self._rect, r):
            self._streak += 1
        else:
            self._streak = 1
        self._rect = r
        self.active = r if self._streak >= self.persist else None
        return self.active
