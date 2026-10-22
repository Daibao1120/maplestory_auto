"""圖形介面（autopot/gui.py）的流程測試：用真實截圖拼的模擬商店當遊戲。

需要 tkinter 和畫面（Windows 一定有；Linux 用 xvfb-run 跑），沒有就跳過。
流程：掃描商店 → 選 HP 補品 → 套用 → 試跑（不會真的買）→ 掃描背包 → 勾要賣的 → 套用。
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import autopot.calib as calib  # noqa: E402
import autopot.shop as shopmod  # noqa: E402
import realsim as rs  # noqa: E402

R = ROOT / "tests" / "samples" / "real"


def _display_ok():
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _display_ok(), reason="沒有畫面可以開視窗")


class SimWin(rs.RealShop):
    keyword = "MapleStory Worlds"

    def locate(self):
        return True

    def activate(self):
        pass

    def foreground(self):
        return 0

    def restore_foreground(self, h):
        pass

    def set_topmost(self, on=True):
        pass

    def mouse_pos(self):
        return (0, 0)


@pytest.fixture
def gui(tmp_path, monkeypatch):
    tdir = tmp_path / "templates"
    shutil.copytree(R, tdir)
    ddir = tmp_path / "data"
    ddir.mkdir()
    from autopot.autocal import quickslot_cells
    c = json.loads((R / "calibration.json").read_text())
    c["regions"].update(quickslot_cells(c["regions"]["qs_hp"], c["regions"]["qs_mp"]))   # 自動校正會存 8 格
    (ddir / "calibration.json").write_text(json.dumps(c))
    monkeypatch.setattr(calib, "ROOT", tmp_path)
    monkeypatch.setattr(calib, "TEMPLATE_DIR", tdir)
    monkeypatch.setattr(calib, "DATA_DIR", ddir)
    monkeypatch.setattr(calib, "CALIB_FILE", ddir / "calibration.json")
    clock = rs.Clock()
    monkeypatch.setattr(shopmod, "time", clock)
    from autopot import gui as g
    from autopot.test_hooks import no_dialogs
    no_dialogs(monkeypatch, g)
    shop = ["蘋果", "雞蛋", "milk", "烤肉", "ice", "柳橙", "命中藥丸"]
    inv = [("milk", 200), ("elixir", 100), ("special", 20), ("milk", 200), ("elixir", 50)]
    sim = SimWin(shop, inv, clock=clock, bag_slots=40)
    root = tk.Tk()
    app = g.App(root)
    monkeypatch.setattr(app, "_game", lambda: sim)
    yield app, root, sim, shop, tmp_path
    try:
        root.destroy()
    except Exception:
        pass


def _wait(app, root, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        if not app._busy():
            root.update()
            app._poll()
            root.update()
            return
        time.sleep(0.02)
    raise AssertionError("背景工作一直沒做完")


def test_scan_pick_apply_dryrun_and_sell(gui):
    app, root, sim, shop, tmp = gui
    app.scan_shop()
    _wait(app, root)
    assert len(app.shop_items) == len(shop)
    milk = shop.index("milk")
    ice = shop.index("ice")
    app.shop_choice[milk].set("hp")
    app.shop_choice[ice].set("mp")
    app.buy_vars["hp"]["name"].set("馴鹿奶")
    app.buy_vars["mp"]["name"].set("紅豆刨冰")
    app.buy_vars["hp"]["fallback"].set(1234)
    app.buy_vars["mp"]["fallback"].set(321)
    app.apply_buy()
    import yaml
    saved = yaml.safe_load((tmp / "settings.yaml").read_text(encoding="utf-8"))
    assert saved["buy"]["hp"]["template"] == "buy_hp" and saved["buy"]["hp"]["learned"]
    assert (tmp / "templates" / "buy_hp.png").exists()
    assert saved["buy"]["hp"]["quickslot"] == "qs_home"
    assert app.var_problem.get() == ""                  # 可以開始了
    # 試跑：走完整流程但不會真的買
    app.dry_run()
    _wait(app, root)
    assert sim.bought == {}
    # 真的補一次（直接叫 Runner.refill，用模擬商店）
    from autopot.app import Runner
    r = Runner(app.cfg, sim)
    assert r.refill({"hp": None, "mp": None}, "測試")
    assert sim.bought == {"milk": 1234, "ice": 321}
    # 掃背包 → 勾萬能療傷藥（保留 1 組）
    app.scan_inv()
    _wait(app, root)
    names = []
    for it in sim.inv:
        if it["name"] not in names:
            names.append(it["name"])
    assert len(app.inv_items) == len(names)
    it, v_sell, v_name, v_keep = app.inv_vars[names.index("elixir")]
    v_sell.set(True)
    v_name.set("萬能療傷藥")
    v_keep.set(1)
    app.apply_sell()
    assert app.cfg["sell"][0]["name"] == "萬能療傷藥" and app.cfg["sell"][0]["keep_stacks"] == 1
    # 不能把要買的馴鹿奶設成要賣
    app.inv_vars[names.index("milk")][1].set(True)
    app.apply_sell()
    assert len(app.cfg["sell"]) == 1


def test_cannot_start_before_setup(gui, monkeypatch):
    app, root, sim, shop, tmp = gui
    shown = []
    from autopot import gui as g
    monkeypatch.setattr(g.messagebox, "showwarning", lambda *a, **k: shown.append(a))
    app.start_monitor()
    assert shown and "補品" in shown[0][1]
    assert not app._busy()
