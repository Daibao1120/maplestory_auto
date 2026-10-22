"""雷電模擬器（手機版）的模擬商店，給單元測試用。

手機版跟電腦版的介面是同一套圖，只是大小是 0.9 倍（實測，tests/samples/ld 的原始截圖）。
所以拿電腦版的模擬商店（realsim.RealShop）來改：
  * 畫面縮成 0.9 倍、裁成 1920x1080（跟模擬器一樣）
  * 畫面下方蓋一條工具列：清單最下面那一列、和 ▼ 的下半部都被擋住（實測就是這樣）
    點到工具列上 = 點不到清單（記在 hud_taps）
  * 手機版的操作方式（實測）：
      - 背包：點 SHORTCUT → 選單裡的「物品」
      - 妙妙：點一下選取、隔一下再點一下才會開商店（AdbWindow.double_click）
      - 賣：點一下道具（選取）→ 點「販售物品」才會跳出「要販售幾個？」
        電腦版的「雙擊道具」在手機版只會選取，不會跳視窗
  * 沒有滑鼠游標 → 沒有說明框、▲▼ 不會變亮
"""
from __future__ import annotations

import cv2
import numpy as np

import realsim as rs

S = 0.9                      # 手機版介面 = 電腦版 x 0.9
CX0, CY0 = 300, 260          # 縮小後的畫面從哪裡開始裁（1920x1080）
W, H = 1920, 1080
SELL_BTN = (1854, 603, 188, 38)          # shop_mid.png 裡「販售物品」的位置（電腦版座標）
MENU_AT = (1500, 900)                    # SHORTCUT 選單裡「物品」畫在哪（電腦版座標）
SHORTCUT_AT = (1900, 1250)               # SHORTCUT 按鈕（電腦版座標；放在工具列遮罩上面，看得到也點得到）


def to_ld(x, y):
    return x * S - CX0, y * S - CY0


def to_pc(x, y):
    return (x + CX0) / S, (y + CY0) / S


def ld_down_top(oy=0):
    """▼ 上緣在手機畫面上的 y。"""
    return to_ld(0, rs.DOWN_BOX[0] + oy)[1]


HUD_Y = int(ld_down_top()) + 8           # 工具列從這裡開始（▼ 只露出上面 8px，跟實測差不多）


class TouchShop(rs.RealShop):
    def __init__(self, *a, shortcut_item=None, shortcut_btn=None, **kw):
        kw.setdefault("hover_effects", False)
        super().__init__(*a, **kw)
        self.menu_open = False
        self.hud_taps = 0
        self.double_on_rows = 0
        self.item_tpl = cv2.resize(shortcut_item, None, fx=1 / S, fy=1 / S, interpolation=cv2.INTER_CUBIC)
        self.btn_tpl = cv2.resize(shortcut_btn, None, fx=1 / S, fy=1 / S, interpolation=cv2.INTER_CUBIC)

    # ---------- 手機版的點擊規則 ----------
    def _hit(self, x, y):
        if not self.shop_open and not self.dialog:
            bx, by = SHORTCUT_AT
            if bx <= x < bx + self.btn_tpl.shape[1] and by <= y < by + self.btn_tpl.shape[0]:
                return "shortcut", None
            if self.menu_open:
                mx, my = MENU_AT
                if mx <= x < mx + self.item_tpl.shape[1] and my <= y < my + self.item_tpl.shape[0]:
                    return "menu_item", None
                return "menu_other", None
        if self.shop_open and not self.dialog:
            sx, sy, sw, sh = SELL_BTN
            if sx <= x - self.ox < sx + sw and sy <= y - self.oy < sy + sh:
                return "sell", None
        return super()._hit(x, y)

    def _click_at(self, x, y, double=False):
        hit, arg = self._hit(x, y)
        if hit == "shortcut":
            self.menu_open = not self.menu_open
            return
        if hit == "menu_item":
            self.menu_open = False
            self.inv_window = True
            return
        if hit == "menu_other":
            self.menu_open = False
            return
        if hit == "inv_button":           # 手機版沒有背包按鈕
            return
        if hit == "sell":
            if self.sel["inv"] is not None and self.sel["inv"] < len(self.inv):
                obj = self.inv[self.sel["inv"]]
                self._open_dialog("sell", str(obj["count"]), obj=obj)
            return
        if hit == "row" and double and arg[0] == "inv":
            self.double_on_rows += 1      # 手機版雙擊道具只會選取
            self.sel["inv"] = arg[1]
            return
        if hit == "leave":
            self.inv_window = False
        super()._click_at(x, y, double=double)

    def capture_pc(self):
        img = rs.RealShop.capture(self)
        if not self.shop_open:
            rs._paste(img, self.btn_tpl, *SHORTCUT_AT)
            if self.menu_open:
                rs._paste(img, self.item_tpl, *MENU_AT)
        return img


class LdView:
    """把 TouchShop（電腦版座標）包成手機版畫面（1920x1080）；介面跟 AdbWindow 一樣。"""

    def __init__(self, shop: TouchShop):
        self.s = shop
        self.taps = []

    def __getattr__(self, k):
        return getattr(self.s, k)

    # 畫面
    def capture(self):
        pc = self.s.capture_pc()
        small = cv2.resize(pc, None, fx=S, fy=S, interpolation=cv2.INTER_AREA)
        img = small[CY0:CY0 + H, CX0:CX0 + W].copy()
        img[HUD_Y:] = (40, 30, 30)                       # 下方工具列（擋住清單最下面）
        return img

    def client_rect(self):
        return 0, 0, W, H

    # 輸入（手機座標 → 電腦版座標）
    def _pc(self, x, y):
        self.taps.append((int(x), int(y)))
        if y >= HUD_Y:
            self.s.hud_taps += 1
            return None
        return to_pc(x, y)

    def click(self, x, y, jitter=0):
        p = self._pc(x, y)
        if p:
            self.s.click(*p)

    def tap(self, x, y):
        p = self._pc(x, y)
        if p:
            self.s.tap(*p)

    def double_click(self, x, y):
        p = self._pc(x, y)
        if p:
            self.s.double_click(*p)

    def drag(self, x1, y1, x2, y2):
        a, b = self._pc(x1, y1), to_pc(x2, y2)
        if a:
            self.s.drag(a[0], a[1], b[0], b[1])

    def move_away(self, x, y):
        pass

    def wheel(self, x, y, clicks):
        pass

    def mouse_pos(self):
        return (0, 0)


def ld_calibration(pc_cal: dict, rows: int = 4) -> dict:
    """電腦版模擬商店的校正值 → 手機版（x0.9、裁切、清單只用上面 rows 列）。"""
    import copy
    c = copy.deepcopy(pc_cal)

    def tr(r):
        x, y = to_ld(r[0], r[1])
        return [int(round(x)), int(round(y)), int(round(r[2] * S)), int(round(r[3] * S))]
    c["regions"] = {k: tr(v) for k, v in pc_cal["regions"].items()}
    for k in ("shop_list", "inv_list"):
        c["regions"][k][3] = int(rows * rs.PITCH * S)
    c["anchors"] = {k: dict(v, dx=int(round(v["dx"] * S)), dy=int(round(v["dy"] * S)))
                    for k, v in pc_cal.get("anchors", {}).items()}
    c["window_size"] = [W, H]
    return c


def ld_templates(src_dir, dst_dir, shortcut_dir):
    """電腦版模擬用的模板 x0.9 + 手機版才有的 SHORTCUT / 物品（直接用模擬器的原始截圖剪的）。"""
    import shutil
    from pathlib import Path
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    for f in src_dir.glob("*.png"):
        if f.name in ("shop_mid.png", "shop_ice_notice.png", "timer_panel.png"):
            continue
        im = cv2.imread(str(f))
        cv2.imwrite(str(dst_dir / f.name), cv2.resize(im, None, fx=S, fy=S, interpolation=cv2.INTER_AREA))
    for n in ("shortcut_item", "shortcut_btn"):
        shutil.copy(Path(shortcut_dir) / f"{n}.png", dst_dir / f"{n}.png")
    # 手機版的「背包按鈕」就是 SHORTCUT（工具列上看得到它 = 遊戲畫面正常）
    shutil.copy(Path(shortcut_dir) / "shortcut_btn.png", dst_dir / "inv_button.png")
