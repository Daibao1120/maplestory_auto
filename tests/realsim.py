"""用「真實遊戲截圖」拼出來的模擬商店（給單元測試用，不需要遊戲）。

畫面全部來自 tests/samples/real/ 裡你電腦上存下來的截圖：
  * 商店外框、按鈕、▲▼、捲軸、滑塊都是真的像素
  * 清單每一列是從真實截圖剪下來的（命中藥丸、蘋果、馴鹿奶 200…），
    紅豆刨冰／馴鹿奶／要賣的道具則是把真實模板貼到真實的空白列上
並模擬實測過的「麻煩」行為：
  * 一次顯示 5 列、點 ▲▼ 捲一列、可以拖滑塊
  * 滑鼠停在道具上會跳出說明框（會擋住清單／捲軸）
  * 滑鼠停在 ▲▼ 上按鈕會變亮（模板會比對不到）
  * 滾輪有機率被吃掉、一格捲 3 列
  * 雙擊沒選取的道具，有機率只會選取（要先點一下再雙擊）
  * 按對話框的「確認」第一下有機率沒反應（Enter 一定有效）
  * 背包滿了只會買一部分，跳「僅購買了 N 個」提示
  * 掛機時會一直撿東西，空格會被佔掉
  * 商店視窗可以在任何位置（整個畫面平移）
所有動作都會花「虛擬時間」，用來檢查整個流程夠不夠快。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

R = Path(__file__).resolve().parent / "samples" / "real"
PITCH, VIS = 115, 5
# shop_mid.png 裡的實測座標
LISTS = {
    "shop": dict(cx=766, cy=837, cw=575, sbx=1351),
    "inv": dict(cx=1428, cy=837, cw=575, sbx=2013),
}
SBW = 40
TRACK = (874, 1374)          # 滑塊可以移動的範圍（y）
THUMB = (991, 1065)          # shop_mid 裡滑塊的位置（剪圖用）
UP_BOX = (838, 870)          # ▲ 按鈕 y 範圍
DOWN_BOX = (1376, 1410)      # ▼ 按鈕 y 範圍
LEAVE = (1174, 587, 191, 40)
BUY = (1174, 650, 191, 40)
TAB_USE = (1541, 761, 101, 48)
HEADER = (705, 485, 1380, 255)      # 商店上方（NPC 圖、按鈕那一排）的空白處，可以拖動視窗
# 對話框（螢幕置中，不跟商店移動）
DLG = (966, 607, 1705, 908)
DLG_TITLE = (1010, 651)
FIELD = (1007, 709, 654, 50)
OK_AT = (1459, 840)
CANCEL_AT = (1583, 840)
STACK = 200


def _img(name):
    im = cv2.imread(str(R / name))
    assert im is not None, name
    return im


MID = _img("shop_mid.png")
PANEL = _img("timer_panel.png")      # 10/21 實測：蓋在「離開商店」「購買物品」上的「剩餘時間」框
NOTICE = _img("shop_ice_notice.png")
TPL = {n: _img(f"{n}.png") for n in (
    "buy_ice", "buy_milk", "sell_elixir", "sell_special", "sell_vitality", "dialog_ok", "dialog_cancel",
    "qty_dialog", "shop_leave", "inv_open", "inv_tab_cash", "miaomiao", "inv_button")}


def _row(img, L, k, dx=0, dy=0):
    c = LISTS[L]
    y = c["cy"] + PITCH * k + dy
    return img[y:y + PITCH, c["cx"] + dx:c["cx"] + dx + c["cw"]].copy()


def _blank(row):
    """把列左邊的圖示和文字抹掉（用同一列右邊空白的那一欄填滿）。"""
    r = row.copy()
    col = r[:, 540:541]
    r[:, :530] = np.repeat(col, 530, axis=1)
    return r


def _paste(dst, src, x, y):
    h, w = src.shape[:2]
    dst[y:y + h, x:x + w] = src
    return dst


def _ref_upscaled(name):
    """沒有原尺寸模板的道具（超級藥水）：用低解析度參考圖放大。"""
    ref = cv2.imread(str(R.parent.parent.parent / "assets" / "refs" / f"{name}.png"))
    f = 3840 / 1456          # 參考圖是在 1456 寬的截圖上剪的，原尺寸螢幕 3840 寬
    return cv2.resize(ref, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)


def _text_row(base, text, seed):
    r = _blank(base)
    rng = np.random.default_rng(seed)
    icon = rng.integers(40, 200, (70, 70, 3), dtype=np.uint8)
    _paste(r, icon, 18, 22)
    cv2.putText(r, text, (111, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (40, 40, 40), 2)
    cv2.putText(r, f"{seed * 37 % 900 + 100} mesos", (111, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (40, 40, 40), 2)
    return r


def build_rows():
    """名稱 → 一列的圖（115 x 575）。"""
    rows = {}
    for k, n in enumerate(["命中藥丸", "蘋果", "雞蛋", "烤肉", "柳橙"]):
        rows["shop:" + n] = _row(MID, "shop", k)
    rows["shop:棒冰棒"] = _row(NOTICE, "shop", 3, -2, -235)
    sblank = _blank(rows["shop:蘋果"])
    rows["shop:ice"] = _paste(sblank.copy(), TPL["buy_ice"], 3, 7)
    rows["shop:milk"] = _paste(sblank.copy(), TPL["buy_milk"], 3, 7)
    for i in range(40):
        rows[f"shop:filler{i}"] = _text_row(rows["shop:蘋果"], f"SHOP ITEM {i}", i + 1)
    rows["inv:cube"] = _row(MID, "inv", 0)
    rows["inv:milk164"] = _row(MID, "inv", 1)
    rows["inv:milk"] = _row(MID, "inv", 2)            # 馴鹿奶 200（一模一樣的列）
    rows["inv:elixir"] = _row(NOTICE, "inv", 3, -2, -235)
    iblank = _blank(rows["inv:milk"])
    elixir_icon = rows["inv:elixir"][:, :105]
    for n in ("special", "vitality"):
        r = iblank.copy()
        r[:, :105] = elixir_icon
        r[:, :105] = cv2.add(r[:, :105], np.full_like(r[:, :105], 30))   # 圖示稍微不同
        rows["inv:" + n] = _paste(r, TPL[f"sell_{n}"], 111, 17)
    r = iblank.copy()
    rows["inv:super"] = _paste(r, _ref_upscaled("sell_super"), 111, 17)
    rows["inv:ice"] = _paste(iblank.copy(), TPL["buy_ice"][:, :250], 3, 7)
    for i in range(40):
        rows[f"inv:junk{i}"] = _text_row(rows["inv:milk"], f"SCROLL {i}", 100 + i)
    rows["blank:shop"] = sblank
    rows["blank:inv"] = iblank
    return rows


ROWS = build_rows()
HL = np.array([102, 204, 255], np.int16)      # 選取中的橘色（實測）


def _highlight(row):
    r = row.astype(np.int16)
    g = r.mean(axis=2)
    spread = r.max(axis=2) - r.min(axis=2)
    bg = (g > 150) & (spread < 30)
    r[bg] = HL
    return r.astype(np.uint8)


def _translate(img, dx, dy):
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REPLICATE)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, float(s))

    def monotonic(self):
        return self.t


class RealShop:
    """模擬的遊戲視窗（介面跟 autopot.window.GameWindow 一樣）。"""

    def __init__(self, shop_items, inv, offset=(0, 0), seed=0, clock=None,
                 bag_slots=None, wheel_drop=0.5, flaky_dbl=0.5, flaky_ok=0.5,
                 tap_drop=0.0, pickup_every=None, pickup_items=("vitality", "special", "elixir"),
                 info_changed_p=0.0, hover_effects=True, shop_open=True, lag_p=0.0,
                 start_tab_use=True, tab_click_drop=0.0, panel=None, draggable=True):
        self.rng = np.random.default_rng(seed)
        self.clock = clock or Clock()
        self.ox, self.oy = offset
        self.shop_items = list(shop_items)
        # 背包是「格子」：賣掉的格子變空，撿到/買到的東西會先放進最前面的空格（= 插在清單中間）
        self.slots = [dict(name=n, count=c) for n, c in inv]
        self.bag_slots = bag_slots or len(self.slots)
        self.lag_p = lag_p
        self.tab_use = start_tab_use      # 右邊背包目前是不是「消耗」分頁（不是的話顯示裝備）
        self.tab_click_drop = tab_click_drop
        self._pending = []        # 延遲生效的捲動 (時間, 清單, 方向)
        self.top = {"shop": 0, "inv": 0}
        self.sel = {"shop": None, "inv": None}
        self.dialog = None
        self.mouse = (0, 0)
        self.shop_open = shop_open
        self.inv_window = False
        self.wheel_drop, self.flaky_dbl, self.flaky_ok, self.tap_drop = wheel_drop, flaky_dbl, flaky_ok, tap_drop
        self.pickup_every, self.pickup_items = pickup_every, pickup_items
        self.info_changed_p = info_changed_p
        self.hover_effects = hover_effects
        self._next_pickup = self.clock.t + (pickup_every or 1e18)
        self.changed_since_open = False
        self.bought: dict[str, int] = {}
        self.sold: list[tuple[str, int]] = []
        self.notices: list[str] = []
        self.log: list[str] = []
        self.base = _translate(MID, self.ox, self.oy)
        self.closed_base = self.base.copy()
        cv2.rectangle(self.closed_base, (700 + self.ox, 540 + self.oy), (2090 + self.ox, 1450 + self.oy), (60, 50, 40), -1)
        tc = MID[1300:1301, LISTS["shop"]["sbx"]:LISTS["shop"]["sbx"] + SBW]
        self.track_tile = tc
        self.thumb_img = MID[THUMB[0]:THUMB[1], LISTS["shop"]["sbx"]:LISTS["shop"]["sbx"] + SBW].copy()
        self.inv_button_at = (1900, 1470)
        # 地圖上方的「剩餘時間」框（10/21 實測）：固定在螢幕上，半透明，會擋住底下的按鈕（點不到）
        self.panel = panel                 # (x, y, w, h) 螢幕座標
        self.draggable = draggable         # 商店視窗能不能用滑鼠拖（拖上方空白處）
        self.drags = []

    # ---------- 狀態 ----------
    @property
    def inv(self):
        return [s for s in self.slots if s]

    def _put(self, item):
        """放進最前面的空格；沒有空格就放到最後（如果背包還有容量）。"""
        for i, s in enumerate(self.slots):
            if s is None:
                self.slots[i] = item
                return True
        if len(self.slots) < self.bag_slots:
            self.slots.append(item)
            return True
        return False

    def _tick(self, dt):
        self.clock.t += dt
        for p in [p for p in self._pending if p[0] <= self.clock.t]:
            self._pending.remove(p)
            self._scroll(p[1], p[2])
        while self.clock.t >= self._next_pickup:
            self._next_pickup += self.pickup_every
            self._pickup()

    def _pickup(self):
        name = self.pickup_items[int(self.rng.integers(len(self.pickup_items)))]
        for it in self.inv:                          # 疊到同種道具上
            if it["name"] == name and it["count"] < 100:
                it["count"] += 1
                self.changed_since_open = True
                return
        if self._put(dict(name=name, count=1)):
            self.changed_since_open = True
            self.log.append(f"撿到 {name}，佔掉一格")

    def items(self, L):
        if L == "inv" and not self.tab_use:
            return [f"junk{20 + i}" for i in range(12)]          # 裝備分頁：一堆裝備
        return self.shop_items if L == "shop" else [it["name"] for it in self.inv]

    def free_capacity(self, name):
        cap = sum(STACK - it["count"] for it in self.inv if it["name"] == name)
        free = self.slots.count(None) + self.bag_slots - len(self.slots)
        return cap + free * STACK

    def count(self, name):
        return sum(it["count"] for it in self.inv if it["name"] == name)

    # ---------- 座標 ----------
    def _list_box(self, L):
        c = LISTS[L]
        return c["cx"] + self.ox, c["cy"] + self.oy, c["cw"], PITCH * VIS

    def _thumb_y(self, L):
        n = len(self.items(L))
        if n <= VIS:
            return None
        th = THUMB[1] - THUMB[0]
        return TRACK[0] + round((TRACK[1] - TRACK[0] - th) * self.top[L] / (n - VIS))

    def _hit(self, x, y):
        """回傳 (種類, 參數)。"""
        if self.panel and not self.dialog:
            px, py, pw, ph = self.panel
            if px <= x < px + pw and py <= y < py + ph:
                return "panel", None
        if self.dialog:
            if OK_AT[0] <= x < OK_AT[0] + 103 and OK_AT[1] <= y < OK_AT[1] + 43:
                return "ok", None
            if self.dialog["kind"] != "notice" and CANCEL_AT[0] <= x < CANCEL_AT[0] + 103 and CANCEL_AT[1] <= y < CANCEL_AT[1] + 43:
                return "cancel", None
            if FIELD[0] <= x < FIELD[0] + FIELD[2] and FIELD[1] <= y < FIELD[1] + FIELD[3]:
                return "field", x
            return "dialog", None
        if not self.shop_open:
            bx, by = self.inv_button_at
            if bx <= x < bx + 67 and by <= y < by + 56:
                return "inv_button", None
            if self.inv_window:
                if 829 <= x < 919 and 379 <= y < 435:
                    return "cash_tab", None
                if 420 <= x < 502 and 450 <= y < 509:
                    return "miaomiao", None
            return None, None
        x0, y0 = x - self.ox, y - self.oy
        for name, (bx, by, bw, bh) in (("leave", LEAVE), ("buy", BUY), ("tab_use", TAB_USE)):
            if bx <= x0 < bx + bw and by <= y0 < by + bh:
                return name, None
        hx, hy, hw, hh = HEADER
        if hx <= x0 < hx + hw and hy <= y0 < hy + hh:
            return "header", None
        for L, c in LISTS.items():
            sx = c["sbx"]
            if sx <= x0 < sx + SBW:
                if UP_BOX[0] <= y0 < UP_BOX[1]:
                    return "arrow", (L, -1)
                if DOWN_BOX[0] <= y0 < DOWN_BOX[1]:
                    return "arrow", (L, +1)
                ty = self._thumb_y(L)
                if ty is not None and ty <= y0 < ty + (THUMB[1] - THUMB[0]):
                    return "thumb", L
                if TRACK[0] <= y0 < TRACK[1]:
                    return "track", L
            if c["cx"] <= x0 < c["cx"] + c["cw"] and c["cy"] <= y0 < c["cy"] + PITCH * VIS:
                idx = self.top[L] + int((y0 - c["cy"]) // PITCH)
                if idx < len(self.items(L)):
                    return "row", (L, idx)
                return "list", L
        return None, None

    # ---------- 畫面 ----------
    def capture(self):
        self._tick(0.03)
        if not self.shop_open:
            img = self.closed_base.copy()
            _paste(img, TPL["inv_button"], *self.inv_button_at)
            if self.inv_window:
                _paste(img, TPL["inv_open"], 400, 300)
                _paste(img, TPL["inv_tab_cash"], 829, 379)
                _paste(img, TPL["miaomiao"], 420, 450)
        else:
            img = self.base.copy()
            if not self.tab_use:
                tx, ty, tw, th = TAB_USE
                img[ty + self.oy:ty + th + self.oy, tx + self.ox:tx + tw + self.ox] = \
                    img[ty + self.oy:ty + th + self.oy, tx + 200 + self.ox:tx + 200 + tw + self.ox]   # 換成灰色分頁
            for L in LISTS:
                self._draw_list(img, L)
            hit, arg = self._hit(*self.mouse)
            if self.hover_effects and hit == "arrow":
                L, d = arg
                x0 = LISTS[L]["sbx"] + self.ox
                y0, y1 = (UP_BOX if d < 0 else DOWN_BOX)
                roi = img[y0 + self.oy:y1 + self.oy, x0:x0 + SBW]
                roi[:] = cv2.addWeighted(roi, 0.35, np.full_like(roi, 255), 0.65, 0)
            if self.hover_effects and hit == "row" and not self.dialog:
                mx, my = self.mouse
                x1, y1 = min(mx + 25, img.shape[1] - 10), min(my + 25, img.shape[0] - 10)
                cv2.rectangle(img, (x1, y1), (min(x1 + 560, img.shape[1] - 1), min(y1 + 260, img.shape[0] - 1)), (60, 40, 25), -1)
                cv2.putText(img, "ITEM INFO", (x1 + 20, y1 + 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
        if self.panel:
            px, py, pw, ph = self.panel
            _paste(img, PANEL[:ph, :pw], px, py)        # 真實截圖剪下來的「剩餘時間」框
        if self.dialog:
            self._draw_dialog(img)
        return img

    def _draw_list(self, img, L):
        c = LISTS[L]
        items = self.items(L)
        for i in range(VIS):
            idx = self.top[L] + i
            name = items[idx] if idx < len(items) else None
            if name is None:
                row = ROWS["blank:" + L]
            elif L == "inv" and name == "milk" and self.inv[idx]["count"] < STACK:
                row = ROWS["inv:milk164"]
            else:
                row = ROWS[f"{L}:{name}"]
            if name is not None and self.sel[L] == idx:
                row = _highlight(row)
            _paste(img, row, c["cx"] + self.ox, c["cy"] + self.oy + PITCH * i)
        x = c["sbx"] + self.ox
        img[TRACK[0] + self.oy:TRACK[1] + self.oy, x:x + SBW] = np.repeat(self.track_tile, TRACK[1] - TRACK[0], axis=0)
        ty = self._thumb_y(L)
        if ty is not None:
            _paste(img, self.thumb_img, x, ty + self.oy)

    def _draw_dialog(self, img):
        d = self.dialog
        x0, y0, x1, y1 = DLG
        cv2.rectangle(img, (x0, y0), (x1, y1), (225, 225, 225), -1)
        cv2.rectangle(img, (x0 + 12, y0 + 12), (x1 - 12, y0 + 170), (160, 110, 60), -1)
        if d["kind"] == "qty":
            _paste(img, TPL["qty_dialog"], *DLG_TITLE)
        elif d["kind"] == "sell":
            cv2.putText(img, "SELL HOW MANY", (DLG_TITLE[0], DLG_TITLE[1] + 30), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)
        else:
            cv2.putText(img, d["text"], (DLG_TITLE[0], DLG_TITLE[1] + 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)
        if d["kind"] in ("qty", "sell"):
            fx, fy, fw, fh = FIELD
            cv2.rectangle(img, (fx, fy), (fx + fw, fy + fh), (255, 255, 255), -1)
            cv2.rectangle(img, (fx, fy), (fx + fw, fy + fh), (90, 90, 90), 1)
            x = fx + 14
            for ch in d["text"]:
                cv2.putText(img, ch, (x, fy + 38), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2)
                x += 24
            _paste(img, TPL["dialog_cancel"], *CANCEL_AT)
        _paste(img, TPL["dialog_ok"], *OK_AT)

    # ---------- 對話框邏輯 ----------
    def _open_dialog(self, kind, text, **kw):
        self.dialog = dict(kind=kind, text=text, cursor=len(text), fresh=True, **kw)

    def _notice(self, text):
        self.notices.append(text)
        self._open_dialog("notice", text)

    def _accept(self):
        d = self.dialog
        self.dialog = None
        if d["kind"] == "qty":
            n = int(d["text"] or 0)
            name = d["item"]
            if n <= 0:
                return
            got = min(n, self.free_capacity(name))
            left = got
            for it in self.inv:
                if it["name"] == name and left:
                    add = min(STACK - it["count"], left)
                    it["count"] += add
                    left -= add
            while left > 0:
                add = min(STACK, left)
                if not self._put(dict(name=name, count=add)):
                    break
                left -= add
            if got:
                self.bought[name] = self.bought.get(name, 0) + got
            if got < n:
                self._notice(f"ONLY BOUGHT {got}")
        elif d["kind"] == "sell":
            it = d["obj"]
            if self.changed_since_open and self.rng.random() < self.info_changed_p:
                self._notice("ITEM INFO CHANGED")
                return
            if not any(x is it for x in self.slots):
                self._notice("ITEM INFO CHANGED")
                return
            n = int(d["text"] or 0)
            if n >= it["count"]:
                j = next(i for i, x in enumerate(self.slots) if x is it)
                self.slots[j] = None
                self.sold.append((it["name"], it["count"]))
                self.sel["inv"] = None
                self.top["inv"] = max(0, min(self.top["inv"], len(self.inv) - VIS))
            else:
                it["count"] -= n
                self.sold.append((it["name"], n))

    # ---------- 輸入（跟 GameWindow 一樣的介面） ----------
    def _click_at(self, x, y, double=False):
        self.mouse = (x, y)
        hit, arg = self._hit(x, y)
        if self.dialog:
            if hit == "ok":
                if self.dialog.get("fresh") and self.rng.random() < self.flaky_ok:
                    self.dialog["fresh"] = False       # 第一下沒反應
                    return
                self._accept()
            elif hit == "cancel":
                self.dialog = None
            elif hit == "field":
                d = self.dialog
                pos = max(0, min(len(d["text"]), (arg - FIELD[0] - 14 + 12) // 24))
                d["cursor"] = pos
            return
        if hit == "inv_button":
            self.inv_window = not self.inv_window
        elif hit == "miaomiao" and double:
            self.shop_open = True
            self.top = {"shop": 0, "inv": 0}
            self.sel = {"shop": None, "inv": None}
            self.changed_since_open = False
        elif hit == "tab_use":
            if self.rng.random() >= self.tab_click_drop:
                self.tab_use = True
        elif hit == "leave":
            self.shop_open = False
            self.dialog = None
        elif hit == "buy":
            if self.sel["shop"] is not None:
                name = self.shop_items[self.sel["shop"]]
                cap = self.free_capacity(name)
                self._open_dialog("qty", str(min(9999, max(cap, 1))), item=name)
        elif hit == "arrow":
            L, d = arg
            if self.rng.random() >= self.tap_drop:
                if self.lag_p and self.rng.random() < self.lag_p:
                    self._pending.append((self.clock.t + 0.5, L, d))   # 遊戲卡一下，晚點才捲
                else:
                    self._scroll(L, d)
        elif hit == "row":
            L, idx = arg
            if double and L == "inv":
                if self.sel["inv"] != idx and self.rng.random() < self.flaky_dbl:
                    self.sel["inv"] = idx               # 只選取，沒跳視窗
                else:
                    self.sel["inv"] = idx
                    self._open_dialog("sell", str(self.inv[idx]["count"]), obj=self.inv[idx])
            else:
                self.sel[L] = idx

    def _scroll(self, L, d):
        n = len(self.items(L))
        self.top[L] = max(0, min(max(0, n - VIS), self.top[L] + d))

    def click(self, x, y, jitter=0):
        self._tick(0.3)
        self._click_at(int(x), int(y))

    def tap(self, x, y):
        self._tick(0.08)
        self._click_at(int(x), int(y))

    def double_click(self, x, y):
        self._tick(0.35)
        self._click_at(int(x), int(y), double=True)

    def drag(self, x1, y1, x2, y2):
        self._tick(0.3)
        self.mouse = (int(x2), int(y2))
        hit, L = self._hit(int(x1), int(y1))
        if hit == "header" and self.shop_open and self.draggable and not self.dialog:
            dx, dy = int(round(x2 - x1)), int(round(y2 - y1))
            self.ox += dx
            self.oy += dy
            self.drags.append((dx, dy))
            self.base = _translate(MID, self.ox, self.oy)
            return
        if hit != "thumb" or self.dialog:
            return
        n = len(self.items(L))
        th = THUMB[1] - THUMB[0]
        frac = (y2 - self.oy - TRACK[0] - th / 2) / (TRACK[1] - TRACK[0] - th)
        self.top[L] = int(round(max(0.0, min(1.0, frac)) * (n - VIS)))

    def wheel(self, x, y, clicks):
        self._tick(0.06)
        self.mouse = (int(x), int(y))
        hit, arg = self._hit(int(x), int(y))
        if self.dialog or hit not in ("row", "list") or self.rng.random() < self.wheel_drop:
            return
        L = arg[0] if hit == "row" else arg
        for _ in range(3):
            self._scroll(L, -int(np.sign(clicks)))

    def move_away(self, x, y):
        self._tick(0.1)
        self.mouse = (int(x), int(y))

    def press(self, key, times=1):
        for _ in range(times):
            self._tick(0.05)
            d = self.dialog
            if not d:
                continue
            if key == "enter":
                self._accept()
            elif key == "esc":
                self.dialog = None
            elif key == "backspace" and d["kind"] != "notice":
                if d["cursor"] > 0:
                    d["text"] = d["text"][:d["cursor"] - 1] + d["text"][d["cursor"]:]
                    d["cursor"] -= 1
            elif key == "delete" and d["kind"] != "notice":
                d["text"] = d["text"][:d["cursor"]] + d["text"][d["cursor"] + 1:]

    def type_text(self, text):
        for ch in text:
            self._tick(0.08)
            d = self.dialog
            if d and d["kind"] != "notice" and ch.isdigit() and len(d["text"]) < 4:
                d["text"] = d["text"][:d["cursor"]] + ch + d["text"][d["cursor"]:]
                d["cursor"] += 1

    def activate(self):
        pass

    def foreground(self):
        return 1

    def restore_foreground(self, h):
        pass

    def mouse_pos(self):
        return self.mouse
