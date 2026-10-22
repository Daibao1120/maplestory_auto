"""全自動校正（給圖形介面用的版本）：自己操作遊戲，把按鈕、圖示、區域全部找出來存好。

跟根目錄的 autocal.py 一樣的做法（assets/refs/ 的低解析度參考圖 → 在畫面上找到 → 剪下原尺寸模板），
差別：
  * 不寫死要買什麼（補品改用「掃描商店」挑，見 catalog.py）
  * 畫面比例用找的（朋友的螢幕、遊戲視窗大小都不一樣）：先在很大的範圍裡找「道具欄按鈕」，
    找到最像的比例之後，其他東西都用這個比例找
  * 快捷欄 8 格（Shift、Ins、Home、PgUp、Ctrl、Del、End、PgDn）全部記下來，讓使用者選補品放在哪一格
"""
from __future__ import annotations

import json
import logging
import time

import cv2
import numpy as np

from . import calib
from .calib import ASSETS, Calibration

log = logging.getLogger("autopot.autocal")

REFS = ASSETS / "refs"
QS_KEYS = [["shift", "ins", "home", "pgup"], ["ctrl", "del", "end", "pgdn"]]
QS_LABEL = {"shift": "Shift", "ins": "Ins", "home": "Home", "pgup": "PgUp",
            "ctrl": "Ctrl", "del": "Del", "end": "End", "pgdn": "PgDn"}


class CalFail(Exception):
    pass


def _meta() -> dict:
    return json.loads((REFS / "refs.json").read_text(encoding="utf-8"))


def find_scale(img: np.ndarray, ref: np.ndarray, base: float,
               lo: float = 0.45, hi: float = 2.2, n: int = 40) -> tuple[float, float, tuple[int, int]]:
    """在 img 縮成 base*m 倍（m 從 lo 到 hi）時找 ref，回傳 (最好的分數, 最好的倍率 base*m, 位置)。"""
    best = (-1.0, base, (0, 0))
    for m in np.geomspace(lo, hi, n):
        f = base * float(m)
        small = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        if small.shape[0] < ref.shape[0] or small.shape[1] < ref.shape[1]:
            continue
        r = cv2.matchTemplate(small, ref, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(r)
        if mv > best[0]:
            best = (float(mv), f, ml)
    # 在最好的倍率附近再細找一次
    s0, f0, _ = best
    for f in np.linspace(f0 * 0.97, f0 * 1.03, 13):
        small = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        if small.shape[0] < ref.shape[0] or small.shape[1] < ref.shape[1]:
            continue
        r = cv2.matchTemplate(small, ref, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(r)
        if mv > best[0]:
            best = (float(mv), float(f), ml)
    return best


def quickslot_cells(qs_hp, qs_mp) -> dict[str, list[int]]:
    """從參考版面的兩格（Home、PgUp）推算 8 格的位置。格子是正方形排列，左右、上下間距一樣。"""
    pitch = qs_mp[0] - qs_hp[0]
    x_home, y0 = qs_hp[0], qs_hp[1]
    out = {}
    for r, row in enumerate(QS_KEYS):
        for c, k in enumerate(row):
            out[f"qs_{k}"] = [int(x_home + (c - 2) * pitch), int(y0 + r * pitch), int(qs_hp[2]), int(qs_hp[3])]
    return out


class AutoCalibrator:
    def __init__(self, win, cfg: dict, say=None):
        self.win = win
        self.cfg = cfg
        self.say = say or (lambda msg: log.info(msg))
        self.meta = _meta()
        self.F = None                    # 遊戲畫面 → 參考圖 的縮放倍率
        self.cal = Calibration.load()
        self._ref: dict[str, np.ndarray] = {}

    # ---------- 基本工具 ----------
    def ref(self, name):
        if name not in self._ref:
            self._ref[name] = cv2.imread(str(REFS / f"{name}.png"))
        return self._ref[name]

    def shot(self):
        for _ in range(5):
            img = self.win.capture()
            if float(img.std()) > 4:
                return img
            time.sleep(0.4)
        raise CalFail("截圖一直是空白畫面，遊戲視窗可能被縮小了")

    def low(self, img):
        return cv2.resize(img, None, fx=self.F, fy=self.F, interpolation=cv2.INTER_AREA)

    def locate(self, name, img=None, thr: float = 0.72):
        img = self.shot() if img is None else img
        lo = self.low(img)
        t0 = self.ref(name)
        best = None
        for s in (0.94, 0.97, 1.0, 1.03, 1.06):
            t = cv2.resize(t0, None, fx=s, fy=s, interpolation=cv2.INTER_LINEAR) if s != 1 else t0
            if lo.shape[0] < t.shape[0] or lo.shape[1] < t.shape[1]:
                continue
            r = cv2.matchTemplate(lo, t, cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(r)
            if best is None or mv > best[0]:
                best = (mv, ml, t.shape[1], t.shape[0])
        if not best or best[0] < thr:
            return None
        mv, (x, y), w, h = best
        F = self.F
        return mv, [round(x / F), round(y / F), round(w / F), round(h / F)]

    def wait_locate(self, name, timeout=6, thr=0.72):
        end = time.time() + timeout
        while time.time() < end:
            m = self.locate(name, thr=thr)
            if m:
                return m
            time.sleep(0.4)
        return None

    def need(self, m, what):
        if not m:
            raise CalFail(f"畫面上找不到「{what}」")
        return m

    def save_tpl(self, name, box, img=None, inset=1):
        img = self.shot() if img is None else img
        x, y, w, h = box
        c = img[y + inset:y + h - inset, x + inset:x + w - inset]
        calib.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(calib.TEMPLATE_DIR / f"{name}.png"), c)
        self.cal._tpl_cache.pop(name, None)

    def layout(self, name, anchor_ref, anchor_box):
        lx0, ly0, lx1, ly1 = self.meta["layout"][name]
        ax0, ay0 = self.meta["items"][anchor_ref][:2]
        bx, by = anchor_box[:2]
        F = self.F
        return [round(bx + (lx0 - ax0) / F), round(by + (ly0 - ay0) / F),
                round((lx1 - lx0) / F), round((ly1 - ly0) / F)]

    def offset(self, item, anchor_ref):
        x0, y0, x1, y1 = self.meta["items"][item]
        ax0, ay0 = self.meta["items"][anchor_ref][:2]
        return round(((x0 + x1) / 2 - ax0) / self.F), round(((y0 + y1) / 2 - ay0) / self.F)

    def click_box(self, box, double=False):
        x, y, w, h = box
        (self.win.double_click if double else self.win.click)(x + w // 2, y + h // 2)

    # ---------- 步驟 ----------
    def step_scale(self):
        self.say("[1/5] 找遊戲畫面的比例")
        img = self.shot()
        base = self.meta["ref_screen_width"] / self.meta["native_screen_width"]
        score, f, _ = find_scale(img, self.ref("inv_button"), base)
        if score < 0.7:
            raise CalFail(f"找不到下方工具列的道具欄按鈕（最像 {score:.2f}）。遊戲要開著、畫面不要被擋住")
        self.F = f
        self.say(f"  ✔ 畫面比例 {f / base:.2f} 倍（相似度 {score:.2f}）")

    def step_hud(self):
        self.say("[2/5] 下方工具列、HP/MP 條、快捷欄")
        img = self.shot()
        self.cal.data["window_size"] = [img.shape[1], img.shape[0]]
        _, box = self.need(self.locate("inv_button", img), "道具欄按鈕（下方工具列）")
        self.save_tpl("inv_button", box, img)
        for n in ("hp_bar", "mp_bar", "qs_hp", "qs_mp"):
            self.cal.set_region(n, self.layout(n, "inv_button", box))
        for k, r in quickslot_cells(self.cal.region("qs_hp"), self.cal.region("qs_mp")).items():
            self.cal.set_region(k, r)
        from .vision import bar_ratio, crop
        hp = bar_ratio(crop(img, self.cal.region("hp_bar")), "hp")
        mp = bar_ratio(crop(img, self.cal.region("mp_bar")), "mp")
        self.say(f"  ✔ 目前讀到 HP {hp:.0%}、MP {mp:.0%}")
        if not hp > 0.05:
            raise CalFail("HP 條位置好像不對（讀到 0%）。角色是不是死掉了？")
        self.cal.save()
        return box

    def step_inventory(self):
        self.say("[3/5] 打開道具欄 → 現金分頁 → 找妙妙")
        m = self.locate("inv_open")
        if not m:
            _, b = self.need(self.locate("inv_button"), "道具欄按鈕")
            self.click_box(b)
            m = self.need(self.wait_locate("inv_open"), "道具欄視窗（ITEM INVENTORY）")
        _, inv = m
        self.save_tpl("inv_open", inv)
        dx, dy = self.offset("inv_tab_cash", "inv_open")
        self.win.click(inv[0] + dx, inv[1] + dy)
        time.sleep(0.8)
        img = self.shot()
        _, tab = self.need(self.locate("inv_tab_cash", img), "現金分頁")
        self.save_tpl("inv_tab_cash", tab, img)
        self.cal.set_anchor("inv_tab_cash", "inv_open", dx, dy)
        _, mm = self.need(self.locate("miaomiao", img, thr=0.75), "妙妙（現金欄裡要有妙妙）")
        self.save_tpl("miaomiao", mm, img)
        self.cal.save()

    def step_shop(self):
        self.say("[4/5] 雙擊妙妙打開商店")
        s = None
        for attempt in range(3):
            m = self.locate("miaomiao")
            if not m:
                raise CalFail("找不到妙妙")
            self.click_box(m[1], double=True)
            s = self.wait_locate("shop_leave", 8)
            if s:
                break
            self.say("  商店還沒開，再試一次")
        if not s:
            raise CalFail("雙擊妙妙三次都沒有開出商店")
        time.sleep(0.8)
        img = self.shot()
        _, leave = s
        self.save_tpl("shop_leave", leave, img)
        for n in ("shop_buy", "shop_sell"):
            _, b = self.need(self.locate(n, img), n)
            self.save_tpl(n, b, img)
        self.cal.set_region("shop_list", self.layout("shop_list", "shop_leave", leave))
        self.cal.set_region("inv_list", self.layout("inv_list", "shop_leave", leave))
        self.cal.data["shop_leave_pos"] = [int(leave[0]), int(leave[1])]
        sl = self.cal.region("shop_list")
        sx0, sy0 = sl[0] + sl[2] - 20, max(0, sl[1] - 30)
        sub = img[sy0:sl[1] + sl[3] + 30, sx0:sl[0] + sl[2] + 120]
        _, up = self.need(self.locate("list_up", sub), "清單 ▲ 鈕")
        _, dn = self.need(self.locate("list_down", sub), "清單 ▼ 鈕")
        self.save_tpl("list_up", [up[0] + sx0, up[1] + sy0, up[2], up[3]], img, inset=0)
        self.save_tpl("list_down", [dn[0] + sx0, dn[1] + sy0, dn[2], dn[3]], img, inset=0)
        dx, dy = self.offset("shop_tab_use", "shop_leave")
        self.win.click(leave[0] + dx, leave[1] + dy)
        time.sleep(0.8)
        img = self.shot()
        _, tab = self.need(self.locate("shop_tab_use", img), "商店右邊的消耗分頁")
        self.save_tpl("shop_tab_use", tab, img)
        self.cal.set_anchor("shop_tab_use", "shop_leave", dx, dy)
        self.cal.save()

    def step_dialog(self):
        self.say("[5/5] 打開購買數量視窗（會按取消，不會真的買）")
        sl = self.cal.region("shop_list")
        # 點商品清單的第一列 → 購買物品 → 數量視窗
        self.win.click(sl[0] + sl[2] // 3, sl[1] + int(sl[3] * 0.1))
        time.sleep(0.4)
        _, b = self.need(self.locate("shop_buy"), "購買物品")
        self.click_box(b)
        m = self.need(self.wait_locate("qty_dialog", 5), "購買數量視窗")
        time.sleep(0.5)
        img = self.shot()
        _, dlg = m
        self.save_tpl("qty_dialog", dlg, img)
        for n in ("dialog_ok", "dialog_cancel"):
            _, bb = self.need(self.locate(n, img), n)
            self.save_tpl(n, bb, img)
        qi = self.layout("qty_input", "qty_dialog", dlg)
        self.cal.set_region("qty_input", qi)
        self.cal.set_anchor("qty_input", "qty_dialog", qi[0] - dlg[0], qi[1] - dlg[1])
        self.cal.save()
        c = self.locate("dialog_cancel", img)
        if c:
            self.click_box(c[1])
        time.sleep(0.6)
        if self.locate("qty_dialog"):
            raise CalFail("數量視窗沒有關掉，請手動按取消")

    def close_shop(self):
        m = self.locate("shop_leave")
        if m:
            self.click_box(m[1])
            time.sleep(1.0)
        m = self.locate("inv_open")
        if m:
            b = self.locate("inv_button")
            if b:
                self.click_box(b[1])

    def run(self) -> bool:
        self.win.activate()
        time.sleep(1)
        try:
            self.step_scale()
            self.step_hud()
            self.step_inventory()
            self.step_shop()
            self.step_dialog()
            self.close_shop()
        except CalFail as e:
            calib.DATA_DIR.mkdir(parents=True, exist_ok=True)
            try:
                cv2.imwrite(str(calib.DATA_DIR / "autocal_fail.png"), self.shot())
            except Exception:
                pass
            self.say(f"✘ 自動校正失敗：{e}（失敗當下的畫面存在 data/autocal_fail.png）")
            for n in ("dialog_cancel", "shop_leave"):
                try:
                    if self.F:
                        m = self.locate(n)
                        if m:
                            self.click_box(m[1])
                            time.sleep(0.6)
                except Exception:
                    pass
            return False
        self.say("✅ 自動校正完成")
        return True
