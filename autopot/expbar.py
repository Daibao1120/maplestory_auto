"""讀 EXP 條的百分比、找「自動狩獵」按鈕（純影像處理，可單獨測試）。

EXP 條的位置不用校正：HP 條是畫面最下方最長的一段紅色，
同一列往右依序是 HP、MP、EXP 三個白框，第三個框就是 EXP。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .vision import Match

BTN_TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "autohunt_btn.png"
BTN_REF_WIDTH = 1278          # 按鈕模板是在「遊戲畫面寬 1278」的視窗上剪的


def _classes(row: np.ndarray) -> np.ndarray:
    """把一列像素分類：W 白框 / R 紅 / B 藍 / Y 黃(EXP) / . 暗 / ? 其他。"""
    p = row.astype(np.int16)
    b, g, r = p[:, 0], p[:, 1], p[:, 2]
    mx, mn = p.max(axis=1), p.min(axis=1)
    out = np.full(len(p), "?", dtype="<U1")
    out[mx < 90] = "."
    out[(r > 140) & (g > 140) & (np.minimum(r, g) - b > 60)] = "Y"     # EXP 填色（小視窗是偏白的黃）
    out[(b > 150) & (r < 100)] = "B"
    out[(r > 170) & (g < 90) & (b < 90)] = "R"
    out[(mn > 180) & (mx - mn < 50)] = "W"
    return out


def find_bars(img: np.ndarray):
    """回傳 (y, [(x0, x1), ...])：狀態列中線的 y，以及 HP、MP、EXP 三個框的內部範圍。找不到回傳 None。"""
    H, W = img.shape[:2]
    p = img.astype(np.int16)
    red = (p[..., 2] > 170) & (p[..., 1] < 90) & (p[..., 0] < 90)
    red[: int(H * 0.75)] = False
    rows = red.sum(axis=1)
    y = int(np.argmax(rows))
    if rows[y] < W * 0.01:
        return None
    cls = _classes(img[y])
    xs = np.where(cls == "R")[0]
    # HP 條：紅色最長的一段
    best, start = (0, 0), None
    for i in range(len(cls) + 1):
        isr = i < len(cls) and cls[i] == "R"
        if isr and start is None:
            start = i
        elif not isr and start is not None:
            if i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    if not len(xs):
        return None
    # 從 HP 開始往右，用白框切出一個個框（白框旁邊最多容許 2px 的反鋸齒雜色）
    minw = max(20, int(W * 0.035))
    bars, x, inside_start = [], best[0], best[0]
    while x < W and len(bars) < 3:
        if cls[x] == "W":
            if x - inside_start >= minw:
                bars.append((inside_start, x))
            while x < W and cls[x] == "W":
                x += 1
            k = 0
            while x < W and k < 2 and cls[x] == "?":
                x += 1
                k += 1
            inside_start = x
            continue
        x += 1
    if len(bars) < 3:
        return None
    return y, bars[:3]


# EXP 條的填色圖比看得到的框內部寬：左右各有一小段藏在白框下面。
# 實測（1280x720 視窗，框內部 153px）：4.57% → 填 4px、75.57% → 填 117px
#   → 每邊藏約 2.7px（= 框寬 x 0.0177），比例 = (填色 + 藏的) / (框寬 + 兩邊藏的)
# 家裡 2764x1555（框內部 325px）：36.61% → 116px，用同一個比例算出 36.2%（誤差 0.4%）
HIDDEN_RATIO = 0.0177


def fill_to_ratio(n: int, w: int) -> float:
    h = HIDDEN_RATIO * w
    return float(min(1.0, max(0.0, (n + h) / (w + 2 * h)))) if n > 0 else 0.0


def ratio_to_fill(r: float, w: int) -> int:
    """反過來：比例 → 看得到的填色像素（給測試畫假畫面用）。"""
    h = HIDDEN_RATIO * w
    return max(0, min(w, int(round(r * (w + 2 * h) - h))))


def exp_framed(img: np.ndarray, y: int, x0: int, x1: int, reach: int = 50) -> bool:
    """EXP 框的上緣或下緣有白色框線嗎？（遊戲被別的視窗蓋住、畫面變黑時就沒有）"""
    H = img.shape[0]
    for yy in list(range(y - 1, max(-1, y - reach), -1)) + list(range(y + 1, min(H, y + reach))):
        row = img[yy, x0 + 3:x1 - 3].astype(np.int16)
        if row.size == 0:
            continue
        mn, mx = row.min(axis=1), row.max(axis=1)
        if ((mn > 180) & (mx - mn < 50)).mean() > 0.8:
            return True
    return False


def exp_ratio(img: np.ndarray, bars=None) -> float | None:
    """EXP 條填滿的比例（0~1）；找不到 EXP 條（或被擋住）回傳 None。"""
    bars = bars or find_bars(img)
    if not bars:
        return None
    y, (_, (m0, m1), (x0, x1)) = bars
    if (x1 - x0) < 0.9 * (m1 - m0):      # EXP 框被滑鼠游標等東西切斷了 → 這次不算
        return None
    if y >= img.shape[0] or not exp_framed(img, y, x0, x1):
        return None
    n = _fill_px(img, y, x0, x1)
    if n is None:
        return None
    return fill_to_ratio(n, x1 - x0)


def _white_row(img, yy, x0, x1) -> bool:
    row = img[yy, x0 + 3:x1 - 3].astype(np.int16)
    if row.size == 0:
        return False
    mn, mx = row.min(axis=1), row.max(axis=1)
    return bool(((mn > 180) & (mx - mn < 50)).mean() > 0.8)


def box_rows(img: np.ndarray, y: int, x0: int, x1: int, reach: int = 50) -> tuple[int, int]:
    """EXP 框內部的上下範圍 [top, bottom)：從 y 往上、往下找到白色框線為止。
    上框線找不到（例如被說明框蓋住）就從 y 開始。"""
    H = img.shape[0]
    top = y
    for yy in range(y - 1, max(-1, y - reach), -1):
        if _white_row(img, yy, x0, x1):
            top = yy + 1
            break
    bottom = min(H, y + reach)
    for yy in range(y + 1, min(H, y + reach)):
        if _white_row(img, yy, x0, x1):
            bottom = yy
            break
    return top, bottom


def _fill_px(img: np.ndarray, y: int, x0: int, x1: int):
    """EXP 條填了幾個像素。

    實測（公司電腦 1280x720）：HP 最紅的那一列剛好在條的上緣，緊鄰白色亮線，
    只看那附近 3 列會把「空的部分」誤判成有填（讀成 100%）。
    所以看整個框內部的每一列，取中位數。"""
    top, bottom = box_rows(img, y, x0, x1)
    lens = []
    w = x1 - x0
    for yy in range(top, bottom):
        cls = _classes(img[yy, x0:x1])
        if (cls == "W").mean() > 0.2:          # 亮線
            continue
        dark = cls == "."
        n = w
        run = 0
        for i, d in enumerate(dark):           # 第一段連續 3px 以上的暗色 = 空的部分開始
            run = run + 1 if d else 0
            if run >= 3:
                n = i - 2
                break
        else:
            if run:                            # 最右邊只剩 1~2px 是暗的（快滿了）
                n = w - run
        lens.append(n)
    if len(lens) < 3:
        return None
    return int(np.median(lens))


def find_autohunt_button(img: np.ndarray, tpl: np.ndarray | None = None, threshold: float = 0.75) -> Match | None:
    """找左上角的「自動狩獵」按鈕（依視窗大小縮放模板）。"""
    if tpl is None:
        tpl = cv2.imread(str(BTN_TEMPLATE))
    H, W = img.shape[:2]
    search = img[: int(H * 0.6), : int(W * 0.5)]
    base = W / BTN_REF_WIDTH
    best = None
    for k in np.linspace(0.85, 1.15, 13):
        s = base * k
        t = cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        if t.shape[0] >= search.shape[0] or t.shape[1] >= search.shape[1]:
            continue
        r = cv2.matchTemplate(search, t, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(r)
        if best is None or mv > best.score:
            best = Match(ml[0], ml[1], t.shape[1], t.shape[0], float(mv))
    if best is None or best.score < threshold:
        return None
    return best
