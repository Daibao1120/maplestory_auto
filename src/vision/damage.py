"""傷害數字偵測——「這一下有沒有打到東西」的直接證據。

規格全部來自實機量測（2026-08-25 戰火之地，27 幀連續畫面 + 獨立驗證幀）：
橘色數字＝打出去的傷害，紫色數字＝自己受到的傷害。加上字元形狀過濾後
21/22 命中、0 誤判。

三種顏色（實機截圖逐張看過確認）：
  橘（H0-32）＝一般傷害；粉紅（H150-180 與 0-6，高亮低飽和邊）＝爆擊；
  紫（H126-163）＝自己受到的傷害。

關於粉紅有一段彎路值得記著：先前量到「桃紅色帶 3035 個色塊裡 0 個是真傷害」，
於是把粉紅整段寫死成雜訊。那個量測是**在沒有高度歸屬的情況下做的**——全畫面
的粉紅確實被其他玩家的技能特效主導，但**自身高度帶內的粉紅是貨真價實的爆擊
數字**（實測 164 幀有 10 組，逐張裁切看過，全部是數字）。而這個角色的爆擊率
過半，把粉紅當雜訊等於擋掉大半的真實擊殺。
教訓：顏色帶的取捨必須在「已經做完歸屬」之後才量，否則量到的是別人的畫面。

  - 只靠「時間穩定性」分辨 UI：其他玩家的特效同樣是瞬時的，分不開。
  - 聊天框粉紅字 H164-166 緊貼紫色帶邊，所以紫色上限不可越過 163。

真正把雜訊殺掉的是**字元幾何**：傷害數字是一排 2~4 個等高的數字。
量測到的雜訊與對應的過濾條件：
  靜態 UI 圖示（27/27 幀都在）→ 每組至少 2 個字元
  路過玩家的黃帽（w31-35 h18-19）→ 字元高度下限 22
  樹皮（H15-22、V 中位數 145）→ V>=160 且字元高度上限 40
  隊伍血條紅（h16）→ 字元高度下限 22
  其他玩家的技能特效（單塊面積上到 1075）→ 整組高度下限 24
"""

try:
    import cv2
    import numpy as np
    _CV_AVAILABLE = True
except Exception:                                    # pragma: no cover
    cv2 = None
    np = None
    _CV_AVAILABLE = False


# 以下像素門檻都是「基準尺度 1371x808」的數字。跑在原始 2736 要全部乘二；
# 不可以跑在更小的尺度——字元會掉到 11~15px 高，和聊天字（上限 20px）就分不開了。
ORANGE_LO, ORANGE_HI = (0, 150, 160), (32, 255, 255)      # 打出去的一般傷害
# 爆擊是粉紅。色相環繞 0，所以要兩段。飽和度下限比橘色低（爆擊字有大片亮粉
# 與白色描邊），但亮度下限更高——這兩個條件合起來把聊天粉紅字（暗）與樹皮
# （不夠亮）擋在外面，剩下的靠字元幾何與高度歸屬。
CRIT_BANDS = (((150, 110, 170), (180, 255, 255)),
              ((0, 110, 170), (6, 255, 255)))
VIOLET_LO, VIOLET_HI = (126, 140, 160), (163, 255, 255)   # 自己受到的傷害
GLYPH_MIN_AREA = 110
GLYPH_H = (22, 40)
GLYPH_W = (8, 34)
GLYPH_FILL = 0.35
GROUP_GAP_X, GROUP_GAP_Y = 12, 8
GROUP_MIN_GLYPHS = 2
GROUP_H = (24, 46)
GROUP_W = (26, 170)


class DamageGroup:
    """畫面上的一組傷害數字。"""

    __slots__ = ("x", "y", "w", "h", "glyphs", "kind")

    def __init__(self, x, y, w, h, glyphs, kind):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.glyphs, self.kind = glyphs, kind

    @property
    def center(self):
        return (self.x + self.w // 2, self.y + self.h // 2)

    @property
    def bottom(self):
        return self.y + self.h

    def __repr__(self):                              # pragma: no cover
        return f"<{self.kind} {self.glyphs}字 @{self.center} {self.w}x{self.h}>"


def _glyphs(mask):
    n, _lab, st, _cen = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for k in range(1, n):
        x, y, w, h, a = st[k]
        if a < GLYPH_MIN_AREA:
            continue
        if not (GLYPH_H[0] <= h <= GLYPH_H[1]):
            continue
        if not (GLYPH_W[0] <= w <= GLYPH_W[1]):
            continue
        if a / float(max(1, w * h)) < GLYPH_FILL:
            continue
        out.append([int(x), int(y), int(w), int(h)])
    return out


def _group(glyphs, kind):
    """把靠在一起的字元併成一個數字。傷害是一排數字，單一色塊多半是雜訊。"""
    boxes = [g[:] + [1] for g in glyphs]
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            if boxes[i] is None:
                continue
            for j in range(i + 1, len(boxes)):
                if boxes[j] is None:
                    continue
                ax, ay, aw, ah, an = boxes[i]
                bx, by, bw, bh, bn = boxes[j]
                gap_x = max(bx - (ax + aw), ax - (bx + bw))
                gap_y = max(by - (ay + ah), ay - (by + bh))
                if gap_x <= GROUP_GAP_X and gap_y <= GROUP_GAP_Y:
                    nx, ny = min(ax, bx), min(ay, by)
                    boxes[i] = [nx, ny,
                                max(ax + aw, bx + bw) - nx,
                                max(ay + ah, by + bh) - ny, an + bn]
                    boxes[j] = None
                    merged = True
    out = []
    for b in boxes:
        if b is None:
            continue
        x, y, w, h, n = b
        if n < GROUP_MIN_GLYPHS:
            continue
        if not (GROUP_H[0] <= h <= GROUP_H[1]):
            continue
        if not (GROUP_W[0] <= w <= GROUP_W[1]):
            continue
        out.append(DamageGroup(x, y, w, h, n, kind))
    return out


def find_damage(frame):
    """在**基準尺度**畫面上找傷害數字，回傳 [DamageGroup, ...]。

    kind = "dealt"（橘，打出去的）或 "taken"（紫，自己被打的）。
    """
    if not _CV_AVAILABLE or frame is None:
        return []
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    out = []
    for lo, hi, kind in ((ORANGE_LO, ORANGE_HI, "dealt"),
                         (VIOLET_LO, VIOLET_HI, "taken")):
        m = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        out.extend(_group(_glyphs(m), kind))
    # 爆擊（粉紅）：色相環繞 0，兩段遮罩合併後再找字元
    m = None
    for lo, hi in CRIT_BANDS:
        b = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        m = b if m is None else cv2.bitwise_or(m, b)
    out.extend(_group(_glyphs(m), "dealt"))
    return out


class DamageWatcher:
    """追蹤「最近有沒有打到東西」，並把別人的傷害數字排除掉。

    同一張圖上常有其他玩家在打怪：實測這張圖 59% 的橘色數字屬於站在上層平台
    的其他玩家。單看「畫面上有沒有橘字」會一直誤判成自己打得到。所以要用
    角色的腳底高度做歸屬——自己打出的傷害會出現在自己所在的那一層。
    """

    # 自己打出的傷害，數字底部相對**角色腳底**的高度範圍（基準 px）。
    #
    # 實機量測（27 幀）：角色腳底 y 675~678，自己打出的傷害數字底部落在
    # 612~635，也就是腳底上方 40~66px——數字畫在角色胸口高度，而且每幀還會
    # 往上飄約 17px。取 (-80, +20) 留足餘裕。
    # 別層的傷害（其他玩家在上層平台打怪）底部在 y 324~396，也就是腳底上方
    # 280~350px，被這個窗擋在外面還有兩百多像素的距離。
    OWN_BAND = (-80, 20)

    def __init__(self, own_band=None):
        self.own_band = tuple(own_band or self.OWN_BAND)
        self.last_dealt = None      # 最近一次「自己打到」的時間
        self.last_taken = None      # 最近一次「自己被打」的時間
        self.dealt_count = 0
        self.last_side = None       # 最近一次打到的東西在自己的左邊還右邊

    def update(self, frame, now, player=None, stale=False):
        """吃一幀（基準尺度）＋角色中心座標，回傳這一幀屬於自己的傷害組。

        player 為 None 時無法歸屬——這時**不採信任何傷害**，寧可當作沒打到。
        認錯成「打得到」會讓腳本對著空氣一直開火，那正是要修的問題。
        """
        groups = find_damage(frame)
        # stale＝定位器這一幀沒鎖到、沿用上次位置（實測誤差可到 180px）。
        # 用錯的原點做高度歸屬，等於把別人的傷害算成自己的。
        if player is None or stale:
            return []
        px, py = player
        lo, hi = self.own_band
        mine = [g for g in groups
                if g.kind == "dealt" and lo <= (g.bottom - py) <= hi]
        # py 是腳底 y；OWN_BAND 就是相對腳底量的（見常數註解）
        if mine:
            self.last_dealt = now
            self.dealt_count += len(mine)
            cx = sum(g.center[0] for g in mine) / len(mine)
            self.last_side = "left" if cx < px else "right"
        if any(g.kind == "taken" for g in groups):
            self.last_taken = now
        return mine

    def hitting(self, now, memory=1.2):
        """最近 memory 秒內有打到東西嗎。"""
        return self.last_dealt is not None and now - self.last_dealt <= memory
