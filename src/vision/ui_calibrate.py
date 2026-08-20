"""UI 自動校準：在任何解析度下找出 HP/MP 條與小地圖面板。

為什麼需要：遊戲世界會隨解析度縮放，但 UI 不會等比縮放（實測 2736→3840 時
血條、小地圖在畫面中的相對位置與大小都變了）。硬寫座標必然失效，所以改成
每次啟動自動找。全部是純函式或小型類別，可用合成影像測試。
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


def longest_run(row) -> Tuple[int, int]:
    """回傳一維布林序列中最長連續 True 的 (長度, 起點)。"""
    best_len = best_start = 0
    n = start = 0
    for i, v in enumerate(row):
        if v:
            if n == 0:
                start = i
            n += 1
            if n > best_len:
                best_len, best_start = n, start
        else:
            n = 0
    return best_len, best_start


def _color_mask(strip, color):
    b = strip[:, :, 0].astype(int)
    g = strip[:, :, 1].astype(int)
    r = strip[:, :, 2].astype(int)
    if color == "red":
        return (r > 140) & (r - g > 50) & (r - b > 50)
    return (b > 140) & (b - r > 40) & (b - g > 20)


def find_bar(frame, color="red", bottom_frac=0.88, min_frac=0.02):
    """在畫面下緣找 HP(red)/MP(blue) 條，回傳 dict(x, y, len) 或 None。

    取最長的一段連續色塊——長條 UI 在畫面下方是最顯著的水平色帶。
    """
    if not _CV_AVAILABLE or frame is None:
        return None
    h, w = frame.shape[:2]
    y0 = int(h * bottom_frac)
    m = _color_mask(frame[y0:], color)
    best = (0, 0, 0)
    for y in range(m.shape[0]):
        L, st = longest_run(m[y])
        if L > best[0]:
            best = (L, st, y)
    L, st, y = best
    if L < int(w * min_frac):
        return None
    return {"x": int(st), "y": int(y0 + y), "len": int(L)}


def find_minimap_rect(frame, quad_w=0.30, quad_h=0.35, max_area_frac=0.05):
    """找小地圖面板（左上角、深色地形塊），回傳 (x, y, w, h) 或 None。

    只在左上角象限找；排除過大的區塊——玩家開啟的技能欄/裝備欄等深色視窗
    面積遠大於小地圖（實測誤判成 1152×756），用面積上限與位置限制濾掉。
    """
    if not _CV_AVAILABLE or frame is None:
        return None
    h, w = frame.shape[:2]
    qh, qw = int(h * quad_h), int(w * quad_w)
    q = frame[0:qh, 0:qw]
    hsv = cv2.cvtColor(q, cv2.COLOR_BGR2HSV)
    dark = (hsv[:, :, 2] < 130).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    num, _l, stats, _c = cv2.connectedComponentsWithStats(dark, connectivity=8)
    frame_area = h * w
    best = None
    for i in range(1, num):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        # 面積上限以「整張畫面」為基準：小地圖實測僅佔 0.6%，玩家開的技能欄
        # 等深色視窗佔 20%+，用象限面積當基準會把小地圖本身也濾掉。
        if area > frame_area * max_area_frac:
            continue                       # 太大 → 是開啟的 UI 視窗，不是小地圖
        if not (bw > w * 0.02 and bh > h * 0.02):
            continue
        if not (0.3 < bw / max(1, bh) < 8):
            continue
        if x > w * 0.10 or y > h * 0.20:   # 小地圖固定貼左上角
            continue
        if best is None or area > best[0]:
            best = (area, x, y, bw, bh)
    return None if best is None else (best[1], best[2], best[3], best[4])


class BarReader:
    """讀 HP/MP 百分比；自動記住看過的最長長度當作「滿值」。

    不必知道遊戲解析度或條的實際寬度——用執行期觀察到的最大值自我校準。
    """

    def __init__(self, color="red"):
        self.color = color
        self.rect: Optional[dict] = None
        self.full = 0

    def calibrate(self, frame):
        r = find_bar(frame, self.color)
        if r:
            self.rect = r
            self.full = max(self.full, r["len"])
        return r is not None

    def read(self, frame):
        """回傳 0~1 的比例；尚未校準或讀不到回 None。"""
        if not _CV_AVAILABLE or frame is None or not self.rect:
            return None
        x, y, _L = self.rect["x"], self.rect["y"], self.rect["len"]
        h = frame.shape[0]
        y1, y2 = max(0, y - 4), min(h, y + 5)
        # 搜尋寬度給足（條會因升等變長）；HP=紅、MP=藍互斥，不會互相汙染
        span = max(1, int(self.full * 2.5)) if self.full else frame.shape[1]
        band = frame[y1:y2, x:min(frame.shape[1], x + span)]
        if band.size == 0:
            return None
        m = _color_mask(band, self.color)
        L, _st = longest_run(m.any(axis=0))
        if L > self.full:
            self.full = L                  # 條變長（換角色/升等）→ 更新滿值
        return min(1.0, L / float(self.full)) if self.full else None


def _yellow_mask(strip):
    b = strip[:, :, 0].astype(int)
    g = strip[:, :, 1].astype(int)
    r = strip[:, :, 2].astype(int)
    return (r > 130) & (g > 110) & (b < 120)


def find_exp_bar(frame, bottom_frac=0.88, min_frac=0.02):
    """找 EXP 條（黃綠色長條），回傳 dict(x, y, len) 或 None。"""
    if not _CV_AVAILABLE or frame is None:
        return None
    h, w = frame.shape[:2]
    y0 = int(h * bottom_frac)
    m = _yellow_mask(frame[y0:])
    best = (0, 0, 0)
    for y in range(m.shape[0]):
        L, st = longest_run(m[y])
        if L > best[0]:
            best = (L, st, y)
    L, st, y = best
    if L < int(w * min_frac):
        return None
    return {"x": int(st), "y": int(y0 + y), "len": int(L)}


def exp_text_roi(frame, exp_bar, width_frac=0.055, height=26):
    """EXP 百分比文字區（在 EXP 條右側）——用來偵測「有沒有進帳」。

    回傳 (x, y, w, h)；沒有 exp_bar 時回 None。
    """
    if not exp_bar or frame is None:
        return None
    h, w = frame.shape[:2]
    x = min(w - 4, exp_bar["x"] + exp_bar["len"] + int(w * 0.004))
    y = max(0, exp_bar["y"] - height // 2)
    return (x, y, min(int(w * width_frac), w - x), min(height, h - y))


def exp_text_roi_from_bars(frame, hp, mp, text_h_frac=0.013):
    """由 HP/MP 兩條的幾何推算 EXP 百分比文字區（偵測「有沒有進帳」用）。

    底部三條 UI 等距排列（HP→MP→EXP），HP/MP 用顏色可靠偵測，EXP 條顏色
    會隨填充比例變動不好認——所以用間距外推，比猜顏色穩。
    """
    if frame is None or not mp:
        return None
    h, w = frame.shape[:2]
    gap = 6
    if hp:
        gap = max(2, mp["x"] - (hp["x"] + hp["len"]))
    x = mp["x"] + mp["len"] + gap
    if x >= w - 10:
        return None
    th = max(14, int(h * text_h_frac))
    y = max(0, mp["y"] - th - int(h * 0.004))
    return (x, y, min(mp["len"] + int(w * 0.012), w - x), th + int(h * 0.004))


def find_bars_pair(frame, bottom_frac=0.85):
    """成對找 HP/MP 條，回傳 (hp, mp)。

    只找「最長藍色段」會被聊天視窗等藍色 UI 騙（實測抓到 x=276 的聊天列）。
    HP 紅色在畫面中較獨特，先鎖 HP，再要求 MP：同一列（±4px）、在 HP 右邊、
    長度相近（0.6~1.6 倍）。這組幾何約束在實機穩定。
    """
    if not _CV_AVAILABLE or frame is None:
        return None, None
    hp = find_bar(frame, "red", bottom_frac=bottom_frac)
    if not hp:
        return None, None
    h, w = frame.shape[:2]
    y0 = max(0, hp["y"] - 4)
    band = frame[y0:min(h, hp["y"] + 5)]
    m = _color_mask(band, "blue")
    best = None
    for yy in range(m.shape[0]):
        row = m[yy].copy()
        row[:hp["x"] + hp["len"]] = False        # 只看 HP 條右側
        L, st = longest_run(row)
        if L and 0.6 * hp["len"] <= L <= 1.6 * hp["len"]:
            if best is None or L > best[0]:
                best = (L, st, y0 + yy)
    if best is None:
        return hp, None
    return hp, {"x": int(best[1]), "y": int(best[2]), "len": int(best[0])}
