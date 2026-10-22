"""純影像處理（不碰滑鼠鍵盤），方便單獨測試。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class Match:
    x: int          # 左上角（相對於搜尋圖）
    y: int
    w: int
    h: int
    score: float

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


def load_template(path: str | Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"找不到模板圖：{path}")
    return img


def crop(img: np.ndarray, rect) -> np.ndarray:
    x, y, w, h = [int(v) for v in rect]
    return img[max(0, y):y + h, max(0, x):x + w]


def _ink(img: np.ndarray) -> np.ndarray:
    """只留深色筆畫（文字、圖示外框）。清單列被選取時底色會變橘色，用這個就不受影響。"""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return ((g < 100) * 255).astype(np.uint8)


def _score_map(img: np.ndarray, tpl: np.ndarray, robust: bool) -> np.ndarray:
    r = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    if robust:
        r2 = cv2.matchTemplate(_ink(img), _ink(tpl), cv2.TM_CCOEFF_NORMED)
        r = np.maximum(r, np.nan_to_num(r2, nan=-1.0))
    return r


def find(img: np.ndarray, tpl: np.ndarray, threshold: float = 0.85,
         region=None, robust: bool = False) -> Match | None:
    """在 img（或其中 region=[x,y,w,h]）找出最像 tpl 的位置。"""
    ox = oy = 0
    if region is not None:
        ox, oy = int(region[0]), int(region[1])
        img = crop(img, region)
    if img.shape[0] < tpl.shape[0] or img.shape[1] < tpl.shape[1]:
        return None
    r = _score_map(img, tpl, robust)
    _, mv, _, ml = cv2.minMaxLoc(r)
    if mv < threshold:
        return None
    return Match(ml[0] + ox, ml[1] + oy, tpl.shape[1], tpl.shape[0], float(mv))


def find_all(img: np.ndarray, tpl: np.ndarray, threshold: float = 0.85,
             region=None, robust: bool = False) -> list[Match]:
    """找出所有不重疊的符合位置（由上到下排序）。"""
    ox = oy = 0
    if region is not None:
        ox, oy = int(region[0]), int(region[1])
        img = crop(img, region)
    if img.shape[0] < tpl.shape[0] or img.shape[1] < tpl.shape[1]:
        return []
    r = _score_map(img, tpl, robust)
    h, w = tpl.shape[:2]
    out: list[Match] = []
    r = r.copy()
    while True:
        _, mv, _, ml = cv2.minMaxLoc(r)
        if mv < threshold:
            break
        out.append(Match(ml[0] + ox, ml[1] + oy, w, h, float(mv)))
        x0, y0 = max(0, ml[0] - w // 2), max(0, ml[1] - h // 2)
        r[y0:ml[1] + h // 2 + 1, x0:ml[0] + w // 2 + 1] = -1
    return sorted(out, key=lambda m: (m.y, m.x))


def bar_ratio(bar_img: np.ndarray, kind: str) -> float:
    """讀取 HP/MP 條的填滿比例（0~1）。

    HP 條是紅色、MP 條是藍色，空的部分是深灰色。
    取中間幾列像素，計算「是填色」的欄位比例。
    """
    if bar_img is None or bar_img.size == 0:
        return float("nan")
    h = bar_img.shape[0]
    band = bar_img[max(0, h // 2 - 2): h // 2 + 3].astype(np.int32)
    b, g, r = band[..., 0], band[..., 1], band[..., 2]
    if kind == "hp":
        filled = (r > 150) & (r - np.maximum(g, b) > 90)
    elif kind == "mp":
        filled = (b > 150) & (b - r > 90)
    else:
        raise ValueError(kind)
    cols = filled.mean(axis=0) > 0.5
    if not cols.any():
        return 0.0
    # 條是從左往右填，取最右邊的填色位置當比例（比單純計數更不怕雜點）
    idx = np.where(cols)[0]
    # 去掉離群的孤立點：要求右端前 5 欄內至少一半是填色
    right = idx[-1]
    for cand in idx[::-1]:
        seg = cols[max(0, cand - 5): cand + 1]
        if seg.mean() >= 0.5:
            right = cand
            break
    return float(min(1.0, (right + 1) / len(cols)))


def region_signature(img: np.ndarray) -> np.ndarray:
    """縮小成灰階小圖，用來判斷清單捲動後畫面有沒有變。"""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, (32, 32), interpolation=cv2.INTER_AREA).astype(np.int16)


def same_view(a: np.ndarray, b: np.ndarray, tol: float = 2.0) -> bool:
    return float(np.abs(region_signature(a) - region_signature(b)).mean()) < tol


def bar_visible(bar_img: np.ndarray, kind: str) -> bool:
    """這塊區域看起來真的是 HP/MP 條嗎？（被別的視窗擋住時就不是）

    條的中間那一列只會有兩種顏色：填色（紅/藍）或空條的深灰色。"""
    if bar_img is None or bar_img.size == 0:
        return False
    h = bar_img.shape[0]
    band = bar_img[max(0, h // 2 - 2): h // 2 + 3].astype(np.int32)
    b, g, r = band[..., 0], band[..., 1], band[..., 2]
    if kind == "hp":
        fill = (r > 150) & (r - np.maximum(g, b) > 90)
    else:
        fill = (b > 150) & (b - r > 90)
    mx, mn = band.max(axis=2), band.min(axis=2)
    empty = (mx < 90) & (mx - mn < 30)
    return float((fill | empty).mean()) > 0.8


def bar_framed(img: np.ndarray, rect) -> bool:
    """HP/MP 條上下都有一條白色外框嗎？（實測外框在條的上方 3~5px、下方 2~4px）

    角色真的死掉時，條是空的但外框還在；被別的視窗（例如深色主題的程式）蓋住、
    或遊戲畫面變黑時，外框就不見了 → 用這個分辨「HP 歸零」和「看不到」。"""
    x, y, w, h = [int(v) for v in rect]
    pad = max(8, h // 3)

    def has_white_row(y0, y1):
        for yy in range(max(0, y0), min(img.shape[0], y1)):
            row = img[yy, max(0, x + w // 10): x + w - w // 10].astype(np.int16)
            if row.size == 0:
                continue
            mn, mx = row.min(axis=1), row.max(axis=1)
            if ((mn > 180) & (mx - mn < 50)).mean() > 0.8:
                return True
        return False

    if y + h + 2 >= img.shape[0]:
        # 條緊貼著畫面最下面（手機版）：下框在畫面外，只看上框
        return has_white_row(y - pad, y)
    return has_white_row(y - pad, y) and has_white_row(y + h, y + h + pad)
