"""用真實截圖拼成的模擬商店（tests/realsim.py），測完整的「賣 → 買」流程。

每個測試都會檢查：
  * 有沒有賣錯東西（白名單以外的一律不能碰、保留的那組不能賣）
  * 有沒有買到該買的數量
  * 花了多少（虛擬）時間 —— 太慢就算失敗
"""
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
import autopot.shop as shopmod  # noqa: E402
import realsim as rs  # noqa: E402

R = ROOT / "tests" / "samples" / "real"
CFG = {"match_threshold": 0.85, "sell_threshold": 0.93, "max_per_purchase": 9999, "close_inventory_after": True}

SHOP = [f"filler{i}" for i in range(15)] + ["命中藥丸", "蘋果", "雞蛋", "烤肉", "柳橙", "棒冰棒",
                                              "ice", "filler20", "filler21", "filler22", "milk", "filler23",
                                              "filler24", "filler25"]
# 跟你現在的背包差不多：前面一些道具，中間夾著要賣的，後面一大串一模一樣的馴鹿奶 200
INV = ([("junk0", 15), ("junk1", 61), ("super", 200), ("elixir", 100), ("elixir", 100), ("junk2", 1),
        ("super", 200), ("special", 21), ("junk3", 4), ("junk4", 5), ("super", 200), ("ice", 160),
        ("vitality", 12), ("super", 33), ("ice", 200), ("ice", 117), ("elixir", 52), ("cube", 30)]
       + [("milk", 200)] * 24 + [("milk", 54)])
SELL = [
    {"template": "sell_special", "keep_stacks": 0},
    {"template": "sell_vitality", "keep_stacks": 0},
    {"template": "sell_elixir", "keep_stacks": 1},
]
NAME = {"sell_special": "special", "sell_vitality": "vitality", "sell_elixir": "elixir", "sell_super": "super"}

# 真實截圖裡商店相對於校正位置的偏移是 (3, 236)；下面這些是「整個畫面再平移多少」
OFFSETS = [(0, 0), (-63, -180), (0, -259), (-40, -100)]


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
    # 參考圖的縮放比例照你電腦（3840 寬螢幕）算，不要用跑測試那台電腦的螢幕寬度（GitHub 的 Windows 主機螢幕很小）
    from autopot import learn_items
    meta = json.loads((ROOT / "assets" / "refs" / "refs.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(learn_items, "_factor", lambda: meta["ref_screen_width"] / meta["native_screen_width"])

    def make(shop=SHOP, inv=INV, offset=(0, 0), dry=False, **kw):
        g = rs.RealShop(shop, inv, offset=offset, clock=clock, **kw)
        cal = calib.Calibration(json.loads((R / "calibration.json").read_text(encoding="utf-8")))
        bot = shopmod.ShopBot(g, cal, CFG, dry_run=dry)
        bot._update_shift()
        return g, bot, clock
    return make


def names(g):
    return [it["name"] for it in g.inv]


def check_sold_correctly(g, before, items):
    """白名單外的東西一個都不能少；保留的組數要對，而且留下的是最上面那組。"""
    after = names(g)
    keep = {NAME[it["template"]]: it["keep_stacks"] for it in items}
    for n in set(before):
        if n in keep:
            assert after.count(n) == min(keep[n], before.count(n)), (n, after.count(n))
        else:
            assert after.count(n) == before.count(n), f"賣錯東西了：{n}"


# ---------- 基本元件：用真實截圖 ----------
def test_thumb_found_on_real_screenshot():
    mid = cv2.imread(str(R / "shop_mid.png"))
    up = shopmod.Match(1356, 838, 29, 26, 1.0)
    down = shopmod.Match(1356, 1379, 32, 29, 0.93)
    t = shopmod.thumb_span(mid, shopmod.ScrollBar(up, down))
    assert t is not None and abs(t[0] - 993) <= 4 and abs(t[1] - 1063) <= 6
    up2 = shopmod.Match(2021, 838, 29, 26, 1.0)
    down2 = shopmod.Match(2021, 1379, 32, 29, 0.93)
    t2 = shopmod.thumb_span(mid, shopmod.ScrollBar(up2, down2))
    assert t2 is not None and 1060 <= t2[0] <= 1070


def test_arrow_threshold_ignores_thumb():
    """之前「捲動鈕位置不合理」的原因：滑塊被當成 ▲（相似度 0.80）。"""
    from autopot.vision import find_all
    mid = cv2.imread(str(R / "shop_mid.png"))
    ups = find_all(mid, cv2.imread(str(R / "list_up.png")), shopmod.ARROW_TH)
    assert sorted(m.y for m in ups) == [838, 838]


@pytest.mark.parametrize("offset", OFFSETS)
def test_scroll_end_and_scan_through_identical_rows(world, offset):
    """背包裡 24 列一模一樣的馴鹿奶，以前會被當成「捲到底了」。"""
    inv = [("elixir", 100)] + [("milk", 200)] * 30 + [("elixir", 52)]
    g, bot, clock = world(inv=inv, offset=offset)
    rect = bot.region("inv_list")
    t0 = clock.t
    found = bot.scan(rect, ["sell_elixir"], th=0.93)
    assert len(found["sell_elixir"]) == 2, "最底下那組萬能療傷藥沒掃到"
    assert g.top["inv"] == len(inv) - 5
    assert clock.t - t0 < 30, f"掃 32 列花了 {clock.t - t0:.0f} 秒"
    t1 = clock.t
    bot.scroll_end(rect, -1)
    assert g.top["inv"] == 0
    assert clock.t - t1 < 3, "回到頂端應該拖滑塊一次完成"


# ---------- 賣 ----------
@pytest.mark.parametrize("offset", OFFSETS)
def test_sell_all_whitelist(world, offset):
    g, bot, clock = world(offset=offset)
    before = names(g)
    t0 = clock.t
    res = bot.sell_all(SELL)
    took = clock.t - t0
    assert res == {"sell_special": 1, "sell_vitality": 1, "sell_elixir": 2}
    check_sold_correctly(g, before, SELL)
    kept = [it for it in g.inv if it["name"] == "elixir"]
    assert kept == [{"name": "elixir", "count": 100}], "應該留最上面那組 100 個的"
    assert took < 60, f"賣 4 組花了 {took:.0f} 秒，太慢"


def test_sell_super_keep_one(world):
    """超級藥水賣到剩一組（留最上面那組）。"""
    tdir = calib.TEMPLATE_DIR
    g, bot, clock = world()
    # 先用學習功能從背包裡學「超級藥水」的樣子（跟正式執行時一樣）
    from autopot import learn_items
    learned = learn_items.capture_items(bot, ["sell_super"])
    assert learned == ["sell_super"]
    assert (tdir / "sell_super.png").exists()
    items = SELL + [{"template": "sell_super", "keep_stacks": 1}]
    before = names(g)
    res = bot.sell_all(items)
    assert res["sell_super"] == 3
    check_sold_correctly(g, before, items)
    assert [it["count"] for it in g.inv if it["name"] == "super"] == [200]


def test_sell_when_selected_row_needs_click_first_and_ok_is_flaky(world):
    """雙擊沒選取的道具只會選取、按確認第一下沒反應 → 還是要賣成功。"""
    g, bot, clock = world(flaky_dbl=1.0, flaky_ok=1.0)
    before = names(g)
    bot.sell_all(SELL)
    check_sold_correctly(g, before, SELL)


def test_eaten_clicks_on_arrows(world):
    """點 ▲▼ 有三成機率沒反應 → 還是要掃完整個背包、找到最底下的道具。"""
    inv = [("elixir", 100)] + [("milk", 200)] * 20 + [("special", 2)]
    g, bot, clock = world(inv=inv, tap_drop=0.3)
    bot.sell_all(SELL)
    assert "special" not in names(g) and names(g).count("elixir") == 1 and names(g).count("milk") == 20


def test_sell_with_tooltips_and_arrow_hover(world):
    """滑鼠停在道具上會跳說明框、停在 ▲▼ 上按鈕會變亮。"""
    g, bot, clock = world(hover_effects=True)
    before = names(g)
    bot.sell_all(SELL)
    check_sold_correctly(g, before, SELL)


def test_sell_at_list_bottom_does_not_sell_kept_stack(world):
    """賣掉最底下幾列時畫面會往下移一列，不能因此把保留的那組也賣掉。"""
    inv = [("milk", 200)] * 10 + [("elixir", 100), ("elixir", 30), ("special", 3), ("elixir", 7)]
    g, bot, clock = world(inv=inv)
    bot.sell_all(SELL)
    assert [it for it in g.inv if it["name"] == "elixir"] == [{"name": "elixir", "count": 100}]
    assert names(g).count("special") == 0
    assert names(g).count("milk") == 10


def test_sell_restarts_when_item_info_changed(world):
    """「道具資訊已變更」→ 重開商店再賣，不能賣錯。"""
    g, bot, clock = world(pickup_every=4.0, pickup_items=("junk5",), info_changed_p=0.5, bag_slots=len(INV) + 3)
    before = names(g)
    bot.sell_all(SELL)
    after = names(g)
    assert after.count("elixir") == 1 and [it["count"] for it in g.inv if it["name"] == "elixir"] == [100]
    for n in set(before) - {"elixir", "special", "vitality"}:
        assert after.count(n) == before.count(n)


def test_dry_run_sells_nothing_and_finishes(world):
    g, bot, clock = world(dry=True)
    before = [dict(x) for x in g.inv]
    t0 = clock.t
    res = bot.sell_all(SELL)
    assert g.inv == before and g.sold == []
    assert res["sell_elixir"] == 2 and res["sell_special"] == 1
    assert clock.t - t0 < 60


# ---------- 買 ----------
@pytest.mark.parametrize("offset", OFFSETS)
def test_buy_ice_and_milk(world, offset):
    g, bot, clock = world(offset=offset, bag_slots=len(INV) + 20)
    t0 = clock.t
    assert bot.buy("buy_ice", 400)
    assert bot.buy("buy_milk", 3200)
    assert g.bought == {"ice": 400, "milk": 3200}
    assert g.dialog is None
    assert clock.t - t0 < 30, f"買兩樣花了 {clock.t - t0:.0f} 秒"


def test_buy_partial_when_bag_full(world):
    g, bot, clock = world(bag_slots=len(INV) + 1)
    ok = bot.buy("buy_ice", 400)
    assert ok is False and bot.bag_full
    assert g.bought["ice"] < 400 and g.dialog is None


# ---------- 完整流程：賣完馬上買（掛機一直撿東西） ----------
def test_full_refill_sell_then_buy_before_pickups_fill_bag(world):
    """背包全滿 → 賣 4 組空出 4 格 → 要在撿到的東西把格子佔掉之前買完。"""
    g, bot, clock = world(bag_slots=len(INV), pickup_every=45.0, pickup_items=("junk6", "junk7"))
    t0 = clock.t
    bot.sell_all(SELL)
    t_sold = clock.t
    bot.buy("buy_ice", 400)
    bot.buy("buy_milk", 3200)
    total = clock.t - t0
    # 4 格空位：刨冰先補滿殘組 + 至少再一組，馴鹿奶至少一組
    assert g.bought.get("ice", 0) >= 200
    assert g.bought.get("milk", 0) >= 146
    assert clock.t - t_sold < 25, f"賣完到買完花了 {clock.t - t_sold:.0f} 秒"
    assert total < 80, f"整個補貨花了 {total:.0f} 秒"


def test_runner_refill_with_real_screens(world, monkeypatch):
    """主程式的 refill()：開商店 → 賣 → 買 → 關商店。"""
    import yaml
    from autopot import app
    g, bot, clock = world(bag_slots=len(INV) + 30, shop_open=False)
    full = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    full.update(CFG)
    full["sell_enabled"] = True
    full["sell"] = [dict(s, name=s["template"]) for s in SELL]
    full["notify"] = {"beep": False}
    monkeypatch.setattr(app.Calibration, "load", classmethod(lambda c: bot.cal))
    monkeypatch.setattr(bot.cal, "missing", lambda extra=(): [])
    monkeypatch.setattr(app, "time", clock)
    r = app.Runner(full, g)
    t0 = clock.t
    assert r.refill({"hp": None, "mp": None}, "測試")
    assert g.bought == {"ice": 400, "milk": 3200}
    assert names(g).count("elixir") == 1 and "special" not in names(g) and "vitality" not in names(g)
    assert not g.shop_open
    assert clock.t - t0 < 120, f"整個補貨花了 {clock.t - t0:.0f} 秒"


# ---------- 審查時找到的三種「會把要保留的那組賣掉」的情況 ----------
def _elixirs(g):
    return [it["count"] for it in g.inv if it["name"] == "elixir"]


def test_keep_item_listed_before_sell_item(world):
    """要賣的道具在保留的那組上面：賣掉後下面的列往上移，不能把保留的那組也賣掉。"""
    g, bot, clock = world(inv=[("special", 3), ("elixir", 100), ("elixir", 30)] + [("milk", 200)] * 10)
    bot.sell_all([{"template": "sell_elixir", "keep_stacks": 1}, {"template": "sell_special", "keep_stacks": 0}])
    assert _elixirs(g) == [100] and "special" not in names(g)


def test_pickup_fills_freed_slot_in_the_middle(world):
    """賣掉後空出來的格子被撿到的東西佔掉（插在清單中間）。"""
    inv = ([("junk0", 1), ("special", 3)] + [("milk", 200)] * 5 + [("elixir", 100), ("milk", 200), ("elixir", 30)]
           + [("milk", 200)] * 8)
    g, bot, clock = world(inv=inv, pickup_every=3.0, pickup_items=("junk5",))
    bot.sell_all(SELL)
    assert _elixirs(g) == [100]
    assert names(g).count("milk") == 14 and "junk0" in names(g)


def test_laggy_scroll(world):
    """遊戲卡頓：點 ▼ 之後半秒才捲（重試的那下也生效 → 一次捲兩列）。"""
    inv = [("milk", 200)] * 5 + [("elixir", 100)] + [("milk", 200)] * 3 + [("elixir", 30)] + [("milk", 200)] * 6
    for seed in range(4):
        g, bot, clock = world(inv=inv, lag_p=0.5, seed=seed)
        bot.sell_all([{"template": "sell_elixir", "keep_stacks": 1}])
        assert _elixirs(g)[:1] == [100], f"seed {seed}: 保留的那組被賣了"
        assert names(g).count("milk") == 14


def test_many_super_stacks_keep_top_one(world):
    """超級藥水分散在背包各處（中間隔好幾列），賣到剩最上面那組。"""
    from autopot import learn_items
    inv = ([("super", 200), ("junk0", 1), ("junk1", 1), ("super", 200), ("junk2", 1), ("junk3", 1),
            ("junk4", 1), ("super", 120), ("milk", 200), ("super", 33)] + [("milk", 200)] * 12)
    g, bot, clock = world(inv=inv, pickup_every=6.0, pickup_items=("junk5", "junk6"), bag_slots=len(inv) + 2)
    assert learn_items.capture_items(bot, ["sell_super"]) == ["sell_super"]
    bot.sell_all([{"template": "sell_super", "keep_stacks": 1}])
    sup = [it["count"] for it in g.inv if it["name"] == "super"]
    assert sup[:1] == [200], "最上面那組要留著"
    assert len(sup) <= 2, f"還剩 {len(sup)} 組沒賣"


def test_switches_to_use_tab_before_selling(world):
    """實測：右邊背包停在「裝備」分頁，學道具和賣東西都在錯的分頁上做（10/20 01:48）。"""
    g, bot, clock = world(start_tab_use=False, tab_click_drop=0.5)
    before = names(g)
    res = bot.sell_all(SELL)
    assert g.tab_use
    assert res["sell_special"] == 1 and res["sell_elixir"] == 2
    check_sold_correctly(g, before, SELL)


# ---------- 10/21 實測：地圖上方的「剩餘時間」框蓋住「離開商店」「購買物品」 ----------
def _covered_world(world, **kw):
    ph, pw = rs.PANEL.shape[:2]
    # 跟實測一樣：商店視窗被移到比較上面，框剛好蓋住兩個按鈕的上半部
    off = (0, -314)
    leave = (rs.LEAVE[0] + off[0], rs.LEAVE[1] + off[1])
    return world(offset=off, panel=(leave[0] + 4, leave[1] + 22, pw, ph), **kw)


def test_covered_leave_button_is_detected_as_open(world):
    g, bot, _ = _covered_world(world)
    assert bot.find("shop_leave") is None          # 模板真的比對不到（跟實測一樣）
    m, covered = bot.find_leave()
    assert m is not None and covered
    assert bot.shop_is_open()


def test_runner_refill_when_buttons_covered(world, monkeypatch):
    """以前：以為商店沒開 → 一直雙擊妙妙 → 補貨失敗 96 次、角色沒水死掉。
    現在：認得出商店開著 → 把商店視窗拖開 → 照常賣、買、關商店。"""
    import yaml
    from autopot import app
    g, bot, clock = _covered_world(world, bag_slots=len(INV) + 30, shop_open=False)
    full = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    full.update(CFG)
    full["sell_enabled"] = True
    full["sell"] = [dict(s, name=s["template"]) for s in SELL]
    full["notify"] = {"beep": False}
    monkeypatch.setattr(app.Calibration, "load", classmethod(lambda c: bot.cal))
    monkeypatch.setattr(bot.cal, "missing", lambda extra=(): [])
    monkeypatch.setattr(app, "time", clock)
    r = app.Runner(full, g)
    r.shop = bot
    before = names(g)
    assert r.refill({"hp": None, "mp": None}, "測試")
    assert g.drags, "應該要把商店視窗拖開"
    assert g.bought == {"ice": 400, "milk": 3200}
    after = names(g)
    assert after.count("elixir") == 1 and "special" not in after and "vitality" not in after
    for n in set(before) - {"elixir", "special", "vitality", "milk", "ice"}:
        assert after.count(n) == before.count(n), f"賣錯東西了：{n}"
    assert not g.shop_open


def test_covered_and_not_draggable_fails_safely(world):
    """拖不動（或拖了還是擋住）→ 回報錯誤，不能亂點、不能賣錯東西。"""
    g, bot, _ = _covered_world(world, draggable=False)
    before = names(g)
    with pytest.raises(shopmod.ShopError, match="擋住"):
        bot.open_shop()
    assert names(g) == before
    assert not g.sold and not g.bought
    assert g.dialog is None


def test_buy_button_fallback_position(world):
    """「購買物品」模板比對不到時（被擋住一半），用「離開商店」下面的固定位置按。"""
    g, bot, _ = world()
    real = bot.find
    bot.find = lambda name, *a, **k: None if name == "shop_buy" else real(name, *a, **k)
    bot.buy("buy_ice", 50)
    assert g.bought.get("ice") == 50
