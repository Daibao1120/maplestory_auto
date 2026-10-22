"""妙妙商店自動買賣。

每一步都會「看畫面確認」再進行下一步，任何一步失敗就停下並嘗試關閉商店，
絕不在不確定的狀態下亂點。

在真實遊戲裡實測過、這個檔案依賴的行為：
  * 清單右邊的 ▲▼ 點一下剛好捲一列。
  * 捲軸上的藍色滑塊可以直接拖（拖到最上面 = 回到清單頂端，一次完成）。
  * 捲軸滑塊的樣子跟 ▲ 很像（相似度約 0.80），所以找 ▲▼ 的門檻要高於 0.85。
  * 背包常常有十幾列一模一樣的「馴鹿奶 200」，捲一列畫面看起來沒變，
    所以「有沒有捲動」要看滑塊位置，不能只看清單內容。
  * 滑鼠停在清單裡的道具上會跳出說明框，會擋住清單 → 看畫面前先把滑鼠移開。
  * 對話框按「確認」有時第一下沒反應；按 Enter 很穩。
  * 要「先點一下再雙擊」背包道具，才會跳出「要販售幾個？」。
  * 掛機時會一直撿東西，賣出來的空格很快會被撿到的東西佔掉 → 賣完要馬上買。
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

import cv2
import numpy as np

from .calib import Calibration
from .vision import Match, _ink, crop, find, find_all, same_view

log = logging.getLogger("autopot.shop")

ROW_RATIO = 115 / 570      # 實測：一列高 115px，清單區域高 570px（一次顯示 5 列）
ARROW_TH = 0.88            # ▲▼ 的門檻：真的按鈕 0.92~1.0，捲軸滑塊只有 0.80
NOTICE_OK_TH = 0.75        # 提示框「確認」鈕：實測 0.85；畫面上沒有對話框時最高 0.43


class ShopError(RuntimeError):
    pass


def _sleep(a: float, b: float | None = None) -> None:
    time.sleep(random.uniform(a, b if b is not None else a * 1.6))


def _vshift(a: np.ndarray, b: np.ndarray) -> float:
    """估計清單從畫面 a 到 b 往上捲了多少像素（正數 = 內容往上移）。"""
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float32)
    (dx, dy), _ = cv2.phaseCorrelate(ga, gb)
    return -dy


def _diff(a: np.ndarray, b: np.ndarray) -> float:
    if a.size == 0 or b.size == 0 or a.shape != b.shape:
        return 1e9
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


@dataclass
class ScrollBar:
    up: Match
    down: Match

    @property
    def x(self) -> int:
        return (self.up.center[0] + self.down.center[0]) // 2

    @property
    def left(self) -> int:
        return min(self.up.x, self.down.x)

    @property
    def track(self) -> tuple[int, int]:
        return self.up.y + self.up.h + 2, self.down.y - 2


def thumb_span(img: np.ndarray, bar: ScrollBar):
    """找捲軸滑塊的上下緣 (top, bottom)；找不到回傳 None。

    軌道是很均勻的淺灰色，滑塊是深藍色（中間有幾條亮線），
    所以「不是淺灰色」的那一段就是滑塊。"""
    y0, y1 = bar.track
    hw = max(2, bar.up.w // 5)
    band = crop(img, [bar.x - hw, y0, 2 * hw + 1, y1 - y0]).astype(np.int16)
    if band.size == 0:
        return None
    mx, mn = band.max(axis=2), band.min(axis=2)
    track = (mn >= 200) & (mx - mn <= 25)
    rows = track.mean(axis=1) < 0.5
    # 找最長的一段（中間被亮線隔開 4px 以內的算同一段）
    best, start, last = None, None, None
    for i, r in enumerate(list(rows) + [False] * 6):
        if r:
            if start is None:
                start = i
            last = i
        elif start is not None and i - last > 4:
            if best is None or last - start > best[1] - best[0]:
                best = (start, last)
            start = None
    if best is None or best[1] - best[0] < 8:
        return None
    return y0 + best[0], y0 + best[1] + 1


class ShopBot:
    def __init__(self, win, calib: Calibration, cfg: dict, dry_run: bool = False):
        self.win = win
        self.cal = calib
        self.cfg = cfg
        self.dry = dry_run
        self.th = cfg.get("match_threshold", 0.85)
        self.shift = (0, 0)
        self._bars: dict[tuple, ScrollBar | None] = {}
        self._pitches: dict[tuple, float] = {}
        self._drag_px: dict[tuple, int] = {}         # 拖滑塊捲一列要拖幾 px（上次有效的距離）
        self._tap_fails: dict[tuple, int] = {}       # 點捲動鈕連續沒反應幾次（手機版）
        self.scroll_stats = {"tap": 0, "drag": 0, "tap_fail": 0}
        self.bag_full = False
        # 手機版（雷電模擬器）：點一下選取 → 按「販售物品」；背包從 SHORTCUT → 物品 打開；
        # 清單最下面那一列和 ▼ 被畫面下方的工具列擋住一半
        self.touch = bool(cfg.get("touch", False))
        self.row_ratio = float(cfg.get("row_ratio", ROW_RATIO))
        self.arrow_dy = cfg.get("arrow_dy")          # ▲ 到 ▼ 的距離（▼ 被擋住、比對不到時用）
        self.hud_top = cfg.get("hud_top")            # 下方工具列從哪個 y 開始（這以下不要點清單的東西）

    # ---------- 基本工具 ----------
    def shot(self) -> np.ndarray:
        return self.win.capture()

    def find(self, name: str, img=None, region=None, threshold=None) -> Match | None:
        img = self.shot() if img is None else img
        return find(img, self.cal.template(name), threshold or self.th, region)

    def wait_for(self, name: str, timeout: float = 5.0, region=None, gone: bool = False):
        end = time.time() + timeout
        while True:
            m = self.find(name, region=region)
            if (m is None) == gone:
                return True if gone else m
            if time.time() >= end:
                return None
            time.sleep(0.1)

    def click_match(self, m: Match, double: bool = False) -> None:
        cx, cy = m.center
        (self.win.double_click if double else self.win.click)(cx, cy)

    def click_tpl(self, name: str, timeout: float = 3.0, double: bool = False) -> Match:
        m = self.wait_for(name, timeout)
        if not m:
            raise ShopError(f"畫面上找不到「{name}」")
        self.click_match(m, double)
        return m

    def _tap(self, x: int, y: int) -> None:
        """快速點一下（連續點捲動鈕用）。"""
        tap = getattr(self.win, "tap", None)
        if tap:
            tap(x, y)
        else:
            self.win.click(x, y, jitter=0)

    def _park(self) -> None:
        """把滑鼠移到商店的貓咪 NPC 圖上（不會跳說明框、也不會點到東西）。"""
        if not hasattr(self.win, "move_away"):
            return
        lx, ly = self._leave_pos()
        self.win.move_away(lx - 300, ly + 15)
        time.sleep(0.15)          # 等道具說明框消失（實測會慢一點才淡出）

    def _tab_pos(self, name: str, base: str, img):
        """分頁的位置：優先用「相對於 base 的固定位置」（校正時記錄），比模板可靠。
        實測：分頁模板在 0.8 門檻下會配到旁邊長得很像的「裝備」分頁。"""
        a = self.cal.anchor(f"{name}@{base}") or self.cal.anchor(name)
        if a:
            b = self.find_leave(img)[0] if base == "shop_leave" else find(img, self.cal.template(base), self.th)
            if b:
                return b.x + a["dx"], b.y + a["dy"]
        m = find(img, self.cal.template(name), 0.9)
        return m.center if m else None

    @staticmethod
    def _tab_selected(img, pos) -> bool:
        """選中的分頁是粉紅色，沒選中的是灰色（實測粉紅程度 18 vs 0）。"""
        x, y = int(pos[0]), int(pos[1])
        c = crop(img, [x - 35, y - 10, 70, 20]).astype(np.int16)
        if c.size == 0:
            return False
        return float((c[..., 2] - (c[..., 1] + c[..., 0]) / 2).mean()) > 8

    def click_tab(self, name: str, base: str) -> None:
        """點分頁；「消耗」分頁會確認真的切過去了（變粉紅色）才繼續。"""
        verify = name == "shop_tab_use"
        for attempt in range(3):
            img = self.shot()
            pos = self._tab_pos(name, base, img)
            if pos is None:
                raise ShopError(f"找不到分頁「{name}」")
            if verify and self._tab_selected(img, pos):
                return
            self.win.click(*pos)
            _sleep(0.3)
            if not verify:
                return
            self._park()
            if self._tab_selected(self.shot(), pos):
                return
            log.warning("點了「消耗」分頁但好像沒切過去，再點一次")
        self._debug_save("tab_fail", self.shot())
        raise ShopError("點了三次「消耗」分頁都沒有切過去（畫面存在 data/debug/tab_fail_*.png）")

    def dismiss_notices(self, max_n: int = 3) -> int:
        """把跳出的提示框（只有確認鈕、沒有取消鈕）按掉，回傳按了幾個。"""
        n = 0
        for _ in range(max_n):
            img = self.shot()
            # 提示框的確認鈕比較寬，實測相似度只有 0.85 → 用 0.75（沒有對話框時最高只有 0.43）
            ok = self.find("dialog_ok", img, threshold=NOTICE_OK_TH)
            if not ok or self.find("dialog_cancel", img) or self.find("qty_dialog", img):
                break
            log.warning("出現提示框，按確認關掉（畫面存到 data/notice_last.png）")
            try:
                from .calib import DATA_DIR
                cv2.imwrite(str(DATA_DIR / "notice_last.png"), img)
            except Exception:
                pass
            self.win.press("enter")
            if not self._notice_gone():
                self.click_match(ok)
                self._notice_gone()
            n += 1
        return n

    def _notice_gone(self, timeout: float = 1.0) -> bool:
        end = time.time() + timeout
        while True:
            if not self.find("dialog_ok", threshold=NOTICE_OK_TH):
                return True
            if time.time() >= end:
                return False
            time.sleep(0.1)

    # ---------- 商店視窗位置 ----------
    # 商店視窗可以被拖動，每次開的位置不一定一樣，所以清單區域要跟著「離開商店」按鈕走
    def _leave_ref(self):
        """校正時「離開商店」按鈕的位置（由清單區域和參考版面推回去）。"""
        if "shop_leave_pos" in self.cal.data:
            return self.cal.data["shop_leave_pos"]
        import json
        from .calib import ASSETS
        meta = json.loads((ASSETS / "refs" / "refs.json").read_text(encoding="utf-8"))
        sl = self.cal.region("shop_list")
        lay, it = meta["layout"]["shop_list"], meta["items"]["shop_leave"]
        f = sl[2] / (lay[2] - lay[0])        # 參考圖 → 原尺寸 的倍率
        return [round(sl[0] - (lay[0] - it[0]) * f), round(sl[1] - (lay[1] - it[1]) * f)]

    def _leave_pos(self):
        rx, ry = self._leave_ref()
        return rx + self.shift[0], ry + self.shift[1]

    def _ref_scale(self) -> float:
        """參考版面（assets/refs）→ 遊戲畫面的倍率。"""
        import json
        from .calib import ASSETS
        meta = json.loads((ASSETS / "refs" / "refs.json").read_text(encoding="utf-8"))
        lay = meta["layout"]["shop_list"]
        return self.cal.region("shop_list")[2] / (lay[2] - lay[0])

    def _ref_delta(self, a: str, b: str) -> tuple[int, int]:
        """參考版面上 a 的左上角 - b 的左上角（換成遊戲畫面 px）。校正檔有實測值（offsets）就用實測值。"""
        o = self.cal.data.get("offsets", {}).get(f"{a}-{b}")
        if o:
            return int(o[0]), int(o[1])
        import json
        from .calib import ASSETS
        it = json.loads((ASSETS / "refs" / "refs.json").read_text(encoding="utf-8"))["items"]
        f = self._ref_scale()
        return round((it[a][0] - it[b][0]) * f), round((it[a][1] - it[b][1]) * f)

    def find_leave(self, img=None) -> tuple[Match | None, bool]:
        """找「離開商店」按鈕。回傳 (位置, 是否被擋住)。
        10/21 實測：某些地圖畫面上方有「剩餘時間」框，剛好蓋住「離開商店」「購買物品」→ 模板比對不到，
        程式以為商店沒開、一直重新雙擊妙妙。所以按鈕被擋住時，改用右邊的「販售物品」推算位置。"""
        img = self.shot() if img is None else img
        m = self.find("shop_leave", img)
        if m:
            return m, False
        s = self.find("shop_sell", img)
        if s:
            dx, dy = self._ref_delta("shop_leave", "shop_sell")
            t = self.cal.template("shop_leave")
            return Match(s.x + dx, s.y + dy, t.shape[1], t.shape[0], 0.0), True
        return None, False

    def ensure_uncovered(self) -> None:
        """「離開商店」被擋住 → 把商店視窗拖回校正時的位置（拖商店上方的空白處）。拖不動就報錯。"""
        m, covered = self.find_leave()
        if not m or not covered:
            return
        self._debug_save("shop_covered", self.shot())
        log.warning("商店的「離開商店」「購買物品」按鈕被擋住了（例如地圖上方的「剩餘時間」框），把商店視窗拖回原本的位置")
        f = self._ref_scale()
        H = self.shot().shape[0]
        for gx, gy in ((36, -19), (-49, 23), (100, -19)):     # 按鈕上方、左下方空白處（參考版面單位）
            m, covered = self.find_leave()
            if m and not covered:
                break
            if not m:
                raise ShopError("拖商店視窗時商店不見了")
            rx, ry = self._leave_ref()
            dx, dy = rx - m.x, ry - m.y
            if abs(dx) < 20 and abs(dy) < 20:       # 已經在原本的位置還是被擋住 → 往下拖
                dx, dy = 0, int(H * 0.15)
            sx, sy = m.x + gx * f, m.y + gy * f
            if not (0 <= sy < H):
                continue
            self.win.drag(sx, sy, sx + dx, sy + dy)
            _sleep(0.6)
            self._park()
        m, covered = self.find_leave()
        if m and not covered:
            log.info("商店視窗拖好了，按鈕看得到了")
            self._update_shift()
            return
        raise ShopError("商店的「離開商店」按鈕被擋住，而且拖不動商店視窗（畫面存在 data/debug/shop_covered_*.png）。"
                        "請手動把商店視窗往下拖一點、或換一張地圖")

    def _update_shift(self) -> None:
        m, _ = self.find_leave()
        if not m:
            return
        rx, ry = self._leave_ref()
        new = (m.x - rx, m.y - ry)
        if new != self.shift:
            self._bars.clear()
            self._pitches.clear()
        self.shift = new
        if self.shift != (0, 0):
            log.info("商店視窗位置跟校正時差了 %s，清單區域跟著移動", self.shift)

    def region(self, name: str):
        x, y, w, h = self.cal.region(name)
        dx, dy = self.shift if name in ("shop_list", "inv_list") else (0, 0)
        return [x + dx, y + dy, w, h]

    # ---------- 開關商店 ----------
    def shop_is_open(self, img=None) -> bool:
        return self.find_leave(img)[0] is not None

    def _inv_base(self, img=None) -> str | None:
        """道具欄開著嗎？回傳用來定位的模板名稱。
        10/21 實測（手機版）：道具欄開在畫面上方時，標題「ITEM INVENTORY」被「剩餘時間」框蓋住
        → 改用分頁那一排（消耗／裝飾／其他）來認。"""
        img = self.shot() if img is None else img
        if self.find("inv_open", img):
            return "inv_open"
        if self.cal.has_template("inv_tabs") and self.find("inv_tabs", img):
            return "inv_tabs"
        return None

    def open_shop(self) -> None:
        if self.shop_is_open():
            self.ensure_uncovered()
            self._update_shift()
            return
        self.dismiss_notices()
        base = self._inv_base()
        if not base:
            self.click_tpl("inv_button")
            if self.touch:                       # 手機版：SHORTCUT 選單 → 物品
                _sleep(0.5)
                self.click_tpl("shortcut_item", 3)
            end = time.time() + 4
            while not base and time.time() < end:
                time.sleep(0.2)
                base = self._inv_base()
            if not base:
                self._debug_save("inv_not_open", self.shot())
                raise ShopError("道具欄打不開（畫面存在 data/debug/inv_not_open_*.png）")
        _sleep(0.3)
        self.click_tab("inv_tab_cash", base)
        _sleep(0.5)
        mm = self.wait_for("miaomiao", 3)
        if not mm:
            raise ShopError("現金欄裡找不到妙妙")
        for attempt in range(3):
            # 商店其實已經開了（只是按鈕被擋住）就不能再雙擊：那個位置現在是商店裡你的道具，雙擊會跳「要販售嗎？」
            if self.shop_is_open():
                break
            self.click_match(mm, double=True)
            end = time.time() + 8
            opened = False
            while time.time() < end:
                if self.shop_is_open():
                    opened = True
                    break
                time.sleep(0.2)
            if opened:
                _sleep(0.6)
                self.ensure_uncovered()
                self._update_shift()
                return
            if self.dismiss_notices():
                raise ShopError("雙擊妙妙後跳出提示框，商店沒開（可能是點錯道具）")
            log.info("商店還沒開，重試雙擊 (%d)", attempt + 1)
        if self.shop_is_open():
            self.ensure_uncovered()
            self._update_shift()
            return
        self._debug_save("shop_not_open", self.shot())
        raise ShopError("雙擊妙妙三次都沒開出商店（畫面存在 data/debug/shop_not_open_*.png）")

    def close_shop(self) -> None:
        for _ in range(3):
            self.dismiss_notices()
            if self.find("dialog_cancel"):
                self.win.press("esc")
                if not self.wait_for("dialog_cancel", 1, gone=True):
                    c = self.find("dialog_cancel")
                    if c:
                        self.click_match(c)
                _sleep(0.3)
            m, covered = self.find_leave()
            if not m:
                break
            if covered:
                try:
                    self.ensure_uncovered()
                except ShopError as e:
                    log.warning("%s；改按 Esc 關商店", e)
                    self.win.press("esc")
                    _sleep(0.5)
                    continue
                m, covered = self.find_leave()
                if not m or covered:
                    continue
            self.click_match(m)
            end = time.time() + 4
            while time.time() < end and self.shop_is_open():
                time.sleep(0.2)
            if not self.shop_is_open():
                break
        # 收起道具欄，回到原本畫面
        _sleep(0.3)
        if not self.touch and self.cfg.get("close_inventory_after", True) and self.find("inv_open"):
            b = self.find("inv_button")
            if b:
                self.click_match(b)

    # ---------- 清單捲動 ----------
    def _bar(self, list_rect, img=None) -> ScrollBar | None:
        """找清單右側的 ▲▼（找到就記住，商店視窗沒移動就不用再找）。"""
        key = tuple(list_rect)
        if self._bars.get(key) is not None:
            return self._bars[key]
        if img is None:
            self._park()
            time.sleep(0.05)
            img = self.shot()
        x, y, w, h = list_rect
        pad = max(40, w // 6)
        strip = [x + w - pad, max(0, y - h // 3), pad * 2, h + 2 * (h // 3)]
        ups = find_all(img, self.cal.template("list_up"), ARROW_TH, strip)
        downs = find_all(img, self.cal.template("list_down"), ARROW_TH, strip)
        cands = ups + downs
        if not cands:
            log.info("找不到清單的捲動鈕，改用滾輪")
            return None
        top = min(cands, key=lambda m: m.y)
        bot = max(cands, key=lambda m: m.y)
        if self.arrow_dy and bot.y - top.y < h * 0.5:
            # 手機版：▼ 被下方工具列擋住一半 → 用 ▲ 的位置推算；點的時候點露出來的上半部
            t = self.cal.template("list_down")
            bot = Match(top.x, top.y + int(self.arrow_dy), t.shape[1], 8, 0.0)
        if bot.y - top.y < h * 0.5 or abs(top.center[0] - bot.center[0]) > 12:
            log.info("找到的捲動鈕位置不合理，改用滾輪")
            self._dump_arrow_debug(img, strip, list_rect, cands)
            return None
        bar = ScrollBar(top, bot)
        self._bars[key] = bar
        return bar

    def _dump_arrow_debug(self, img, strip, rect, cands) -> None:
        if getattr(self, "_arrow_dumped", False):
            return
        self._arrow_dumped = True
        try:
            from .calib import DATA_DIR
            dbg = img.copy()
            sx, sy, sw, sh = strip
            x, y, w, h = rect
            cv2.rectangle(dbg, (sx, sy), (sx + sw, sy + sh), (0, 0, 255), 3)
            cv2.rectangle(dbg, (x, y), (x + w, y + h), (0, 255, 0), 3)
            for c in cands:
                cv2.rectangle(dbg, (c.x, c.y), (c.x + c.w, c.y + c.h), (255, 0, 255), 3)
            cv2.imwrite(str(DATA_DIR / "arrow_debug.png"), dbg)
            log.info("已存 data/arrow_debug.png（捲動鈕判斷的畫面）")
        except Exception:
            pass

    def _arrow(self, list_rect, which: str, img=None) -> Match | None:
        bar = self._bar(list_rect, img)
        if bar is None:
            return None
        return bar.up if which == "list_up" else bar.down

    def _view(self, img, list_rect) -> np.ndarray:
        """清單內容（不含捲軸，避免捲動鈕的滑鼠效果被當成畫面變化）。"""
        x, y, w, h = list_rect
        bar = self._bars.get(tuple(list_rect))
        if bar is not None:
            w = min(w, bar.left - x - 4)
        return crop(img, [x, y, w, h])

    def _pitch(self, list_rect) -> float:
        """清單一列的高度。用列與列之間的分隔線算，算不出來就用實測比例。"""
        key = tuple(list_rect)
        if key in self._pitches:
            return self._pitches[key]
        default = list_rect[3] * self.row_ratio
        p = default
        try:
            g = cv2.cvtColor(self._view(self.shot(), list_rect), cv2.COLOR_BGR2GRAY).astype(np.float32)
            prof = g[:, : g.shape[1] * 2 // 3].mean(axis=1)
            prof -= prof.mean()
            ac = np.correlate(prof, prof, "full")[len(prof) - 1:]
            lo, hi = int(default * 0.85), int(default * 1.15) + 1
            if hi < len(ac) and ac[0] > 0:
                k = lo + int(np.argmax(ac[lo:hi]))
                if ac[k] / ac[0] > 0.2:
                    p = float(k)
        except Exception:
            pass
        self._pitches[key] = p
        return p

    def _wheel(self, list_rect, clicks: int) -> None:
        x, y, w, h = list_rect
        self.win.wheel(x + w // 3, y + h // 2, clicks)
        self._park()

    def _step(self, list_rect, direction: int) -> int:
        """把清單捲一列（direction=+1 往下、-1 往上），回傳捲了幾列（0 = 捲不動，到底/到頂）。"""
        bar = self._bar(list_rect)
        img = self.shot()
        v0 = self._view(img, list_rect)
        if bar is not None:
            t0 = thumb_span(img, bar)
            arrow = bar.down if direction > 0 else bar.up
            at_end = False
            if t0 is not None:
                lo, hi = bar.track
                at_end = (direction < 0 and t0[0] - lo <= 6) or (direction > 0 and hi - t0[1] <= 6)
            # 滑塊還沒到盡頭卻捲不動 = 點擊被吃掉 → 多試幾次；已經在盡頭就試一次確認即可
            tries = 1 if at_end else (6 if t0 is not None else 3)
            tap_at = arrow.center
            if self.hud_top and arrow.center[1] >= self.hud_top:
                if arrow.y < self.hud_top - 4:
                    # ▼ 下半部被工具列擋住、上面還露出一點 → 點露出來的那一點（10/21 實測 y=907 有效）。
                    # 點一下就捲一列，比拖滑塊快很多（拖滑塊要試好幾次距離）
                    tap_at = (arrow.center[0], self.hud_top - 5)
                else:
                    tries = 0             # ▼ 整個被工具列擋住：點下去會點到工具列 → 直接改拖滑塊
            key = tuple(list_rect)
            if self.touch and not at_end and self._tap_fails.get((key, direction), 0) >= 2:
                tries = 0                 # 這個方向點捲動鈕已經連續沒反應、拖滑塊才有用 → 直接拖（省下每列十幾秒）
            for attempt in range(tries):
                if attempt % 2 == 0:
                    self._tap(*tap_at)
                else:                               # 快速點沒反應 → 改用一般速度點
                    self.win.click(*tap_at, jitter=0)
                for _ in range(3 if self.touch else 6):      # 手機版截圖比較慢，看 3 次就夠
                    time.sleep(0.05)
                    img = self.shot()
                    t1 = thumb_span(img, bar)
                    moved_thumb = t0 is not None and t1 is not None and t1[0] != t0[0]
                    if moved_thumb or not same_view(v0, self._view(img, list_rect)):
                        if moved_thumb and (t1[0] - t0[0]) * direction < 0:
                            log.warning("清單往反方向捲了，停止")
                            return 0
                        self._tap_fails[(key, direction)] = 0
                        self.scroll_stats["tap"] += 1
                        return 1
            if self.touch and t0 is not None and not at_end and getattr(self.win, "drag", None):
                # 手機版：▼ 被工具列整個擋住（點不到）→ 把滑塊往下拖一點點，直到清單動了
                lo, hi = bar.track
                mid = (t0[0] + t0[1]) // 2
                # 上次拖多遠有效就從那裡開始試（10/21 實測：每次都從 4px 試起，一列要拖 5、6 次，掃背包花 12 分鐘）
                key = tuple(list_rect)
                last = self._drag_px.get(key)
                ds = [4, 6, 8, 10, 12, 16, 20]
                if last:
                    ds = [d for d in ds if d >= last] or [last]
                for d in ds:
                    self.win.drag(bar.x, mid, bar.x, mid + d * direction)
                    time.sleep(0.1)
                    img = self.shot()
                    t1 = thumb_span(img, bar)
                    if not same_view(v0, self._view(img, list_rect)):
                        # 方向看滑塊（清單每列長得很像時，用畫面算位移會算錯方向）
                        if t1 is not None and (t1[0] - t0[0]) * direction < 0:
                            log.warning("清單往反方向捲了，停止")
                            return 0
                        self._drag_px[key] = d
                        self.scroll_stats["drag"] += 1
                        if tries:
                            self.scroll_stats["tap_fail"] += 1
                            n = self._tap_fails.get((key, direction), 0) + 1
                            self._tap_fails[(key, direction)] = n
                            if n == 2:
                                log.info("點捲動鈕沒反應、拖滑塊才捲得動 → 這個清單之後直接拖滑塊")
                        return 1
                self._drag_px.pop(key, None)
            lo, hi = bar.track
            log.info("清單捲不動了：滑塊 %s、軌道 %s、試了 %d 次%s", t0, (lo, hi), tries,
                     "（滑塊已在盡頭）" if at_end else "（滑塊不在盡頭！）")
            if True:
                try:
                    x, y, w, h = list_rect
                    dbg = img[max(0, y - 40): y + h + 40, x: bar.x + 40].copy()
                    if t0 is not None:
                        cv2.rectangle(dbg, (bar.x - x - 12, t0[0] - y + 40), (bar.x - x + 12, t0[1] - y + 40), (0, 0, 255), 2)
                    cv2.line(dbg, (bar.x - x - 20, lo - y + 40), (bar.x - x + 20, lo - y + 40), (0, 255, 0), 2)
                    cv2.line(dbg, (bar.x - x - 20, hi - y + 40), (bar.x - x + 20, hi - y + 40), (0, 255, 0), 2)
                    self._debug_save("scroll_stuck", dbg)
                except Exception:
                    pass
            return 0
        # 沒有捲動鈕：用滾輪（較不可靠）
        for attempt in range(3):
            self._wheel(list_rect, -direction)
            for _ in range(4):
                time.sleep(0.1)
                v1 = self._view(self.shot(), list_rect)
                if not same_view(v0, v1):
                    moved = _vshift(v0, v1)
                    if moved * direction < -20:
                        log.warning("清單往反方向捲了（%.0f px），停止", moved)
                        return 0
                    return max(1, round(abs(moved) / self._pitch(list_rect)))
        return 0

    def scroll_end(self, list_rect, direction: int) -> None:
        """捲到清單最上面（-1）或最下面（+1）。直接拖捲軸滑塊，一次到位。"""
        bar = self._bar(list_rect)
        drag = getattr(self.win, "drag", None)
        if bar is not None and drag is not None:
            t = thumb_span(self.shot(), bar)
            if t is not None:
                lo, hi = bar.track
                drag(bar.x, (t[0] + t[1]) // 2, bar.x, lo - 4 if direction < 0 else hi + 4)
                time.sleep(0.1)
                self._park()
        for _ in range(300):
            if not self._step(list_rect, direction):
                return

    def scroll_top(self, list_rect) -> None:
        self.scroll_end(list_rect, -1)

    def scan(self, list_rect, names: list[str], on_view=None, max_steps: int = 300, th=None):
        """從頂端一路往下掃，回傳 {名稱: [絕對 y 位置...]}（同一列只算一次）。

        on_view(view, abs_offset) 若回傳 True 就提早停止。
        位置用「捲了幾列 x 列高」計算，不靠比對畫面，所以一模一樣的列也不會算錯。
        """
        self.scroll_end(list_rect, -1)
        pitch = self._pitch(list_rect)
        found: dict[str, list[float]] = {n: [] for n in names}
        row = 0
        self._park()
        view = self._view(self.shot(), list_rect)
        for _ in range(max_steps):
            offset = row * pitch
            for n in names:
                for m in find_all(view, self.cal.template(n), th or self.th, robust=True):
                    ay = offset + m.y
                    if all(abs(ay - v) > pitch * 0.5 for v in found[n]):
                        found[n].append(ay)
            if on_view and on_view(view, offset):
                break
            k = self._step(list_rect, +1)
            if not k:
                log.info("清單掃到底了（往下捲了 %d 列）", row)
                break
            row += k
            view = self._view(self.shot(), list_rect)
        return found

    def locate_in_list(self, list_rect, name: str, th=None) -> Match | None:
        """捲到該項目出現在畫面上，回傳它在視窗中的位置。"""
        hit: list[Match] = []

        def stop(view, _off):
            m = find(view, self.cal.template(name), th or self.th, robust=True)
            if m:
                m.x += list_rect[0]
                m.y += list_rect[1]
                hit.append(m)
                return True
            return False

        self.scan(list_rect, [name], on_view=stop, th=th)
        return hit[0] if hit else None

    # ---------- 對話框 ----------
    def _confirm(self) -> None:
        """確認目前的數量/販售對話框（試跑時按取消）。

        對話框有「取消」鈕，提示框沒有 → 用「取消」鈕消失來判斷對話框已關閉，
        這樣不會把接著跳出的提示框（例如背包已滿）誤當成成功。"""
        if not self.wait_for("dialog_cancel", 2):
            raise ShopError("要確認的對話框不見了")
        key = "esc" if self.dry else "enter"
        if self.dry:
            log.info("[試跑] 按取消，不真的成交")
        for attempt in range(3):
            self.win.press(key)
            if self.wait_for("dialog_cancel", 1.2, gone=True):
                _sleep(0.15)
                return
            # Enter 沒反應 → 改點按鈕
            b = self.find("dialog_cancel" if self.dry else "dialog_ok")
            if b:
                self.click_match(b)
                if self.wait_for("dialog_cancel", 1.2, gone=True):
                    _sleep(0.15)
                    return
        raise ShopError("對話框按了三次都關不掉")

    def _cancel_dialog(self) -> None:
        for _ in range(3):
            if not self.find("dialog_cancel"):
                return
            self.win.press("esc")
            if self.wait_for("dialog_cancel", 1, gone=True):
                return
            c = self.find("dialog_cancel")
            if c:
                self.click_match(c)
            _sleep(0.3)

    # ---------- 輸入數量 ----------
    def _qty_input_point(self, dlg: Match) -> tuple[int, int]:
        a = self.cal.anchor("qty_input")
        if a:
            r = self.cal.region("qty_input")
            return dlg.x + a["dx"] + r[2] // 2, dlg.y + a["dy"] + r[3] // 2
        r = self.cal.region("qty_input")
        return r[0] + r[2] // 2, r[1] + r[3] // 2

    def _enter_qty(self, qty: int) -> None:
        dlg = self.wait_for("qty_dialog", 4)
        if not dlg:
            raise ShopError("數量視窗沒有出現")
        # 點在輸入框「最右邊」，游標才會在文字最後面（這個遊戲的輸入框不支援 End 鍵）
        fx, fy, fw, fh = self._field_rect(dlg)
        self.win.click(fx + fw - max(8, fw // 30), fy + fh // 2, jitter=0)
        _sleep(0.1)
        self.win.press("backspace", 10)
        self.win.press("delete", 10)      # 游標右邊如果還有字也刪掉
        # 滑鼠移開，避免手形游標被當成字
        self.win.move_away(fx + fw // 2, fy - fh * 3)
        _sleep(0.15)
        if self._field_segments(dlg) != 0:
            self._save_field_debug(dlg)
            self._cancel_dialog()
            raise ShopError("數量框清不乾淨，為了安全取消購買（畫面存在 data/qty_field.png）")
        d_prev = self._field_dark(dlg)
        text = str(int(qty))
        incs = []
        # 一個字一個字打，每打一個字，框裡的黑色就要明顯增加（被輸入法吃掉或打不進去就不會增加）
        for ch in text:
            self.win.type_text(ch)
            _sleep(0.1)
            d = self._field_dark(dlg)
            incs.append(d - d_prev)
            d_prev = d
        segs = self._field_segments(dlg)
        self._save_field_debug(dlg)
        log.info("數量框檢查：每個字增加的深色像素 %s，看到 %d 個字（應該 %d 個）", incs, segs, len(text))
        if min(incs) < 15:
            self._cancel_dialog()
            raise ShopError("有數字沒有打進數量框（可能被輸入法吃掉），為了安全取消購買")
        if segs != len(text):
            self._cancel_dialog()
            raise ShopError(f"數量框裡看到 {segs} 個字，應該是 {len(text)} 個（9999 可能沒刪乾淨），為了安全取消購買")

    def _field_segments(self, dlg: Match) -> int:
        """數量框中間那一條，數有幾段深色（= 幾個字），忽略左右邊框和游標。"""
        x, y, w, h = self._field_rect(dlg)
        m = max(4, w // 40)
        # 看整個字高（上下只去掉邊框）：10/21 實測手機版只看中間一半時，「0」底下那一橫剛好切到一半，
        # 一個「0」被算成兩段 →「1500」看成 5 個字
        box = crop(self.shot(), [x + m, y + h // 8, w - 2 * m, h - 2 * (h // 8)])
        if box.size == 0:
            return -1
        g = cv2.cvtColor(box, cv2.COLOR_BGR2GRAY)
        cols = list(((g < 110).sum(axis=0) >= 2)) + [False]
        min_w = max(2, h // 16)          # 手機版的「1」只有 2px 寬
        runs, start = [], None
        for i, c in enumerate(cols):
            if c and start is None:
                start = i
            elif not c and start is not None:
                runs.append([start, i])
                start = None
        # 同一個字裡面的 1px 空隙要併起來（10/21 實測手機版「0」的右上/右下角是淡灰色，中間斷 1px）。
        # 字跟字之間至少空 3px
        merged = []
        for r in runs:
            if merged and r[0] - merged[-1][1] <= 1:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        return sum(1 for a, b in merged if b - a >= min_w)

    def _save_field_debug(self, dlg: Match) -> None:
        try:
            from .calib import DATA_DIR
            x, y, w, h = self._field_rect(dlg)
            cv2.imwrite(str(DATA_DIR / "qty_field.png"), crop(self.shot(), [x - 20, y - 20, w + 40, h + 40]))
        except Exception:
            pass

    def _field_dark(self, dlg: Match) -> int:
        """數量框中間那一條的深色像素數（白底黑字）。"""
        x, y, w, h = self._field_rect(dlg)
        box = crop(self.shot(), [x, y + h // 4, w, h // 2])
        if box.size == 0:
            return 0
        g = cv2.cvtColor(box, cv2.COLOR_BGR2GRAY)
        return int((g < 110).sum())

    def _field_rect(self, dlg: Match):
        a = self.cal.anchor("qty_input")
        r = self.cal.region("qty_input")
        if a:
            return [dlg.x + a["dx"], dlg.y + a["dy"], r[2], r[3]]
        return r

    def _click_buy(self) -> None:
        """按「購買物品」：找不到模板（被擋住）就用「離開商店」下面的固定位置。"""
        m = self.wait_for("shop_buy", 1.5)
        if not m:
            lv, covered = self.find_leave()
            if not lv or covered:
                raise ShopError("畫面上找不到「購買物品」按鈕")
            dx, dy = self._ref_delta("shop_buy", "shop_leave")
            m = Match(lv.x + dx, lv.y + dy, lv.w, lv.h, 0.0)
        self.click_match(m)

    # ---------- 買 ----------
    def buy(self, tpl_name: str, qty: int, th: float | None = None, verify: bool = False) -> bool:
        """買 qty 個。背包裝不下時遊戲會只買一部分並跳提示 → 回傳 False（bag_full=True）。"""
        if qty <= 0:
            return True
        per = int(self.cfg.get("max_per_purchase", 9999))
        left = int(qty)
        rect = self.region("shop_list")
        while left > 0:
            n = min(per, left)
            t0 = time.time()
            log.info("在商店清單找 %s…", tpl_name)
            item = self.locate_in_list(rect, tpl_name, th=th)
            if item:
                log.info("找到 %s（花了 %.0f 秒）", tpl_name, time.time() - t0)
            if not item:
                raise ShopError(f"商店清單裡找不到「{tpl_name}」")
            if verify:
                # 掃描學到的商品：再逐點確認一次真的是同一個名稱（名稱很像的商品，寧可不買也不能買錯）
                from .catalog import same_item
                got = crop(self.shot(), [item.x, item.y, item.w, item.h])
                if not same_item(self.cal.template(tpl_name), got, min_cc=0.85, max_diff=0.15):
                    self._debug_save("buy_verify_fail", got)
                    raise ShopError(f"找到的商品跟設定的「{tpl_name}」不完全一樣，為了安全不買")
            self.click_match(item)
            _sleep(0.15)
            self._click_buy()
            self._enter_qty(n)
            self._confirm()
            if self.dismiss_notices():
                # 實測：跳出的是「背包空間或楓幣不足，僅購買了 N 個」→ 只買到一部分（背包滿了）
                log.warning("購買 %s x%d：背包空間不足，只買到一部分（畫面存在 data/notice_last.png）", tpl_name, n)
                self.bag_full = True
                return False
            log.info("%s %s x%d", "[試跑]" if self.dry else "已購買", tpl_name, n)
            left -= n
        return True

    # ---------- 賣 ----------
    def _debug_save(self, name: str, img) -> None:
        """出問題時存圖（每種最多 5 張），之後拿來看到底發生什麼事。"""
        try:
            from .calib import DATA_DIR
            cnt = getattr(self, "_dbg_cnt", {})
            self._dbg_cnt = cnt
            cnt[name] = cnt.get(name, 0) + 1
            if cnt[name] <= 5:
                d = DATA_DIR / "debug"
                d.mkdir(exist_ok=True)
                cv2.imwrite(str(d / f"{name}_{cnt[name]}.png"), img)
        except Exception:
            pass

    def _same_list(self, before, after, y_mid: float, pitch: float, tpl: str):
        """點選一列之後，清單有沒有動？
        被選取的那一列會變橘色，所以比對「其他列」有沒有變；
        再確認選取的那一列還是同一個道具（只看深色字，不受橘色影響）。"""
        h = min(before.shape[0], after.shape[0])
        y0, y1 = max(0, int(y_mid - pitch * 0.6)), min(h, int(y_mid + pitch * 0.6))
        parts_b = [before[:y0], before[y1:h]]
        parts_a = [after[:y0], after[y1:h]]
        # 只比「深色字」：選取的橘色底（包括之前選過的列）不會被當成變化。單位：不同的像素 %
        diffs = [float((_ink(b) != _ink(a)).mean() * 100) for b, a in zip(parts_b, parts_a)
                 if b.size and b.shape == a.shape]
        others = max(diffs) if diffs else 0.0
        band = after[y0:y1]
        m = find(band, self.cal.template(tpl), 0.0, robust=True)
        score = m.score if m else 0.0
        return others, score

    def _sell_row(self, m: Match, tpl: str, rect, th: float, view_before=None) -> str:
        """賣掉畫面上 m 那一列（整組）。回傳 'sold' / 'changed'（道具資訊已變更）/ 'moved'（清單動了，沒賣）。"""
        cx, cy = m.center
        pitch = self._pitch(rect)
        for attempt in range(3):
            # 實測：先點一下選取，再雙擊，才會跳出「要販售幾個？」
            self.win.click(cx, cy)
            _sleep(0.1)
            # 雙擊前再確認一次：清單沒有在這一瞬間移動（掛機一直撿東西，清單隨時可能動）
            self._park()
            if view_before is not None:
                after = self._view(self.shot(), rect)
                others, score = self._same_list(view_before, after, cy - rect[1], pitch, tpl)
                if others > 2.0 or score < 0.6:
                    log.warning("要賣的那一列可能移動了（其他列差異 %.1f%%、這列相似度 %.2f），重新看一次清單", others, score)
                    self._debug_save("verify_fail", np.vstack([view_before, after]))
                    return "moved"
            if self.touch:
                # 手機版：選取後按「販售物品」（實測：這樣就會跳出「要販售幾個？」）
                b = self.find("shop_sell")
                if not b:
                    raise ShopError("找不到「販售物品」按鈕")
                self.click_match(b)
            else:
                self.win.double_click(cx, cy)
            if self.wait_for("dialog_cancel", 1.5):
                break
            if self.dismiss_notices():
                return "changed"
            view_before = None if view_before is None else self._view(self.shot(), rect)
        else:
            raise ShopError("雙擊道具三次都沒有出現販售視窗")
        self._confirm()
        if self.dismiss_notices():
            return "changed"
        return "sold"

    def sell_all(self, items: list[dict], max_restarts: int = 3) -> dict[str, int]:
        """一次掃過背包，把白名單道具賣掉（每種保留最上面 keep_stacks 組）。

        items: [{"template": ..., "keep_stacks": n}, ...]；回傳 {template: 賣掉幾組}。
        只會賣模板完全符合（sell_threshold）的道具，沒列在白名單的一律不碰。"""
        sold = {it["template"]: 0 for it in items}
        sth = self.cfg.get("sell_threshold", 0.93)   # 比買東西嚴格，避免把相似道具賣掉
        for restart in range(max_restarts + 1):
            rect = self.region("inv_list")
            self.click_tab("shop_tab_use", "shop_leave")
            _sleep(0.2)
            r = self._sell_pass(rect, items, sold, sth)
            if r == "done":
                return sold
            log.info("販售失敗（背包資料已變更），重開商店再試")
            self.close_shop()
            self.open_shop()
        log.warning("背包一直在變，賣東西重試 %d 次後放棄", max_restarts)
        return sold

    def _sell_pass(self, rect, items, sold, sth) -> str:
        """從上往下掃一遍，邊掃邊賣。

        「要保留的那幾組」怎麼判斷（不靠位置計算，因為清單會因為賣掉、撿到東西而移動）：
          above[t] = 已經「確定」捲出畫面上方的 t 有幾組
          畫面上由上往下第 i 個 t 的序號 = above[t] + i；序號 < 保留組數 → 留著
        above 只在「確定」的時候才增加（滑塊往下動了、而且內容剛好往上移一列），
        任何不確定的情況都寧可少算 → 多留，絕不多賣。"""
        log.info("從背包最上面開始掃，找要賣的東西…")
        self.scroll_end(rect, -1)
        pitch = self._pitch(rect)
        bar = self._bar(rect)
        above = {it["template"]: 0 for it in items}
        demo = {it["template"]: 0 for it in items}       # 試跑：示範過的當作保留
        stuck = moved = 0
        for _ in range(800):
            self._park()
            img = self.shot()
            view = self._view(img, rect)
            hits = {it["template"]: sorted(find_all(view, self.cal.template(it["template"]),
                                                    max(sth, float(it.get("threshold") or 0)), robust=True),
                                           key=lambda m: m.y) for it in items}
            target = None
            for it in items:
                t = it["template"]
                keep = int(it.get("keep_stacks", 0)) + demo[t]
                for i, m in enumerate(hits[t]):
                    if above[t] + i >= keep:
                        target = (t, m)
                        break
                if target:
                    break
            if target is None:
                t0 = thumb_span(img, bar) if bar else None
                if not self._step(rect, +1):
                    log.info("背包掃完了（%s）", "、".join(f"{t} 賣 {n} 組" for t, n in sold.items()))
                    return "done"
                img2 = self.shot()
                t1 = thumb_span(img2, bar) if bar else None
                if self._shifted_one_row(view, self._view(img2, rect), pitch, t0, t1):
                    for t, ms in hits.items():
                        if any(m.y + m.h / 2 < pitch for m in ms):   # 最上面那列是 t → 它捲出去了
                            above[t] += 1
                continue
            t, m = target
            abs_m = Match(m.x + rect[0], m.y + rect[1], m.w, m.h, m.score)
            res = self._sell_row(abs_m, t, rect, sth, view_before=view)
            if res == "changed":
                return "changed"
            if res == "moved":
                moved += 1
                if moved >= 3:      # 一直確認不了 → 這組先不賣（寧可少賣，不可賣錯）
                    log.warning("確認不了要賣的 %s，這組先跳過", t)
                    demo[t] += 1
                    moved = 0
                continue
            moved = 0
            if self.dry:
                demo[t] += 1
                sold[t] += 1
                log.info("[試跑] 示範賣 %s 1 組", t)
                continue
            self._park()
            after = self._view(self.shot(), rect)
            if same_view(view, after, tol=0.5):
                # 按了確認但東西沒少（例如不能賣的道具）→ 不要一直重試同一組
                stuck += 1
                log.warning("賣 %s 之後背包沒有變化", t)
                if stuck >= 2:
                    demo[t] += 1
                    stuck = 0
                continue
            stuck = 0
            sold[t] += 1
            log.info("賣掉 %s 1 組", t)
            # 賣掉的如果在清單最底，畫面會往下移一列，捲出去的那組可能又出現在最上面。
            # 保守處理：最上面那列是要保留的道具 → 少算一組「已在上方」（寧可多留）
            for t2, ms in self._top_row_hits(after, items, sth, pitch).items():
                if ms and above[t2] > 0:
                    above[t2] -= 1
        return "done"

    def _top_row_hits(self, view, items, sth, pitch):
        out = {}
        for it in items:
            t = it["template"]
            th = max(sth, float(it.get("threshold") or 0))
            out[t] = [m for m in find_all(view, self.cal.template(t), th, robust=True) if m.y + m.h / 2 < pitch]
        return out

    @staticmethod
    def _shifted_one_row(before, after, pitch, t0, t1) -> bool:
        """確定清單剛好往下捲了一列：滑塊往下動了，而且內容剛好往上移一列。
        只比深色字、容許 ±3px 誤差（實測列高不一定剛好是整數）。"""
        if t0 is None or t1 is None or t1[0] <= t0[0]:
            return False
        h = min(before.shape[0], after.shape[0])
        ib, ia = _ink(before[:h]), _ink(after[:h])
        best = 100.0
        p0 = int(round(pitch))
        for p in range(p0 - 3, p0 + 4):
            if 0 < p < h:
                best = min(best, float((ib[p:] != ia[:h - p]).mean() * 100))
        return best < 3.0

    def sell(self, tpl_name: str, keep_stacks: int = 0, max_stacks: int = 50) -> int:
        """賣單一種道具（相容舊介面）。"""
        return self.sell_all([{"template": tpl_name, "keep_stacks": keep_stacks}])[tpl_name]
