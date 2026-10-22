"""雷電模擬器（手機版）的補水。

兩部分：
  1. 真實截圖（tests/samples/ld，模擬器 1920x1080 的原始截圖，10/21 實際操作一次補水時存的）：
     模板找不找得到、清單區域、捲動鈕、HP/MP 條……
  2. 模擬商店（tests/ldsim.py）：用手機版的操作方式跑完整的「開商店 → 賣 → 買 → 關商店」，
     檢查有沒有賣錯、買對數量、會不會點到下方工具列。
另外 AdbWindow（用 ADB 截圖、點擊）用假的 adb 測指令有沒有送對。
"""
from __future__ import annotations

import json
import struct
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
from autopot.adbwin import AdbError, AdbWindow, parse_raw_screencap  # noqa: E402
from autopot.vision import Match, bar_framed, bar_ratio, bar_visible, crop, find, find_all  # noqa: E402

LD = ROOT / "tests" / "samples" / "ld"
LDT = ROOT / "ld" / "templates"
LDCAL = json.loads((ROOT / "ld" / "calibration.json").read_text(encoding="utf-8"))


def frame(name):
    im = cv2.imread(str(LD / f"ld_{name}.png"))
    assert im is not None, name
    return im


def tpl(name):
    return cv2.imread(str(LDT / f"{name}.png"))


@pytest.fixture
def real_ld(monkeypatch):
    """用模擬器的模板（ld/templates）。"""
    monkeypatch.setattr(calib, "TEMPLATE_DIR", LDT)


# =====================================================================
# 1. 真實截圖
# =====================================================================
STATES = {
    # 畫面: 應該看得到的 / 不應該看得到的
    "normal": ({"inv_button"}, {"shop_leave", "inv_open", "dialog_cancel", "shortcut_item"}),
    "shortcut_menu": ({"inv_button", "shortcut_item"}, {"shop_leave", "inv_open"}),
    "inventory_cash": ({"inv_open", "miaomiao", "inv_button"}, {"shop_leave", "dialog_cancel"}),
    "shop_open": ({"shop_leave", "shop_buy", "shop_sell", "inv_button"}, {"dialog_cancel", "inv_open", "miaomiao"}),
    "shop_use_tab": ({"shop_leave", "shop_sell", "sell_special", "sell_elixir", "sell_super"}, {"dialog_cancel"}),
    "buy_dialog": ({"qty_dialog", "dialog_ok", "dialog_cancel", "shop_leave"}, set()),
    "buy_dialog_ime": ({"qty_dialog", "dialog_ok", "dialog_cancel"}, set()),
    "sell_dialog_ime": ({"dialog_ok", "dialog_cancel"}, {"qty_dialog"}),
    "closed": ({"inv_button"}, {"shop_leave", "inv_open", "dialog_cancel"}),
}


@pytest.mark.parametrize("name", list(STATES))
def test_real_frames_templates(name):
    img = frame(name)
    yes, no = STATES[name]
    for t in yes:
        m = find(img, tpl(t), 0.85)
        assert m is not None, f"{name}: 應該找到 {t}"
    for t in no:
        m = find(img, tpl(t), 0.85)
        assert m is None, f"{name}: 不應該找到 {t}（{m}）"


def test_real_frames_are_native_resolution():
    for f in LD.glob("ld_*.png"):
        assert cv2.imread(str(f)).shape[:2] == (1080, 1920), f.name


def test_real_hp_mp_bars():
    img = frame("normal")                       # HP 1497/2701
    hp = crop(img, LDCAL["regions"]["hp_bar"])
    assert bar_visible(hp, "hp")
    assert bar_ratio(hp, "hp") == pytest.approx(1497 / 2701, abs=0.03)
    assert bar_framed(img, LDCAL["regions"]["hp_bar"])           # 條貼著畫面最下面，只有上框
    assert bar_visible(crop(img, LDCAL["regions"]["mp_bar"]), "mp")


def test_real_list_regions_and_pitch(real_ld):
    """清單區域只用上面 4 列（第 5 列被工具列擋住），一列高約 103.5px。"""
    img = frame("shop_use_tab")
    cal = calib.Calibration(LDCAL)
    bot = shopmod.ShopBot(_StillWin(img), cal, {"touch": True, "row_ratio": 0.25, "arrow_dy": 488})
    for k in ("shop_list", "inv_list"):
        r = cal.region(k)
        assert r[1] + r[3] < 905, "清單區域不能碰到下方工具列"
        assert bot._pitch(r) == pytest.approx(103.5, abs=2.5)


def test_real_scrollbar_with_hidden_down_arrow(real_ld):
    """▼ 被工具列擋住一半（比對不到）→ 用 ▲ 推算位置；點的地方要在工具列上面（實測點上半部有效）。"""
    img = frame("shop_use_tab")
    cal = calib.Calibration(LDCAL)
    bot = shopmod.ShopBot(_StillWin(img), cal, {"touch": True, "row_ratio": 0.25, "arrow_dy": 488})
    for k in ("shop_list", "inv_list"):
        bar = bot._bar(cal.region(k), img)
        assert bar is not None, k
        assert bar.down.center[1] < 912          # config_ld.yaml 的 hud_top
        assert bar.down.center[1] > bar.up.center[1] + 400
        assert shopmod.thumb_span(img, bar) is not None, f"{k}: 找不到滑塊"


def test_real_sell_items_found_in_inv_list_rows():
    img = frame("shop_use_tab")
    r = LDCAL["regions"]["inv_list"]
    names = {}
    for t in ("sell_elixir", "sell_super", "sell_special"):
        names[t] = [m.y for m in find_all(img, tpl(t), 0.9, r)]
    assert len(names["sell_elixir"]) == 1      # 第 5 列那組萬能療傷藥被工具列擋住 → 不算
    assert len(names["sell_super"]) == 2
    assert len(names["sell_special"]) == 1


def test_real_find_leave_from_sell_button_offset(real_ld):
    """「離開商店」被擋住時用「販售物品」推算的位置要準（實測位置差不到 3px）。"""
    img = frame("shop_open")
    cal = calib.Calibration(LDCAL)
    bot = shopmod.ShopBot(_StillWin(img), cal, {"touch": True})
    real = find(img, tpl("shop_leave"), 0.9)
    bot.find = lambda name, im=None, region=None, threshold=None: (
        None if name == "shop_leave" else find(im if im is not None else img, cal.template(name), threshold or 0.85, region))
    m, covered = bot.find_leave(img)
    assert covered and abs(m.x - real.x) <= 3 and abs(m.y - real.y) <= 3


def test_real_qty_field_rect(real_ld):
    img = frame("buy_dialog")
    cal = calib.Calibration(LDCAL)
    bot = shopmod.ShopBot(_StillWin(img), cal, {"touch": True})
    dlg = find(img, cal.template("qty_dialog"), 0.9)
    x, y, w, h = bot._field_rect(dlg)
    field = img[y + 4:y + h - 4, x + 4:x + w - 4]
    assert (field.min(axis=2) > 200).mean() > 0.8          # 是白色的輸入框
    assert bot._field_segments(dlg) == 3                   # 裡面是「100」三個字


class _StillWin:
    def __init__(self, img):
        self.img = img

    def capture(self):
        return self.img

    def move_away(self, x, y):
        pass


# =====================================================================
# 2. AdbWindow（假的 adb）
# =====================================================================
class FakeAdb:
    def __init__(self, img=None, head=16, fail=None):
        self.calls = []
        self.img = img if img is not None else np.zeros((1080, 1920, 3), np.uint8)
        self.head = head
        self.fail = fail

    def __call__(self, args, timeout=10.0):
        self.calls.append(list(args))
        if self.fail and args[0] in self.fail:
            raise AdbError("device offline")
        if args[0] == "get-state":
            return b"device\n"
        if args[:2] == ["exec-out", "screencap"]:
            h, w = self.img.shape[:2]
            rgba = np.dstack([self.img[..., ::-1], np.full((h, w), 255, np.uint8)])
            hdr = struct.pack("<III", w, h, 1) + (b"\0\0\0\0" if self.head == 16 else b"")
            return hdr + rgba.tobytes()
        return b""


@pytest.mark.parametrize("head", [12, 16])
def test_adb_capture_parses_raw_screencap(head):
    img = frame("shop_open")
    w = AdbWindow(runner=FakeAdb(img, head))
    out = w.capture()
    assert out.shape == img.shape and np.array_equal(out, img)
    assert w.client_rect() == (0, 0, 1920, 1080)


def test_adb_bad_screencap_raises():
    with pytest.raises(AdbError):
        parse_raw_screencap(b"\x01\x00")
    with pytest.raises(AdbError):
        parse_raw_screencap(struct.pack("<III", 100, 100, 1) + b"\0" * 10)


def test_adb_input_commands():
    fa = FakeAdb()
    w = AdbWindow(runner=fa, tap_gap=0.0)
    w.tap(10.4, 20.6)
    w.double_click(5, 6)
    w.drag(1, 2, 3, 4)
    w.press("enter")
    w.press("backspace", 3)
    w.type_text("120")
    cmds = [c for c in fa.calls]
    assert cmds[0] == ["shell", "input", "tap", "10", "21"]
    assert cmds[1] == cmds[2] == ["shell", "input", "tap", "5", "6"]     # 點兩下（中間隔一下）
    assert cmds[3] == ["shell", "input", "swipe", "1", "2", "3", "4", "400"]
    assert cmds[4] == ["shell", "input", "keyevent", "66"]
    assert cmds[5] == ["shell", "input", "keyevent", "67", "67", "67"]
    assert cmds[6] == ["shell", "input", "text", "120"]
    with pytest.raises(AdbError):
        w.type_text("1;rm")                 # 只能輸入數字（不能被塞指令）
    with pytest.raises(AdbError):
        w.press("f12")


def test_adb_never_touches_real_mouse_and_reports_idle():
    w = AdbWindow(runner=FakeAdb())
    assert w.mouse_pos() == (0, 0)
    w.move_away(1, 2)
    w.wheel(1, 2, 3)
    w.activate()
    w.set_topmost(True)


def test_adb_connect_errors_are_explained():
    w = AdbWindow(runner=FakeAdb(fail={"get-state"}))
    with pytest.raises(AdbError, match="ADB 偵錯"):
        w.connect()
    assert not w.locate()


# =====================================================================
# 3. 模擬商店（手機版操作）
# =====================================================================
import ldsim  # noqa: E402
import realsim as rs  # noqa: E402

R = ROOT / "tests" / "samples" / "real"
SHOP = [f"filler{i}" for i in range(15)] + ["命中藥丸", "蘋果", "雞蛋", "烤肉", "柳橙", "棒冰棒",
                                              "ice", "filler20", "filler21", "filler22", "milk", "filler23",
                                              "filler24", "filler25"]
INV = ([("junk0", 15), ("junk1", 61), ("super", 200), ("elixir", 100), ("elixir", 100), ("junk2", 1),
        ("super", 200), ("special", 21), ("junk3", 4), ("junk4", 5), ("super", 200), ("ice", 160),
        ("vitality", 12), ("super", 33), ("ice", 200), ("ice", 117), ("elixir", 52), ("cube", 30)]
       + [("milk", 200)] * 24 + [("milk", 54)])
SELL = [
    {"template": "sell_special", "keep_stacks": 0},
    {"template": "sell_vitality", "keep_stacks": 0},
    {"template": "sell_elixir", "keep_stacks": 1},
]


@pytest.fixture
def ldworld(tmp_path, monkeypatch):
    tdir = tmp_path / "templates"
    ldsim.ld_templates(R, tdir, LDT)
    ddir = tmp_path / "data"
    ddir.mkdir()
    monkeypatch.setattr(calib, "TEMPLATE_DIR", tdir)
    monkeypatch.setattr(calib, "DATA_DIR", ddir)
    clock = rs.Clock()
    monkeypatch.setattr(shopmod, "time", clock)
    shopmod.random.seed(1)
    pc_cal = json.loads((R / "calibration.json").read_text(encoding="utf-8"))
    up = cv2.imread(str(tdir / "list_up.png"))

    def make(shop=SHOP, inv=INV, offset=(0, 0), dry=False, **kw):
        g = ldsim.TouchShop(shop, inv, offset=offset, clock=clock,
                            shortcut_item=tpl("shortcut_item"), shortcut_btn=tpl("shortcut_btn"), **kw)
        v = ldsim.LdView(g)
        cal = calib.Calibration(ldsim.ld_calibration(pc_cal))
        # ▲ 到 ▼ 上緣的距離（手機版實測 488；這個模擬畫面裡用一樣的方法量）
        img = v.capture() if g.shop_open else None
        dy = None
        if img is not None:
            ups = find_all(img, up, 0.88)
            dy = ldsim.ld_down_top(offset[1]) - min(m.y for m in ups)
        cfg = {"match_threshold": 0.85, "sell_threshold": 0.93, "max_per_purchase": 9999,
               "touch": True, "row_ratio": 0.25, "arrow_dy": dy or 484, "hud_top": ldsim.HUD_Y}
        bot = shopmod.ShopBot(v, cal, cfg, dry_run=dry)
        bot._update_shift()
        return g, v, bot, clock
    return make


def names(g):
    return [it["name"] for it in g.inv]


def test_ld_sim_frame_looks_like_phone():
    """模擬畫面：1920x1080、▼ 被工具列擋住比對不到、清單第 5 列被擋住一部分。"""
    g = ldsim.TouchShop(SHOP, INV, shortcut_item=tpl("shortcut_item"), shortcut_btn=tpl("shortcut_btn"))
    img = ldsim.LdView(g).capture()
    assert img.shape[:2] == (1080, 1920)
    down = cv2.resize(cv2.imread(str(R / "list_down.png")), None, fx=0.9, fy=0.9, interpolation=cv2.INTER_AREA)
    assert find(img, down, 0.88, [0, 800, 1920, 280]) is None     # 清單下方的 ▼ 被擋住了
    assert (img[ldsim.HUD_Y:] == (40, 30, 30)).all()


@pytest.mark.parametrize("offset", [(0, 0), (-40, -60), (30, 20)])
def test_ld_sell_whitelist_with_sell_button(ldworld, offset):
    g, v, bot, clock = ldworld(offset=offset)
    before = names(g)
    res = bot.sell_all(SELL)
    after = names(g)
    assert "special" not in after and "vitality" not in after
    assert after.count("elixir") == 1
    for n in set(before) - {"special", "vitality", "elixir"}:
        assert after.count(n) == before.count(n), f"賣錯東西了：{n}"
    assert res["sell_elixir"] == 2 and res["sell_special"] == 1 and res["sell_vitality"] == 1
    assert g.hud_taps == 0, "點到下方工具列了"
    assert g.double_on_rows == 0, "手機版不能用雙擊賣（只會選取）"


def test_ld_keeps_top_elixir_stack(ldworld):
    inv = [("junk0", 1), ("elixir", 100), ("junk1", 2), ("elixir", 80), ("super", 200), ("elixir", 7)] + \
          [("milk", 200)] * 10
    g, v, bot, clock = ldworld(inv=inv)
    bot.sell_all([{"template": "sell_elixir", "keep_stacks": 1}])
    el = [it["count"] for it in g.inv if it["name"] == "elixir"]
    assert el == [100], el


def test_ld_buy_milk(ldworld):
    g, v, bot, clock = ldworld(bag_slots=len(INV) + 30)
    assert bot.buy("buy_milk", 1500)
    assert g.bought == {"milk": 1500}
    assert g.hud_taps == 0


def test_ld_buy_partial_when_bag_full(ldworld):
    g, v, bot, clock = ldworld(bag_slots=len(INV))
    assert not bot.buy("buy_milk", 3000)
    assert bot.bag_full


def test_ld_scroll_to_bottom_and_back(ldworld):
    """▼ 比對不到也要能捲到底（點 ▼ 露出來的上半部）。"""
    g, v, bot, clock = ldworld()
    r = bot.region("inv_list")
    bot.scroll_end(r, +1)
    assert g.top["inv"] == len(g.inv) - rs.VIS
    bot.scroll_end(r, -1)
    assert g.top["inv"] == 0
    assert g.hud_taps == 0


def test_ld_open_shop_via_shortcut_and_close(ldworld):
    g, v, bot, clock = ldworld(shop_open=False)
    bot.open_shop()
    assert g.shop_open
    bot.close_shop()
    assert not g.shop_open
    assert not g.menu_open


def test_ld_dry_run_sells_and_buys_nothing(ldworld):
    g, v, bot, clock = ldworld(dry=True, bag_slots=len(INV) + 30)
    before = names(g)
    bot.sell_all(SELL)
    bot.buy("buy_milk", 500)
    assert names(g) == before and not g.bought and not g.sold


def test_ld_runner_refill_sells_then_buys_milk_only(ldworld, monkeypatch):
    """主程式 refill()：模擬器只買馴鹿奶，賣的藥跟電腦版一樣。"""
    import yaml
    from autopot import app
    g, v, bot, clock = ldworld(shop_open=False, bag_slots=len(INV) + 30)
    cfg = yaml.safe_load((ROOT / "config_ld.yaml").read_text(encoding="utf-8"))
    cfg.update({"match_threshold": 0.85, "sell_threshold": 0.93, "row_ratio": 0.25,
                "arrow_dy": bot.arrow_dy, "hud_top": ldsim.HUD_Y, "notify": {"beep": False}})
    cfg["sell"] = [dict(s, name=s["template"]) for s in SELL]
    monkeypatch.setattr(app.Calibration, "load", classmethod(lambda c: bot.cal))
    monkeypatch.setattr(bot.cal, "missing", lambda extra=(): [])
    monkeypatch.setattr(app, "time", clock)
    r = app.Runner(cfg, v)
    r.shop = bot
    assert r.refill({"hp": None}, "測試")
    assert set(g.bought) == {"milk"} and g.bought["milk"] == cfg["buy"]["hp"]["fallback_buy"]
    after = names(g)
    assert "special" not in after and after.count("elixir") == 1
    assert not g.shop_open
    assert g.hud_taps == 0


# =====================================================================
# 4. 主程式讀模擬器畫面（真實截圖 + 假的 adb）
# =====================================================================
def _ld_runner(monkeypatch, tmp_path, img):
    import yaml
    from autopot import app
    monkeypatch.setattr(calib, "TEMPLATE_DIR", LDT)
    monkeypatch.setattr(calib, "CALIB_FILE", ROOT / "ld" / "calibration.json")
    monkeypatch.setattr(calib, "DATA_DIR", tmp_path)
    cfg = yaml.safe_load((ROOT / "config_ld.yaml").read_text(encoding="utf-8"))
    cfg["notify"] = {"beep": False}
    fa = FakeAdb(img)
    win = AdbWindow(runner=fa)
    return app, app.Runner(cfg, win), fa


def test_ld_runner_reads_real_screen(monkeypatch, tmp_path):
    app, r, fa = _ld_runner(monkeypatch, tmp_path, frame("normal"))
    _, hp, mp, counts = r.read_state()
    assert hp == pytest.approx(1497 / 2701, abs=0.03)
    assert counts == {"hp": None}
    assert "下次定時補貨" in r.status_line(hp, mp, counts)


def test_ld_runner_follows_moved_status_bar(monkeypatch, tmp_path):
    """10/21 實測：下方狀態列整塊往右移了約 150px（ld_hud_moved.png，HP 2153/2701）。
    以前一直報「HP/MP 條被擋住」、一個多小時沒在顧；現在要在新位置讀到 HP。"""
    app, r, fa = _ld_runner(monkeypatch, tmp_path, frame("normal"))
    r.read_state()                                   # 先在正常位置讀一次（記下基準）
    fa.img = frame("hud_moved")
    _, hp, mp, _ = r.read_state()
    assert hp == pytest.approx(2153 / 2701, abs=0.03)
    assert mp == pytest.approx(20243 / 20307, abs=0.03)
    assert r._bar_shift[0] == pytest.approx(148, abs=4) and abs(r._bar_shift[1]) <= 12
    fa.img = frame("normal")                         # 移回來也要跟著回來
    _, hp, _, _ = r.read_state()
    assert hp == pytest.approx(1497 / 2701, abs=0.03) and r._bar_shift == (0, 0)


def test_ld_runner_moved_status_bar_while_hunting(monkeypatch, tmp_path):
    """10/22 00:23 實測：商店關了、die7 正常在打怪，但狀態列還是在右移的位置（HP 2701/2701）。"""
    app, r, fa = _ld_runner(monkeypatch, tmp_path, frame("normal"))
    r.read_state()
    fa.img = frame("hud_moved_hunting")
    _, hp, mp, _ = r.read_state()
    assert hp > 0.7 and mp == pytest.approx(15423 / 20307, abs=0.05)


def test_ld_runner_moved_status_bar_without_baseline(monkeypatch, tmp_path):
    """一開始狀態列就已經移位（沒有正常位置的基準）也要讀得到。"""
    app, r, fa = _ld_runner(monkeypatch, tmp_path, frame("hud_moved"))
    _, hp, _, _ = r.read_state()
    assert hp == pytest.approx(2153 / 2701, abs=0.03)


def test_ld_runner_black_or_other_screen_is_not_visible(monkeypatch, tmp_path):
    app, r, fa = _ld_runner(monkeypatch, tmp_path, np.zeros((1080, 1920, 3), np.uint8))
    with pytest.raises(app.NotVisible):
        r.read_state()
    fa.img = frame("normal")[::-1].copy()          # 不是遊戲畫面（例如切到別的 App）
    with pytest.raises(app.NotVisible):
        r.read_state()


def test_ld_config_matches_calibration():
    import yaml
    cfg = yaml.safe_load((ROOT / "config_ld.yaml").read_text(encoding="utf-8"))
    assert cfg["touch"] is True and set(cfg["buy"]) == {"hp"}
    assert cfg["buy"]["hp"]["template"] == "buy_milk"
    assert [s["template"] for s in cfg["sell"]] == ["sell_special", "sell_vitality", "sell_elixir", "sell_super"]
    for s in cfg["sell"]:
        assert (LDT / f"{s['template']}.png").exists()
    assert cfg["hotkeys"]["refill_now"] != "f10"          # 不跟電腦版的熱鍵打架
    up = LDCAL["regions"]["shop_list"][1] + 2              # ▲ 在清單區域上緣附近
    assert abs((up + cfg["arrow_dy"]) - 903) < 15          # 實測 ▼ 上緣 903
    assert LDCAL["regions"]["inv_list"][1] + LDCAL["regions"]["inv_list"][3] < cfg["hud_top"]


# =====================================================================
# 5. start.bat 一起啟動模擬器、遠端指令
# =====================================================================
def test_start_emulator_spawns_run_ld_and_stops_it():
    import importlib.util
    spec = importlib.util.spec_from_file_location("runmod", ROOT / "run.py")
    runmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runmod)

    class P:
        def __init__(self, args, cwd=None):
            self.args, self.cwd, self.killed = args, cwd, False

        def poll(self):
            return None if not self.killed else 0

        def terminate(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

    class A:
        dry_run = False
    p = runmod.start_emulator({"emulator": {"enabled": True}}, A(), popen=P)
    assert p is not None and p.args[-1].endswith("run_ld.py")
    runmod.stop_emulator(p)
    assert p.killed
    assert runmod.start_emulator({"emulator": {"enabled": False}}, A(), popen=P) is None
    assert runmod.start_emulator({}, A(), popen=P) is None
    A.dry_run = True
    assert runmod.start_emulator({"emulator": {"enabled": True}}, A(), popen=P).args[-1] == "--dry-run"


def test_default_config_enables_emulator():
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["emulator"]["enabled"] is True


def test_remote_command_shot_status_and_dryrun(monkeypatch, tmp_path):
    """data_ld/command.txt：shot 存畫面、status 印狀態、dryrun 走一次補貨但不算數。"""
    app, r, fa = _ld_runner(monkeypatch, tmp_path, frame("normal"))
    (tmp_path / "command.txt").write_text("shot", encoding="utf-8")
    assert r.check_command() == "shot"
    assert not (tmp_path / "command.txt").exists()
    assert list((tmp_path / "debug").glob("shot_*.png"))
    (tmp_path / "command.txt").write_text("status\n", encoding="utf-8")
    assert r.check_command() == "status"
    calls = []
    r.refill = lambda counts, why, emergency=False: (calls.append((why, r.shop.dry)), setattr(r.guard, "last_refill", 9e9),
                                                     setattr(r, "failures", 7))[0] is None
    r.guard.last_refill = 123.0
    (tmp_path / "command.txt").write_text("DryRun", encoding="utf-8")
    assert r.check_command() == "dryrun"
    assert calls and calls[0][1] is True                 # 試跑時一定是 dry
    assert r.shop.dry is False                           # 跑完恢復
    assert r.guard.last_refill == 123.0 and r.failures == 0
    (tmp_path / "command.txt").write_text("rm -rf", encoding="utf-8")
    assert r.check_command() is None
    assert r.check_command() is None                     # 沒有檔案


def test_real_inventory_with_covered_title(real_ld):
    """10/21 19:45 實測：道具欄開在上方，標題被「剩餘時間」框蓋住 → 以前「道具欄打不開」。
    現在用分頁那一排認出道具欄，並算出「現金」分頁的位置。"""
    img = frame("inventory_covered_title")
    cal = calib.Calibration(LDCAL)
    bot = shopmod.ShopBot(_StillWin(img), cal, {"touch": True})
    assert find(img, tpl("inv_open"), 0.85) is None             # 標題真的比對不到
    assert bot._inv_base(img) == "inv_tabs"
    x, y = bot._tab_pos("inv_tab_cash", "inv_tabs", img)
    assert abs(x - 1491) < 12 and abs(y - 232) < 10
    # 原本標題看得到的畫面：還是用標題
    img2 = frame("inventory_cash")
    assert bot._inv_base(img2) == "inv_open"
    x2, y2 = bot._tab_pos("inv_tab_cash", "inv_tabs", img2)
    assert abs(x2 - 1190) < 8 and abs(y2 - 270) < 8
    for name in ("normal", "shop_open", "closed"):
        assert bot._inv_base(frame(name)) is None, name


def test_supervisor_restarts_emulator_child():
    import importlib.util
    spec = importlib.util.spec_from_file_location("runmod2", ROOT / "run.py")
    runmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runmod)

    class P:
        def __init__(self, code=None):
            self.code = code
            self.killed = False

        def poll(self):
            return self.code

        def terminate(self):
            self.killed = True
            self.code = 0

        def wait(self, timeout=None):
            return 0
    spawned = []

    def spawn():
        p = P()
        spawned.append(p)
        return p
    slept = []
    sup = runmod.EmulatorSupervisor(P(None), spawn, sleep=slept.append, delay=60)
    assert not sup.step()                    # 還在跑 → 不動
    sup.child.code = 3                       # restart 指令
    assert sup.step() and len(spawned) == 1 and not slept     # 馬上重開
    sup.child.code = 1                       # 當掉
    assert sup.step() and len(spawned) == 2 and slept == [60]
    sup.stop()
    assert spawned[-1].killed
    assert not sup.step()                    # 停了就不再重開


def test_remote_restart_command(monkeypatch, tmp_path):
    app, r, fa = _ld_runner(monkeypatch, tmp_path, frame("normal"))
    (tmp_path / "command.txt").write_text("restart", encoding="utf-8")
    assert r.check_command() == "restart"
    assert r.restart and r.quit


def test_real_qty_field_1500_counts_four_digits():
    """10/21 實測：手機版數量框「1500」被算成 5 個字（「0」右下角淡灰 → 中間斷 1px；只看中間一半時底下那橫切到一半）。"""
    img = cv2.imread(str(ROOT / "tests" / "samples" / "qty" / "ld_1500.png"))   # 數量框外面多裁 20px

    class _Cal:
        data = {}

        def anchor(self, n):
            return None

        def region(self, n):
            return [20, 20, img.shape[1] - 40, img.shape[0] - 40]

    bot = shopmod.ShopBot.__new__(shopmod.ShopBot)
    bot.cal = _Cal()
    bot.shot = lambda: img
    assert bot._field_segments(Match(0, 0, 1, 1, 1.0)) == 4


def test_ld_dead_down_arrow_switches_to_dragging(ldworld):
    """10/21 實測：掃背包 12 分鐘、找馴鹿奶 6 分鐘 — 點 ▼ 沒反應，每列都先白點 6 次才改拖滑塊。
    現在：點了沒反應、拖才有用兩次之後，就直接拖（而且從上次有效的距離開始拖）。"""
    g, v, bot, clock = ldworld()
    orig_tap, orig_click = v.tap, v.click
    dead = {"n": 0}

    def near_down(x, y):
        return y >= ldsim.HUD_Y - 20

    def tap(x, y):
        if near_down(x, y):
            dead["n"] += 1
            return
        orig_tap(x, y)

    def click(x, y, jitter=0):
        if near_down(x, y):
            dead["n"] += 1
            return
        orig_click(x, y, jitter=jitter)
    v.tap, v.click = tap, click
    before = names(g)
    res = bot.sell_all(SELL)
    after = names(g)
    assert res["sell_elixir"] == 2 and res["sell_special"] == 1 and res["sell_vitality"] == 1
    for n in set(before) - {"special", "vitality", "elixir"}:
        assert after.count(n) == before.count(n), f"賣錯東西了：{n}"
    st = bot.scroll_stats
    assert st["drag"] > 10
    assert st["tap_fail"] <= 3, st                 # 只白點前兩、三列
    assert dead["n"] <= 3 * 6 + 2, dead            # 之後就不再點 ▼


def test_start_bat_relaunches_on_restart_code():
    """restart 指令：run.py 用結束代碼 3 結束，start.bat 看到就重新執行（Windows 上不能用 os.execv）。"""
    import run
    bat = (ROOT / "start.bat").read_text(encoding="utf-8", errors="replace")
    assert run.RESTART_CODE == 3
    assert "if %errorlevel%==3 goto run" in bat and ":run" in bat
    assert "os.execv(" not in (ROOT / "run.py").read_text(encoding="utf-8").split("def main")[-1]
