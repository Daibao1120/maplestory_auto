"""掃描學習（autopot/catalog.py）：用真實截圖拼的模擬商店，把商店/背包掃一遍，每種道具只列一次。"""
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import autopot.calib as calib  # noqa: E402
import autopot.catalog as cat  # noqa: E402
import autopot.shop as shopmod  # noqa: E402
import realsim as rs  # noqa: E402
from test_replay import CFG, INV, R  # noqa: E402

# 真實商品名稱（命中藥丸…）＋一些假商品。假商品用完全不同的字（模擬商店原本的假商品「SHOP ITEM 1/2/3」
# 只差一個數字，比真實的中文名稱還難分；真實的中文名稱至少差一個字）
WORDS = ["APPLE PIE", "ZEBRA", "QUICK FOX", "MOON", "JELLY", "WIZARD HAT", "BOLT"]
for _i, _w in enumerate(WORDS):
    rs.ROWS["shop:w" + str(_i)] = rs._text_row(rs.ROWS["shop:蘋果"], _w, 300 + _i)
    rs.ROWS["inv:w" + str(_i)] = rs._text_row(rs.ROWS["inv:milk"], _w, 400 + _i)
SHOP = ["w0", "w1", "命中藥丸", "蘋果", "雞蛋", "w2", "烤肉", "柳橙", "棒冰棒",
        "ice", "w3", "milk", "w4", "w5"]
INV = [(n.replace("junk0", "w0").replace("junk1", "w1").replace("junk2", "w2").replace("junk3", "w3")
        .replace("junk4", "w4"), c) for n, c in INV]


@pytest.fixture
def world(tmp_path, monkeypatch):
    tdir = tmp_path / "templates"
    shutil.copytree(R, tdir)
    ddir = tmp_path / "data"
    ddir.mkdir()
    monkeypatch.setattr(calib, "TEMPLATE_DIR", tdir)
    monkeypatch.setattr(calib, "DATA_DIR", ddir)
    clock = rs.Clock()
    monkeypatch.setattr(shopmod, "time", clock)
    shopmod.random.seed(1)

    def make(shop=SHOP, inv=INV, offset=(0, 0), **kw):
        g = rs.RealShop(shop, inv, offset=offset, clock=clock, **kw)
        c = calib.Calibration(json.loads((R / "calibration.json").read_text(encoding="utf-8")))
        bot = shopmod.ShopBot(g, c, CFG)
        bot._update_shift()
        return g, bot, clock
    return make


@pytest.mark.parametrize("offset", [(0, 0), (-40, -100)])
def test_scan_shop_lists_every_item_once(world, offset):
    g, bot, clock = world(offset=offset)
    items = cat.scan_list(bot, bot.region("shop_list"))
    assert len(items) == len(set(SHOP)), [it.first_row for it in items]
    assert clock.t - 1000 < 600, "掃太久了"


def test_learned_template_buys_the_right_item(world):
    """從掃描結果挑「馴鹿奶」那一列 → 存成模板 → 用它買，買到的是馴鹿奶。"""
    g, bot, clock = world(bag_slots=len(INV) + 30)
    items = cat.scan_list(bot, bot.region("shop_list"))
    milk_row = SHOP.index("milk")
    it = next(i for i in items if i.first_row == milk_row)
    cat.save_item_template("buy_learned_hp", it)
    bot.cal._tpl_cache.clear()
    th, _ = cat.threshold_for(it, items)
    assert bot.buy("buy_learned_hp", 321, th=th, verify=True)
    assert g.bought == {"milk": 321}


def test_learned_template_for_every_shop_item_is_unique(world):
    """每一種商品學到的模板（用 threshold_for 算出的門檻），在整個清單裡只會比對到它自己那一列。
    模擬商店裡的「SHOP ITEM 1 / 2 / 3…」只差一個數字，比真實的「紅色藥水 / 橙色藥水」還難分。"""
    g, bot, clock = world()
    items = cat.scan_list(bot, bot.region("shop_list"))
    rows = [rs.ROWS["shop:" + n] for n in SHOP]
    for it in items:
        th, worst = cat.threshold_for(it, items)
        hits = [k for k, r in enumerate(rows)
                if shopmod.find(r, it.name_img, th, robust=True) is not None]
        assert hits == [it.first_row], (SHOP[it.first_row], th, worst, hits)


def test_scan_inventory_groups_stacks(world):
    g, bot, clock = world()
    bot.click_tab("shop_tab_use", "shop_leave")
    items = cat.scan_list(bot, bot.region("inv_list"))
    distinct = []
    for n, _ in INV:
        if n not in distinct:
            distinct.append(n)
    assert len(items) == len(distinct), (len(items), distinct)
    milk = items[distinct.index("milk")]
    assert milk.count >= sum(1 for n, _ in INV if n == "milk") - 3      # 組數只是參考


def test_learned_sell_template_sells_only_that_item(world):
    """從背包掃描挑「萬能療傷藥」→ 存成模板 → 賣（保留 1 組），其他東西一個都不能少。"""
    g, bot, clock = world()
    bot.click_tab("shop_tab_use", "shop_leave")
    items = cat.scan_list(bot, bot.region("inv_list"))
    distinct = []
    for n, _ in INV:
        if n not in distinct:
            distinct.append(n)
    el = items[distinct.index("elixir")]
    cat.save_item_template("sell_learned_1", el)
    bot.cal._tpl_cache.clear()
    th, _ = cat.threshold_for(el, items, base=0.93)
    before = [s["name"] for s in g.inv]
    bot.sell_all([{"template": "sell_learned_1", "keep_stacks": 1, "threshold": th}])
    after = [s["name"] for s in g.inv]
    assert after.count("elixir") == 1
    for n in set(before) - {"elixir"}:
        assert after.count(n) == before.count(n), n


def test_name_crop_ignores_count_and_price():
    """同一種道具：背包圖示上的數量不同（164 / 200）、價格不同 → 名稱圖一樣。"""
    a = cat.name_crop(rs.ROWS["inv:milk164"])
    b = cat.name_crop(rs.ROWS["inv:milk"])
    assert a is not None and b is not None and cat.same_item(a, b)
    c = cat.name_crop(rs.ROWS["shop:蘋果"])
    assert not cat.same_item(a, c)
    assert cat.name_crop(rs.ROWS["blank:shop"]) is None


def test_real_screenshot_rows_and_names():
    """真實截圖 shop_mid.png：商品清單 5 列（命中藥丸、蘋果、雞蛋、烤肉、柳橙）都不一樣；
    背包 5 列裡 4 列是馴鹿奶（數量 164 / 200 不同）→ 名稱都一樣。"""
    mid = cv2.imread(str(R / "shop_mid.png"))
    for L, expect_distinct in (("shop", 5), ("inv", 2)):
        c = rs.LISTS[L]
        view = mid[c["cy"]:c["cy"] + rs.PITCH * 5, c["cx"]:c["cx"] + c["cw"]]
        bands = cat.row_bands(view, rs.PITCH)
        assert len(bands) == 5, (L, bands)
        names = [cat.name_crop(view[a:b]) for a, b in bands]
        assert all(n is not None for n in names)
        groups = []
        for n in names:
            if not any(cat.same_item(g, n) for g in groups):
                groups.append(n)
        assert len(groups) == expect_distinct, L


def test_real_ld_screenshot_rows():
    """手機版（雷電模擬器）真實截圖：商品清單看得到的 4 列都切得出名稱。"""
    img = cv2.imread(str(ROOT / "tests" / "samples" / "ld" / "ld_shop_open.png"))
    L = json.loads((ROOT / "ld" / "calibration.json").read_text(encoding="utf-8"))["regions"]["shop_list"]
    view = img[L[1]:L[1] + L[3], L[0]:L[0] + L[2]]
    bands = cat.row_bands(view, 103.5)
    assert len(bands) == 4, bands
    names = [cat.name_crop(view[a:b]) for a, b in bands]
    assert all(n is not None for n in names)
    assert not any(cat.same_item(names[i], names[j]) for i in range(4) for j in range(i + 1, 4))
