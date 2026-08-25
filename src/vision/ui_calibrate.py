"""UI 自動校準：在任何解析度下找出 HP/MP 條與小地圖面板。

為什麼需要：遊戲世界會隨解析度縮放，但 UI 不會等比縮放（實測 2736→3840 時
血條、小地圖在畫面中的相對位置與大小都變了）。硬寫座標必然失效，所以改成
每次啟動自動找。全部是純函式或小型類別，可用合成影像測試。
"""
from __future__ import annotations

from typing import Optional, Tuple

try:
    import numpy as _np
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


def _neutral_after(frame, y, x, n=8, tol=12, v_min=150, skip=2, need=0.75):
    """條的右側緊接著是不是「中性灰的空槽」。

    這是分辨真血條與同色 UI 按鈕的關鍵。血條畫在一個灰色凹槽裡，沒填滿的部分
    露出 (204,204,204) 這種三通道相等的灰；按鈕右邊則是圖示或邊框，通道明顯
    不相等（實測購物商場按鈕右側是 (221,204,187)）。
    """
    h, w = frame.shape[:2]
    x += skip          # 緊鄰條尾的一兩個像素常是邊緣振鈴，實測 (194,194,230)
    if x + n > w or not (0 <= y < h):
        return False
    px = frame[y, x:x + n].astype(int)
    b, g, r = px[:, 0], px[:, 1], px[:, 2]
    spread = _np.maximum(_np.maximum(abs(b - g), abs(g - r)), abs(b - r))
    good = (spread <= tol) & (px.min(axis=1) >= v_min)
    return bool(good.mean() >= need)     # 多數即可，容忍零星雜點


def find_bar(frame, color="red", bottom_frac=0.88, min_frac=0.02):
    """在畫面下緣找 HP(red)/MP(blue) 條，回傳 dict(x, y, len) 或 None。

    優先選「右側接著中性灰空槽」的那一段；沒有符合的才退回最長段。

    為什麼不能只取最長：實測 164 幀，血量 24% 時真血條長 49px，而底部 UI 的
    購物商場紅色按鈕也剛好是 49px，掃描順序讓按鈕先贏。後果是 HP 讀值恆為
    1.000（按鈕永遠「滿」），血量保護與 HP_LOST_LIMIT 從此不可能觸發——
    整條安全鏈默默失效，而且每一幀都回報「找到血條」，看起來完全正常。
    """
    if not _CV_AVAILABLE or frame is None:
        return None
    h, w = frame.shape[:2]
    y0 = int(h * bottom_frac)
    m = _color_mask(frame[y0:], color)
    lim = int(w * min_frac)
    best = (0, 0, 0)
    troughed = (0, 0, 0)
    for y in range(m.shape[0]):
        row = m[y].copy()
        while True:
            L, st = longest_run(row)
            if L < max(1, lim):
                break
            if L > best[0]:
                best = (L, st, y)
            if L > troughed[0] and _neutral_after(frame, y0 + y, st + L):
                troughed = (L, st, y)
            row[st:st + L] = False
    L, st, y = troughed if troughed[0] else best
    if L < lim:
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
        # 外框也要有上限。上面看的是「連通區的像素數」，而玩家開啟的視窗只有
        # 邊框是深色 → 像素數很少卻能撐出巨大的外框。實測回傳過 (0,0,820,558)
        # ——整個左上象限，含使用者開著的勳章視窗。用那個 ROI 去找玩家點會鎖在
        # 視窗裡的一個靜態圖示上，座標 164 幀完全不動，於是 _pos_miss 永遠不累加、
        # 永遠不重新校準，platform_span 恆為 None → 45 秒後進 IDLE_SAFE 卡到天亮。
        if bw * bh > frame_area * max_area_frac:
            continue
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
    if frame is None or (not mp and not hp):
        return None
    h, w = frame.shape[:2]
    if not mp:
        # MP 條是靠藍色填充段找的，MP 見底時就找不到——但 EXP 區的位置不會因為
        # MP 少了就跑掉。三條 UI 等距排列，用 HP 的幾何往右推兩格即可。
        # 沒有這個後備時，MP 一低就等於整個進帳偵測失效（EXP ROI 退化成 1x1，
        # 永遠讀不到變化），反而觸發「EXP 停滯」的假警報。
        gap = max(2, int(w * 0.004))
        mp = {"x": hp["x"] + hp["len"] + gap, "y": hp["y"], "len": hp["len"]}
    gap = 6
    if hp:
        gap = max(2, mp["x"] - (hp["x"] + hp["len"]))
    x = mp["x"] + mp["len"] + gap
    if x >= w - 10:
        return None
    th = max(14, int(h * text_h_frac))
    y = max(0, mp["y"] - th - int(h * 0.004))
    return (x, y, min(mp["len"] + int(w * 0.012), w - x), th + int(h * 0.004))


def find_bars_pair(frame, bottom_frac=0.85, max_gap_frac=0.15, min_ratio=0.08):
    """成對找 HP/MP 條，回傳 (hp, mp)。

    只找「最長藍色段」會被聊天視窗等藍色 UI 騙（實測抓到 x=276 的聊天列），
    所以先鎖較獨特的紅色 HP，再用**位置**關係找 MP：同一列（±4px）、緊接在
    HP 右側一段距離內、取最長的一段。

    重點：不可用「長度比例」當條件——條的長度會隨當前值變動。實測 MP 只剩
    56% 時長度僅 HP 的 0.56 倍，舊的 0.6~1.6 倍限制會直接把正確的 MP 條拒絕掉，
    連帶讓整個 UI 校準失敗。實際滿值由 BarReader 在執行期自我學習。
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
    hp_end = hp["x"] + hp["len"]
    limit = hp_end + int(w * max_gap_frac)
    best = None
    for yy in range(m.shape[0]):
        row = m[yy].copy()
        row[:hp_end] = False                      # 只看 HP 條右側
        row[limit:] = False                       # 太遠的藍色 UI 不是 MP 條
        L, st = longest_run(row)
        if L >= max(6, int(hp["len"] * min_ratio)):
            if best is None or L > best[0]:
                best = (L, st, y0 + yy)
    if best is None:
        return hp, None
    return hp, {"x": int(best[1]), "y": int(best[2]), "len": int(best[0])}
