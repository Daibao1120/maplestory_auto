"""模擬遊戲畫面，測試整個補貨流程（不需要遊戲）。

假遊戲會依照狀態畫出按鈕、清單、對話框；程式的點擊會改變狀態。
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import autopot.calib as calib  # noqa: E402
import autopot.shop as shopmod  # noqa: E402

RNG = np.random.default_rng(42)


def patch(h=30, w=80):
    p = RNG.integers(0, 255, (h, w, 3), dtype=np.uint8)
    p[:3] = 255
    p[:, :3] = 0
    return p


NAMES = ["inv_button", "inv_open", "inv_tab_cash", "miaomiao", "decoy_item", "shop_leave", "shop_buy",
         "shop_sell", "shop_tab_use", "list_up", "list_down", "qty_dialog", "dialog_ok", "dialog_cancel",
         "notice_text"]
P = {n: patch(24 if n.startswith("list_") else 30, 24 if n.startswith("list_") else 80) for n in NAMES}
P["shop_tab_use"][..., 2] = 255      # 選中的「消耗」分頁是粉紅色（真實遊戲也是）
SHOP_ITEMS = [f"item{i}" for i in range(30)]
SHOP_ITEMS[18] = "buy_milk"
SHOP_ITEMS[24] = "buy_ice"
ROW = {n: patch(36, 200) for n in SHOP_ITEMS}
INV_ITEMS = ["sell_junk", "keep_me", "sell_junk", "keep_me2", "sell_junk", "other", "other2", "other3"]
INVROW = {n: patch(36, 200) for n in set(INV_ITEMS)}


class FakeGame:
    W, H = 1400, 900
    POS = {
        "inv_button": (900, 840), "inv_open": (300, 100), "inv_tab_cash": (480, 140),
        "decoy_item": (300, 200), "miaomiao": (390, 200),
        "shop_leave": (500, 90), "shop_buy": (500, 130), "shop_sell": (900, 130), "shop_tab_use": (760, 180),
        "qty_dialog": (600, 400), "dialog_ok": (620, 480), "dialog_cancel": (720, 480), "notice_text": (600, 400),
    }
    SHOP_LIST = (300, 230, 210, 5 * 40)    # x,y,w,h，一次顯示 5 列
    INV_LIST = (750, 230, 210, 5 * 40)

    def __init__(self):
        self.bg = RNG.integers(20, 60, (self.H, self.W, 3), dtype=np.uint8)
        self.inv_open = False
        self.shop = False
        self.dialog = None          # None / "qty" / "sellqty" / "notice"
        self.typed = ""
        self.selected = None
        self.shop_off = 7
        self.inv_off = 0
        self.inv_items = list(INV_ITEMS)
        self.bought = {}
        self.sold = []
        self.space_left = 10 ** 9
        self.decoy_clicks = 0
        self.fg = 1

    # ---- 畫面 ----
    def _put(self, img, name, pos=None):
        x, y = pos or self.POS[name]
        p = P[name]
        img[y:y + p.shape[0], x:x + p.shape[1]] = p

    def _list(self, img, rect, items, rows, off):
        x, y, w, h = rect
        img[y:y + h, x:x + w] = 230
        for i in range(5):
            j = off + i
            if j < len(items):
                img[y + i * 40 + 2:y + i * 40 + 38, x + 5:x + 205] = rows[items[j]]
        self._put(img, "list_up", (x + w + 5, y))
        self._put(img, "list_down", (x + w + 5, y + h - 24))

    def capture(self):
        img = self.bg.copy()
        self._put(img, "inv_button")
        if self.inv_open and not self.shop:
            for n in ("inv_open", "inv_tab_cash", "decoy_item", "miaomiao"):
                self._put(img, n)
        if self.shop:
            for n in ("shop_leave", "shop_buy", "shop_sell", "shop_tab_use"):
                self._put(img, n)
            self._list(img, self.SHOP_LIST, SHOP_ITEMS, ROW, self.shop_off)
            self._list(img, self.INV_LIST, self.inv_items, INVROW, self.inv_off)
        if self.dialog in ("qty", "sellqty"):
            if self.dialog == "qty":
                self._put(img, "qty_dialog")
                img[440:460, 640:740] = 255                  # 數量框（白底）
                for i, _ch in enumerate(self.typed[:9]):
                    img[444:456, 646 + i * 10:652 + i * 10] = 0   # 每個字一塊黑色
                img[440:442, 640:740] = 80                   # 框線（固定）
                img[443:457, 646 + len(self.typed) * 10] = 0      # 游標（很窄）
            self._put(img, "dialog_ok")
            self._put(img, "dialog_cancel")
        elif self.dialog == "notice":
            self._put(img, "notice_text")
            self._put(img, "dialog_ok")
        return img

    # ---- 輸入 ----
    def _hit(self, name, x, y, pos=None):
        px, py = pos or self.POS[name]
        h, w = P[name].shape[:2]
        return px <= x < px + w and py <= y < py + h

    def _row_at(self, rect, x, y, off, items):
        rx, ry, rw, rh = rect
        if rx <= x < rx + rw and ry <= y < ry + rh:
            j = off + int((y - ry) // 40)
            return items[j] if j < len(items) else None
        return None

    def click(self, x, y, jitter=0):
        if self.dialog:
            if self._hit("dialog_ok", x, y):
                if self.dialog == "qty":
                    q = int(self.typed or 0)
                    if q > self.space_left:
                        self.dialog = "notice"
                        return
                    self.bought[self.selected] = self.bought.get(self.selected, 0) + q
                    self.space_left -= q
                    self.dialog = None
                elif self.dialog == "sellqty":
                    idx = self.inv_off + self._sel_row
                    self.sold.append(self.inv_items.pop(idx))
                    self.inv_off = max(0, min(self.inv_off, len(self.inv_items) - 5))
                    self.dialog = None
                else:
                    self.dialog = None
            elif self.dialog != "notice" and self._hit("dialog_cancel", x, y):
                self.dialog = None
            elif self.dialog == "qty":
                self.typed = self.typed  # 點輸入框
            return
        if self._hit("inv_button", x, y):
            self.inv_open = not self.inv_open
            return
        if self.shop:
            if self._hit("shop_leave", x, y):
                self.shop = False
            elif self._hit("shop_buy", x, y) and self.selected in SHOP_ITEMS:
                self.dialog, self.typed = "qty", "9999"
            elif self._hit("shop_sell", x, y) and self.selected == "__inv__":
                self.dialog = "sellqty"
            sx, sy, sw, sh = self.SHOP_LIST
            if self._hit("list_up", x, y, (sx + sw + 5, sy)):
                self.shop_off = max(0, self.shop_off - 1)
            if self._hit("list_down", x, y, (sx + sw + 5, sy + sh - 24)):
                self.shop_off = min(len(SHOP_ITEMS) - 5, self.shop_off + 1)
            ix, iy, iw, ih = self.INV_LIST
            if self._hit("list_up", x, y, (ix + iw + 5, iy)):
                self.inv_off = max(0, self.inv_off - 1)
            if self._hit("list_down", x, y, (ix + iw + 5, iy + ih - 24)):
                self.inv_off = max(0, min(len(self.inv_items) - 5, self.inv_off + 1))
            r = self._row_at(self.SHOP_LIST, x, y, self.shop_off, SHOP_ITEMS)
            if r:
                self.selected = r
            r = self._row_at(self.INV_LIST, x, y, self.inv_off, self.inv_items)
            if r:
                self.selected = "__inv__"
                self._sel_row = int((y - iy) // 40)

    def double_click(self, x, y):
        if self.shop and not self.dialog:
            ix, iy, iw, ih = self.INV_LIST
            r = self._row_at(self.INV_LIST, x, y, self.inv_off, self.inv_items)
            if r:
                self._sel_row = int((y - iy) // 40)
                self.dialog = "sellqty"
                return
        if self.inv_open and not self.shop and self._hit("miaomiao", x, y):
            self.shop = True
        elif self.inv_open and self._hit("decoy_item", x, y):
            self.decoy_clicks += 1
            self.dialog = "notice"

    def press(self, key, times=1):
        if self.dialog == "qty":
            for _ in range(times):
                if key == "backspace":
                    self.typed = self.typed[:-1]
        if key == "esc":
            self.dialog = None

    def move_away(self, x, y):
        pass

    def wheel(self, x, y, clicks):
        step = -1 if clicks > 0 else 1
        for _ in range(abs(clicks)):
            sx, sy, sw, sh = self.SHOP_LIST
            ix, iy, iw, ih = self.INV_LIST
            if sx <= x < sx + sw and sy <= y < sy + sh:
                self.shop_off = max(0, min(len(SHOP_ITEMS) - 5, self.shop_off + step))
            elif ix <= x < ix + iw and iy <= y < iy + ih:
                self.inv_off = max(0, min(max(0, len(self.inv_items) - 5), self.inv_off + step))

    def type_text(self, t):
        if self.dialog == "qty":
            self.typed += t

    # Runner 會用到的
    def activate(self): pass
    def foreground(self): return self.fg
    def restore_foreground(self, h): pass
    def mouse_pos(self): return (0, 0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    tdir = tmp_path / "templates"
    tdir.mkdir()
    for n, p in P.items():
        cv2.imwrite(str(tdir / f"{n}.png"), p)
    for n in ("buy_milk", "buy_ice"):
        cv2.imwrite(str(tdir / f"{n}.png"), ROW[n][2:30, 2:150])
    cv2.imwrite(str(tdir / "sell_junk.png"), INVROW["sell_junk"][2:30, 60:190])
    monkeypatch.setattr(calib, "TEMPLATE_DIR", tdir)
    monkeypatch.setattr(shopmod, "_sleep", lambda *a, **k: None)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    g = FakeGame()
    cal = calib.Calibration({"regions": {
        "shop_list": list(g.SHOP_LIST), "inv_list": list(g.INV_LIST),
        "qty_input": [640, 440, 100, 20],
    }, "anchors": {}, "shop_leave_pos": list(g.POS["shop_leave"])})
    cfg = {"match_threshold": 0.9, "max_per_purchase": 9999, "close_inventory_after": True}
    return g, cal, cfg


def test_full_buy(env):
    g, cal, cfg = env
    bot = shopmod.ShopBot(g, cal, cfg)
    bot.open_shop()
    assert g.shop and g.decoy_clicks == 0
    assert bot.buy("buy_milk", 3210)
    assert bot.buy("buy_ice", 400)
    bot.close_shop()
    assert g.bought == {"buy_milk": 3210, "buy_ice": 400}
    assert not g.shop and not g.inv_open


def test_dry_run_buys_nothing(env):
    g, cal, cfg = env
    bot = shopmod.ShopBot(g, cal, cfg, dry_run=True)
    bot.open_shop()
    bot.buy("buy_milk", 100)
    bot.close_shop()
    assert g.bought == {} and not g.shop


def test_split_large_purchase(env):
    g, cal, cfg = env
    bot = shopmod.ShopBot(g, cal, dict(cfg, max_per_purchase=4000))
    bot.open_shop()
    bot.buy("buy_milk", 9000)
    assert g.bought["buy_milk"] == 9000


def test_inventory_full_notice_is_handled(env):
    g, cal, cfg = env
    g.space_left = 50
    bot = shopmod.ShopBot(g, cal, cfg)
    bot.open_shop()
    assert bot.buy("buy_milk", 100) is False   # 背包滿 → 回報沒買完，提示框要被按掉
    assert g.dialog is None
    assert g.bought == {}
    bot.close_shop()
    assert not g.shop


def test_sell_whitelist_keeps_stacks(env):
    g, cal, cfg = env
    bot = shopmod.ShopBot(g, cal, cfg)
    bot.open_shop()
    sold = bot.sell("sell_junk", keep_stacks=1)
    assert sold == 2
    assert g.sold == ["sell_junk", "sell_junk"]
    assert g.inv_items.count("sell_junk") == 1
    assert g.inv_items.index("sell_junk") == 0      # 留下的是最上面那組
    assert "keep_me" in g.inv_items and "keep_me2" in g.inv_items


def test_missing_item_raises_and_shop_closes(env):
    g, cal, cfg = env
    cv2.imwrite(str(calib.TEMPLATE_DIR / "buy_nothing.png"), patch(28, 148))
    bot = shopmod.ShopBot(g, cal, cfg)
    bot.open_shop()
    with pytest.raises(shopmod.ShopError):
        bot.buy("buy_nothing", 10)
    bot.close_shop()
    assert not g.shop


def test_runner_refill_end_to_end(env, monkeypatch):
    g, cal, cfg = env
    from autopot import app
    import yaml
    full = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    full.update(cfg)
    monkeypatch.setattr(app.Calibration, "load", classmethod(lambda c: cal))
    monkeypatch.setattr(cal, "missing", lambda extra=(): [])
    r = app.Runner(full, g)
    for t in r.trackers.values():
        t.history.extend([(0, 1)] * 10)
    r.ocr_ok = True
    assert r.refill({"hp": 1200, "mp": 150}, "測試")
    assert g.bought == {"buy_milk": 6750 - 1200, "buy_ice": 850 - 150}
    assert not g.shop


def test_runner_loop_smoke(env, monkeypatch):
    """跑主迴圈幾圈：數量低 → 自動補貨；HP 歸零 → 判斷死亡並停止操作。"""
    g, cal, cfg = env
    from autopot import app
    import yaml
    full = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    full.update(cfg)
    full["notify"] = {"beep": False}
    full["ocr_enabled"] = True
    full["safety"]["defer_if_user_active_sec"] = 0
    cal.data["regions"].update({"hp_bar": [100, 860, 200, 20], "mp_bar": [320, 860, 200, 20],
                                "qs_hp": [1100, 820, 60, 60], "qs_mp": [1170, 820, 60, 60]})
    state = {"hp": 1.0, "counts": {"qs_hp": 1400, "qs_mp": 600}}
    orig_capture = g.capture

    def capture():
        img = orig_capture()
        w = int(200 * state["hp"])
        img[860:880, 100:300] = (50, 50, 50)
        img[860:880, 100:100 + w] = (0, 0, 238)
        img[860:880, 320:520] = (50, 50, 50)
        img[860:880, 320:420] = (238, 159, 0)
        img[855:857, 100:520] = 255          # HP/MP 條上下的白色外框（真實遊戲也有）
        img[882:884, 100:520] = 255
        return img
    g.capture = capture
    monkeypatch.setattr(app.Calibration, "load", classmethod(lambda c: cal))
    monkeypatch.setattr(cal, "missing", lambda extra=(): [])
    r = app.Runner(full, g)
    r.reader.read = lambda img, start_x=None: (
        state["counts"]["qs_hp"] if img.shape[1] == 60 and r._which == "qs_hp" else state["counts"]["qs_mp"])
    orig_crop = app.crop

    def crop(img, rect):
        for k in ("qs_hp", "qs_mp"):
            if list(rect) == cal.data["regions"][k]:
                r._which = k
        return orig_crop(img, rect)
    monkeypatch.setattr(app, "crop", crop)
    ticks = {"n": 0}

    def fake_sleep(s):
        ticks["n"] += 1
        if g.bought and not g.shop and state["hp"] > 0:
            state["hp"] = 0.0          # 補完貨之後角色死掉
            ticks["dead_at"] = ticks["n"]
        if ticks["n"] > ticks.get("dead_at", 10 ** 9) + 30 or ticks["n"] > 5000:
            r.quit = True
    monkeypatch.setattr(time, "sleep", fake_sleep)
    t0 = [1_000_000.0]

    def fake_time():
        t0[0] += 2.0
        return t0[0]
    monkeypatch.setattr(time, "time", fake_time)
    r.run()
    assert g.bought == {"buy_milk": 6750 - 1400, "buy_ice": 850 - 600}   # 數量穩定讀到 → 補到目標
    assert r.guard.dead


def test_ocr_off_uses_fixed_amounts(env, monkeypatch):
    g, cal, cfg = env
    from autopot import app
    import yaml
    full = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    full.update(cfg)
    full["ocr_enabled"] = False          # 關掉數字辨識的情況
    monkeypatch.setattr(app.Calibration, "load", classmethod(lambda c: cal))
    monkeypatch.setattr(cal, "missing", lambda extra=(): [])
    r = app.Runner(full, g)
    assert r.plan({"hp": None, "mp": None}, False) == {"hp": 3200, "mp": 400}
    assert r.refill({"hp": None, "mp": None}, "定時")
    assert g.bought == {"buy_milk": 3200, "buy_ice": 400}


def test_ime_eating_input_aborts_purchase(env):
    """輸入法把數字吃掉（框裡字數不對）→ 取消，不能用錯的數量買。"""
    g, cal, cfg = env
    g.type_text = lambda t: None if t == "2" else setattr(g, "typed", g.typed + t)   # 「2」被吃掉
    bot = shopmod.ShopBot(g, cal, cfg)
    bot.open_shop()
    with pytest.raises(shopmod.ShopError):
        bot.buy("buy_milk", 3200)
    assert g.bought == {} and g.dialog is None


def test_field_not_cleared_aborts(env):
    """9999 刪不掉 → 取消，絕不能用 9999 買下去。"""
    g, cal, cfg = env
    orig = g.press
    g.press = lambda key, times=1: None if key == "backspace" else orig(key, times)
    bot = shopmod.ShopBot(g, cal, cfg)
    bot.open_shop()
    with pytest.raises(shopmod.ShopError):
        bot.buy("buy_milk", 3200)
    assert g.bought == {}


def _runner(env, monkeypatch):
    g, cal, cfg = env
    from autopot import app
    import yaml
    full = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    full.update(cfg)
    monkeypatch.setattr(app.Calibration, "load", classmethod(lambda c: cal))
    monkeypatch.setattr(cal, "missing", lambda extra=(): [])
    return app, app.Runner(full, g)


def test_window_size_auto_fixed(env, monkeypatch):
    """10/21 實測：視窗被拉成 2795x1572（校正時 2764x1555）→ 以前只會叫你自己調回去，
    現在自動把視窗調回校正時的大小，然後繼續。"""
    g, cal, _ = env
    app, r = _runner(env, monkeypatch)
    real = g.capture()
    H, W = real.shape[:2]
    cal.data["window_size"] = [W, H]
    size = {"now": (W + 31, H + 17)}
    calls = []
    orig = g.capture
    g.capture = lambda: orig() if size["now"] == (W, H) else np.zeros((size["now"][1], size["now"][0], 3), np.uint8)
    g.client_rect = lambda: (0, 0, *size["now"])

    def resize(w, h):
        calls.append((w, h))
        size["now"] = (w, h)
        return size["now"]
    g.resize_client = resize
    try:
        r.read_state()
    except (app.NotVisible, KeyError):
        pass                                    # 假畫面沒有 HP 條、沒校正 HP 區也沒關係，重點是大小那關過了（沒有 ShopError）
    assert calls == [(W, H)]
    assert size["now"] == (W, H)


def test_window_size_cannot_fix_reports_and_does_not_spam(env, monkeypatch):
    g, cal, _ = env
    app, r = _runner(env, monkeypatch)
    real = g.capture()
    H, W = real.shape[:2]
    cal.data["window_size"] = [W, H]
    bad = (W + 40, H + 20)
    g.capture = lambda: np.zeros((bad[1], bad[0], 3), np.uint8)
    g.client_rect = lambda: (0, 0, *bad)
    calls = []
    g.resize_client = lambda w, h: calls.append((w, h)) or bad      # 調不動（例如螢幕太小）
    with pytest.raises(app.ShopError, match="視窗大小變了"):
        r.read_state()
    with pytest.raises(app.ShopError):
        r.read_state()
    assert len(calls) == 1                      # 10 秒內不重複調（你可能正在拖視窗）


def test_repeated_refill_failures_back_off(env, monkeypatch):
    """10/21：補貨失敗 96 次、每 2.6 分鐘重試一次都一樣失敗 → 失敗 3 次後改成 10 分鐘才試一次。"""
    g, cal, _ = env
    app, r = _runner(env, monkeypatch)

    def boom():
        raise app.ShopError("雙擊妙妙三次都沒開出商店")
    r.shop.open_shop = boom
    for _ in range(3):
        assert not r.refill({"hp": None, "mp": None}, "測試")
    assert r.failures == 3
    assert r._retry_at > time.time() + 500
    assert "妙妙" in r._last_fail
