"""讀快捷欄上的補品數量（例如馴鹿奶 6200、紅豆刨冰 1224）。

快捷欄的數字是放大約 3 倍的像素字（每個字 6x9 格，每格約 3x3 像素，橫向有時只有 2px）：
  * 填色是「固定的漸層」：上面純白，往下慢慢變淺藍（每一列的顏色都一樣，見 FONT_COLORS）
  * 外面一圈黑框（上下左右各一格；斜角不一定是黑的）
做法：
  1. 找出「顏色剛好等於那一列字的顏色」的像素 = 填色；很暗的像素 = 黑框
  2. 把 0~9 的字形一格一格比對：字的格子要是填色、黑框的格子要是黑的
     （黑框外面是什麼不管 —— 牛奶瓶、刨冰碗的白色常常會從字的空洞或旁邊露出來）
  3. 用動態規劃找出「從左到右、整串最像」的字串（避免把 0 的左邊一條讀成 1）

任何一個字不夠像、或跟別的字分不太出來，就回傳 None（寧可不知道，也不要讀錯）。
"""
from __future__ import annotations

import numpy as np

# 字從格子頂端往下第 74px 開始（格子高 121px）
GLYPH_TOP = 74
SLOT_H = 121
# 每一列字的顏色，從字的最上面往下（實測，像素完全一樣）
_RGB = ([(255, 255, 255)] * 12 + [(238, 255, 255)] * 3 + [(221, 238, 255)] * 3 + [(187, 221, 255)] * 3
        + [(170, 204, 255)] * 3 + [(153, 204, 255)] * 2)
FONT_COLORS = np.array([(b, g, r) for (r, g, b) in _RGB], np.int16)     # 轉成 BGR

# 字形（6 寬 x 9 高；最後一列只有 2px 而且常被漸層吃掉，比對時不用）
FONT = {
    "0": [".####.", "#....#", "#....#", "#....#", "#....#", "#....#", "#....#", "#....#", ".####."],
    "1": ["..#", "###", "..#", "..#", "..#", "..#", "..#", "..#", "..#"],
    "2": [".####.", "#....#", ".....#", ".....#", "....#.", "..##..", ".#....", "#.....", "######"],
    "3": [".####.", "#....#", ".....#", ".....#", "..###.", ".....#", ".....#", "#....#", ".####."],
    "4": ["....#.", "...##.", "..#.#.", ".#..#.", "#...#.", "######", "....#.", "....#.", "....#."],
    "5": ["######", "#.....", "#.....", "#####.", ".....#", ".....#", ".....#", "#....#", ".####."],
    "6": ["..###.", ".#....", "#.....", "#####.", "#....#", "#....#", "#....#", "#....#", ".####."],
    "7": ["######", ".....#", "....#.", "....#.", "...#..", "...#..", "..#...", "..#...", ".#...."],
    "8": [".####.", "#....#", "#....#", "#....#", ".####.", "#....#", "#....#", "#....#", ".####."],
    "9": [".####.", "#....#", "#....#", "#....#", "#....#", ".#####", ".....#", ".....#", ".####."],
}
ROWS = 8                    # 只比對前 8 列
SCALES = (2.8, 2.9, 3.0)    # 橫向每格幾 px（遊戲放大不是剛好 3 倍）
MAX_COLS = 6


def _cell_masks(bitmap: list[str]):
    """字形 → (字的格子, 黑框的格子)，都是 (ROWS+2) x (寬+2)，外圍多一圈。"""
    b = np.array([[c == "#" for c in row] for row in bitmap[:ROWS]], bool)
    h, w = b.shape
    pad = np.zeros((h + 2, w + 2), bool)
    pad[1:-1, 1:-1] = b
    ring = pad.copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):        # 上下左右（斜角不算）
        ring |= np.roll(np.roll(pad, dy, 0), dx, 1)
    ring &= ~pad
    ring[0, :] = False       # 字上面那一格常跟圖示接在一起，不看
    ring[-1, :] = False      # 第 8 列以下不比對
    return pad, ring


CELLS = {d: _cell_masks(bm) for d, bm in FONT.items()}
WIDTH = {d: len(bm[0]) for d, bm in FONT.items()}
# SUPERSET[d] = 字形完全被 d 包含的字（例如 3 ⊂ 8：8 = 3 再加左邊兩小段）。
# 讀到 d、第二像的是它的子集 → d 多出來的格子一定都有填色（不然 d 的分數會掉），可以放寬差距要求。
# 10/22 實測：刨冰「847」「803」的 8 跟 3 只差 0.033，被當成「分不出來」→ 數量一直卡在舊的 311
SUPERSET = {d: {e for e in FONT if e != d and WIDTH[e] == WIDTH[d]
                and not (CELLS[e][0] & ~CELLS[d][0]).any()} for d in FONT}


def fill_and_dark(slot: np.ndarray, top: int):
    p = slot.astype(np.int16)
    H = p.shape[0]
    fill = np.zeros(p.shape[:2], bool)
    for k, col in enumerate(FONT_COLORS):
        y = top + k
        if 0 <= y < H:
            fill[y] = np.all(np.abs(p[y] - col) <= 10, axis=1)
    dark = p.max(axis=2) < 60
    return fill, dark


def _cell_means(mask: np.ndarray, top: int, sx: float, W: int):
    """每一格（取格子中間那一列）有多少比例是 mask。
    回傳 M[r+1, c+1, x]：字的左上角放在 x 時，第 r 列第 c 格（r=-1..ROWS, c=-1..MAX_COLS）。
    超出畫面的位置是 NaN。"""
    H = mask.shape[0]
    M = np.full((ROWS + 2, MAX_COLS + 2, W), np.nan)
    xs = np.arange(W)
    for r in range(-1, ROWS + 1):
        y = top + 3 * r + 1
        if not 0 <= y < H:
            continue
        cs = np.concatenate([[0], np.cumsum(mask[y].astype(np.int32))])
        for c in range(-1, MAX_COLS + 1):
            a = xs + int(round(c * sx))
            b = xs + int(round((c + 1) * sx))
            ok = (a >= 0) & (b <= W) & (b > a)
            v = np.full(W, np.nan)
            v[ok] = (cs[b[ok]] - cs[a[ok]]) / (b[ok] - a[ok])
            M[r + 1, c + 1] = v
    return M


def glyph_scores(fill, dark, top, W):
    """每個字、每個 x 的分數（1 = 完全吻合），各種橫向比例取最好的。回傳 {字: (分數[W], 比例[W])}。"""
    out = {}
    for sx in SCALES:
        NF = _cell_means(fill, top, sx, W)
        ND = _cell_means(dark, top, sx, W)
        for d, (pad, ring) in CELLS.items():
            w = pad.shape[1]
            nf = NF[:, :w][pad]                 # (格數, W)
            nd = ND[:, :w][ring]
            miss = (1 - nf).sum(axis=0) + (1 - nd).sum(axis=0)
            s = 1 - miss / (pad.sum() + ring.sum())
            s = np.nan_to_num(s, nan=0.0)
            if d not in out:
                out[d] = (s, np.full(W, sx))
            else:
                better = s > out[d][0]
                out[d][0][better] = s[better]
                out[d][1][better] = sx
    return out


class QuickslotReader:
    def __init__(self, min_score: float = 0.88, margin: float = 0.05, base: float = 0.7,
                 first_max_x: int = 24, gap: tuple[int, int] = (1, 9), margin_subset: float = 0.02):
        self.min_score = min_score      # 每個字至少要這麼像
        self.margin = margin            # 同一個位置第二像的字至少要差這麼多
        self.margin_subset = margin_subset   # 第二像的字是讀到的字的「子集」（3 ⊂ 8）時，差這麼多就夠
        self.base = base
        self.first_max_x = first_max_x  # 第一個字的左邊最多在格子的第幾 px
        self.gap = gap                  # 字和字之間的空隙（px）

    def read_text(self, slot: np.ndarray):
        """回傳 (數字字串或 None, [(字, x, 分數, 第二像的字, 它的分數), ...])。"""
        H, W = slot.shape[:2]
        top0 = int(round(GLYPH_TOP * H / SLOT_H))
        best = None
        for top in range(top0 - 3, top0 + 4):          # 位置差幾 px 也找得到
            fill, dark = fill_and_dark(slot, top)
            if fill.sum() < 30:
                continue
            S = glyph_scores(fill, dark, top, W)
            r = self._parse(S, W)
            if r and (best is None or r[0] > best[0]):
                best = r + (S, top, fill)
        if best is None:
            return None, []
        _, seq, S, top, fill = best
        info = []
        for d, x, s in seq:
            lo, hi = max(0, x - 2), min(W, x + 3)
            alt = max(((float(S[e][0][lo:hi].max()), e) for e in FONT if e != d))
            info.append((d, x, s, alt[1], alt[0]))
        if any(s < self.min_score or s - a < (self.margin_subset if alt in SUPERSET[d] else self.margin)
               for d, _, s, alt, a in info):
            return None, info
        if len(seq) > 5:
            return None, info
        # 淺藍色（字的下半部才有的顏色）卻不在任何一個讀到的字裡面
        # → 有字被擋住一半、沒讀到 → 不要回傳少一位的數字
        if self.leftover(fill, top, seq, S) > 2:
            return None, info
        return "".join(d for d, *_ in info), info

    @staticmethod
    def leftover(fill, top, seq, S) -> int:
        band = np.zeros_like(fill)
        band[top + 12: top + 26] = fill[top + 12: top + 26]
        band[:, fill.shape[1] - 8:] = False       # 格子最右邊有一小塊同顏色的框，不算
        for d, x, _ in seq:
            w = int(round(WIDTH[d] * S[d][1][x]))
            band[:, max(0, x - 2): x + w + 2] = False
        return int(band.sum())

    def _parse(self, S, W):
        """動態規劃：找出總分最高、由左到右不重疊的一串字。"""
        dp: dict[int, tuple[float, list]] = {}         # 結束位置 → (總分, [(字, x, 分數)])
        g0, g1 = self.gap
        for x in range(2, W):
            for d in FONT:
                s = float(S[d][0][x])
                if s < self.base:
                    continue
                w = int(round(WIDTH[d] * S[d][1][x]))
                gain = (s - self.base) * w
                cand = (gain, [(d, x, s)]) if x <= self.first_max_x else None
                for e in range(x - g1, x - g0 + 1):
                    if e in dp and (cand is None or dp[e][0] + gain > cand[0]):
                        cand = (dp[e][0] + gain, dp[e][1] + [(d, x, s)])
                if cand:
                    e = x + w
                    if e not in dp or dp[e][0] < cand[0]:
                        dp[e] = cand
        if not dp:
            return None
        return max(dp.values(), key=lambda c: c[0])

    def read(self, slot: np.ndarray, start_x=None) -> int | None:
        text, _ = self.read_text(slot)
        return int(text) if text else None
