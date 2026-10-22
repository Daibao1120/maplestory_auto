"""從畫面讀等級（左下角「LV.」後面橘色方塊裡的白色數字，例如 1 9 6）。

位置不用校正：HP 條左邊、同一列上，一排 2~3 個大小一樣的橘色方塊就是等級。
每個方塊裡的數字切成 7x7 格（這個字型就是 7x7 的像素字），跟 0~9 的字形比對。
讀不確定就回傳 None（交給 config.yaml 的 level）。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .expbar import find_bars

GRID = 7


def _orange(p: np.ndarray) -> np.ndarray:
    p = p.astype(np.int16)
    return (p[..., 2] > 200) & (p[..., 1] > 100) & (p[..., 1] < 200) & (p[..., 0] < 90)


def find_boxes(img: np.ndarray, bars=None):
    """回傳等級方塊 [(x, y, w, h), ...]（螢幕座標，由左到右），找不到回傳 None。"""
    bars = bars or find_bars(img)
    if bars:
        y, ((hx, hx1), _, _) = bars
        pad = max(10, int((hx1 - hx) * 0.3))        # 依 HP 條長度決定上下找多遠（跟畫面大小無關）
        y0 = max(0, y - pad)
        reg = img[y0:y + pad, :hx]
    else:                                           # 找不到 HP 條（只有一小塊截圖）→ 整張找
        y0, reg = 0, img
    n, _, st, _ = cv2.connectedComponentsWithStats(_orange(reg).astype(np.uint8))
    cands = [tuple(int(v) for v in s[:4]) for s in st[1:]
             if s[4] > 20 and s[3] >= 8 and 0.6 < s[2] / max(1, s[3]) < 1.3]
    if not cands:
        return None
    # 一排大小一樣、靠在一起的方塊；選最靠近 HP 條的那一排
    cands.sort(key=lambda b: b[0])
    groups, cur = [], [cands[0]]
    for b in cands[1:]:
        p = cur[-1]
        same = abs(b[3] - p[3]) <= max(2, p[3] * 0.15) and abs(b[1] - p[1]) <= max(2, p[3] * 0.15)
        near = 0 <= b[0] - (p[0] + p[2]) <= p[2] * 0.6
        if same and near:
            cur.append(b)
        else:
            groups.append(cur)
            cur = [b]
    groups.append(cur)
    groups = [g for g in groups if 1 <= len(g) <= 3]
    if not groups:
        return None
    g = max(groups, key=lambda g: g[-1][0])
    return [(x, y0 + yy, w, h) for x, yy, w, h in g]


# 數字在方塊裡的位置（以方塊大小為比例；實測 2764 寬畫面：方塊 32x38，字在 x 6~26、y 9~29）
GX0, GX1, GY0, GY1 = 6 / 32, 26 / 32, 9 / 38, 29 / 38


def box_feature(img: np.ndarray, box) -> np.ndarray | None:
    """方塊裡的白色數字 → 7x7 格子（字型是 7x7 的像素字），每格是「白色的比例」0~1。"""
    x, y, w, h = box
    c = img[y:y + h, x:x + w].astype(np.int16)
    if c.size == 0:
        return None
    white = ((c.min(axis=2) > 170) & ((c.max(axis=2) - c.min(axis=2)) < 70)).astype(np.float32)
    f = np.zeros((GRID, GRID), np.float32)
    for r in range(GRID):
        for k in range(GRID):
            # 每格只看中間一半，避免格子邊界差 1px 就混到旁邊那格
            ya = y_at(h, GY0 + (GY1 - GY0) * (r + 0.25) / GRID)
            yb = y_at(h, GY0 + (GY1 - GY0) * (r + 0.75) / GRID)
            xa = y_at(w, GX0 + (GX1 - GX0) * (k + 0.25) / GRID)
            xb = y_at(w, GX0 + (GX1 - GX0) * (k + 0.75) / GRID)
            f[r, k] = white[ya:max(yb, ya + 1), xa:max(xb, xa + 1)].mean()
    return f


def y_at(n: int, frac: float) -> int:
    return int(round(n * frac))


def _score(a: np.ndarray, b: np.ndarray) -> float:
    """1 - 平均每格差多少（兩邊都先變成 0/1）。差一格 ≈ 0.02。"""
    return 1.0 - float(np.abs((a > 0.5).astype(np.float32) - (b > 0.5).astype(np.float32)).mean())


class LevelReader:
    def __init__(self, templates: dict[str, np.ndarray] | None = None,
                 min_score: float = 0.95, margin: float = 0.04):
        if templates is None:
            templates = load_templates()
        self.templates = templates
        self.min_score = min_score
        self.margin = margin

    def read_detail(self, img, bars=None):
        boxes = find_boxes(img, bars)
        if not boxes or not self.templates:
            return None, []
        text, info = "", []
        for b in boxes:
            f = box_feature(img, b)
            if f is None or f.max() == 0:
                return None, info
            # 小畫面時方塊才 15px，格子位置差 1px 就會差一格 → 方塊位置上下左右各試 1px，取最吻合的
            feats = [box_feature(img, (b[0] + dx, b[1] + dy, b[2], b[3]))
                     for dx in (-1, 0, 1) for dy in (-1, 0, 1)] if b[3] < 30 else [f]
            feats = [g for g in feats if g is not None]
            sc = sorted(((max(_score(g, t) for g in feats), d) for d, t in self.templates.items()), reverse=True)
            best, second = sc[0], (sc[1] if len(sc) > 1 else (0.0, "?"))
            info.append((best[1], round(best[0], 3), second[1], round(second[0], 3)))
            if best[0] < self.min_score or best[0] - second[0] < self.margin:
                return None, info
            text += best[1]
        if text.startswith("0"):
            return None, info
        return text, info

    def read(self, img, bars=None) -> int | None:
        text, _ = self.read_detail(img, bars)
        lv = int(text) if text else None
        return lv if lv is not None and 1 <= lv <= 200 else None


# 等級數字的字型（7x7），從你的真實截圖學出來的（Lv88、165、172~175、182、187、191、196）。
# 0 截圖裡沒出現過：這個字型的 0 = 8 去掉中間那一橫。
FONT = {
    "0": [".#####.", "##...##", "##...##", "##...##", "##...##", "##...##", ".#####."],
    "1": ["...##..", "..###..", "...##..", "...##..", "...##..", "...##..", "...##.."],
    "2": [".#####.", "##...##", ".....##", "..####.", ".##....", "##.....", "#######"],
    "3": [".#####.", "##...##", ".....##", "..####.", ".....##", "##...##", ".#####."],
    "4": ["...###.", "..####.", ".##.##.", "##..##.", "#######", "....##.", "....##."],
    "5": ["#######", "##.....", "##.....", ".#####.", ".....##", "##...##", ".#####."],
    "6": [".#####.", "##...##", "##.....", "######.", "##...##", "##...##", ".#####."],
    "7": ["#######", ".....##", "....##.", "...##..", "...##..", "..##...", "..##..."],
    "8": [".#####.", "##...##", "##...##", ".#####.", "##...##", "##...##", ".#####."],
    "9": [".#####.", "##...##", "##...##", ".######", ".....##", "##...##", ".#####."],
}


def load_templates(path: Path | None = None) -> dict[str, np.ndarray]:
    return {d: np.array([[c == "#" for c in row] for row in bm], np.float32) for d, bm in FONT.items()}


def build_templates(samples: list[tuple[np.ndarray, int]]) -> dict[str, np.ndarray]:
    """從「截圖 + 正確等級」學每個數字的樣子（同一個數字的格子取平均）。"""
    acc: dict[str, list] = {}
    for img, lv in samples:
        boxes = find_boxes(img)
        digits = str(lv)
        if not boxes or len(boxes) != len(digits):
            continue
        for b, d in zip(boxes, digits):
            f = box_feature(img, b)
            if f is not None:
                acc.setdefault(d, []).append(f)
    T = {d: np.mean(v, axis=0) for d, v in acc.items()}
    # 截圖裡沒出現過 0 → 用 8 去掉中間那一橫（這個字型的 0 和 8 只差中間）
    if "0" not in T and "8" in T:
        z = T["8"].copy()
        z[GRID // 2, 2:GRID - 2] = 0
        T["0"] = z
    return T
