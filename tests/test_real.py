"""用你遊戲的「真實截圖」做的單元測試（不用開遊戲）。

samples/real/ 裡的圖都是程式在你電腦上存下來的畫面：
  shop_mid.png         商店開著、商品清單捲到一半（商店視窗被移動過）
  shop_ice_notice.png  商店開著、紅豆刨冰那列是「選取中」的橘色、跳出背包不足提示
"""
import json
import sys
from pathlib import Path

import cv2
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import autopot.calib as calib  # noqa: E402
import autopot.shop as shopmod  # noqa: E402
from autopot.vision import crop, find  # noqa: E402

R = ROOT / "tests" / "samples" / "real"
CFG = {"match_threshold": 0.85, "sell_threshold": 0.93, "max_per_purchase": 9999}


class StillWin:
    """只會回傳同一張截圖的假視窗；記錄被點了哪裡。"""
    def __init__(self, img):
        self.img = img
        self.clicks = []

    def capture(self):
        return self.img

    def click(self, x, y, jitter=0):
        self.clicks.append((x, y))

    def wheel(self, x, y, n):
        pass


@pytest.fixture
def bot_for(monkeypatch):
    monkeypatch.setattr(calib, "TEMPLATE_DIR", R)
    monkeypatch.setattr(shopmod.time, "sleep", lambda s: None)

    def make(name):
        img = cv2.imread(str(R / name))
        cal = calib.Calibration(json.loads((R / "calibration.json").read_text()))
        b = shopmod.ShopBot(StillWin(img), cal, CFG)
        b._update_shift()
        return b, img
    return make


# ---------- 商店視窗被移動後，清單區域要跟著移動 ----------
@pytest.mark.parametrize("name", ["shop_mid.png", "shop_ice_notice.png"])
def test_list_region_follows_shop_window(bot_for, name):
    bot, img = bot_for(name)
    leave = find(img, bot.cal.template("shop_leave"), 0.95)
    x, y, w, h = bot.region("shop_list")
    # 商品清單應該在「離開商店」按鈕的左下方，而且在畫面內
    assert x < leave.x and y > leave.y
    assert 0 <= y and y + h <= img.shape[0]


# ---------- ▲▼ 不能找錯（之前一直上下點就是這個 bug） ----------
@pytest.mark.parametrize("name", ["shop_mid.png", "shop_ice_notice.png"])
@pytest.mark.parametrize("lst", ["shop_list", "inv_list"])
def test_arrows_are_not_confused(bot_for, name, lst):
    bot, img = bot_for(name)
    rect = bot.region(lst)
    up = bot._arrow(rect, "list_up", img)
    down = bot._arrow(rect, "list_down", img)
    assert up is not None and down is not None
    _, ry, _, rh = rect
    assert up.center[1] < ry + rh * 0.3, "▲ 應該在清單上方"
    assert down.center[1] > ry + rh * 0.7, "▼ 應該在清單下方"
    assert down.center[1] - up.center[1] > rh * 0.6


# ---------- 選取中（橘色）的商品也要找得到 ----------
def test_highlighted_ice_row_found(bot_for):
    bot, img = bot_for("shop_ice_notice.png")
    rect = bot.region("shop_list")
    m = find(crop(img, rect), bot.cal.template("buy_ice"), CFG["match_threshold"], robust=True)
    assert m is not None, "紅豆刨冰那列是橘色時找不到"
    assert m.score > 0.9


def test_milk_not_confused_with_bag_milk(bot_for):
    # shop_mid 的商品清單裡沒有馴鹿奶；右邊背包裡的馴鹿奶（2750 楓幣）不能被當成要買的那個
    bot, img = bot_for("shop_mid.png")
    rect = bot.region("shop_list")
    assert find(crop(img, rect), bot.cal.template("buy_milk"), CFG["match_threshold"], robust=True) is None


# ---------- 要賣的東西不能認錯 ----------
def test_sell_elixir_found_two_stacks(bot_for):
    bot, img = bot_for("shop_ice_notice.png")
    from autopot.vision import find_all
    ms = find_all(crop(img, bot.region("inv_list")), bot.cal.template("sell_elixir"), CFG["sell_threshold"], robust=True)
    assert len(ms) == 2


def test_sell_special_not_matching_super_potion(bot_for):
    # 畫面上有「超級藥水」，但沒有「特殊藥水」→ 一個都不能找到
    bot, img = bot_for("shop_ice_notice.png")
    from autopot.vision import find_all
    ms = find_all(crop(img, bot.region("inv_list")), bot.cal.template("sell_special"), CFG["sell_threshold"], robust=True)
    assert ms == []


# ---------- 捲動方向判斷 ----------
def test_vshift_sign_on_real_list(bot_for):
    bot, img = bot_for("shop_mid.png")
    x, y, w, h = bot.region("shop_list")
    row = 110
    a = crop(img, [x, y + row, w, h - 2 * row])
    down = crop(img, [x, y + 2 * row, w, h - 2 * row])   # 內容往上移一列 = 往下捲
    up = crop(img, [x, y, w, h - 2 * row])
    assert shopmod._vshift(a, down) > 50
    assert shopmod._vshift(a, up) < -50


@pytest.mark.parametrize("dy", [-60, -30, 30, 60])
def test_arrows_found_even_if_region_estimate_is_off(bot_for, dy):
    # 這次實測商店位置估計差了幾十像素，▲▼ 還是要找得到、而且不能顛倒
    bot, img = bot_for("shop_mid.png")
    x, y, w, h = bot.region("shop_list")
    rect = [x, y + dy, w, h]
    up = bot._arrow(rect, "list_up", img)
    down = bot._arrow(rect, "list_down", img)
    assert up is not None and down is not None
    assert down.center[1] - up.center[1] > h * 0.6


# ---------- 「背包空間不足」提示框要認得出來（之前相似度只有 0.85，差點認不到） ----------
def test_notice_detected_on_real_screenshot(bot_for):
    bot, img = bot_for("shop_ice_notice.png")
    assert bot.find("dialog_ok", img, threshold=shopmod.NOTICE_OK_TH) is not None
    assert bot.find("dialog_cancel", img) is None      # 沒有取消鈕 = 提示框，不是數量視窗


def test_no_notice_on_plain_shop(bot_for):
    bot, img = bot_for("shop_mid.png")
    assert bot.find("dialog_ok", img, threshold=shopmod.NOTICE_OK_TH) is None
