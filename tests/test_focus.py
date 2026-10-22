"""不搶視窗（10/22 你反映：開著 start.bat 就不能看別的視窗，程式一直把遊戲叫到前面）。

用模擬器的真實截圖當遊戲畫面，換成一個「電腦版視窗」的假物件：
  * capture()            = 螢幕截圖（遊戲被蓋住時左下角是別的視窗）
  * capture_background() = 背景截圖（被蓋住也截得到；有的情況會失敗 → None）
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import autopot.calib as calib  # noqa: E402

LDT = ROOT / "ld" / "templates"


def frame(name):
    return cv2.imread(str(ROOT / "tests" / "samples" / "ld" / f"ld_{name}.png"))


class Win:
    hwnd = 1234

    def __init__(self):
        self.normal = frame("normal")
        self.covered = self.normal.copy()
        self.covered[600:, :1300] = (240, 240, 240)      # 瀏覽器蓋住遊戲左下角
        self.screen = self.covered
        self.bg = self.normal
        self.activations = 0
        self.fg = 999                                     # 你正在用的視窗（不是遊戲）
        self.idle = 1e9

    def capture(self):
        return self.screen.copy()

    def capture_background(self):
        return None if self.bg is None else self.bg.copy()

    def activate(self):
        self.activations += 1
        self.fg = self.hwnd

    def foreground(self):
        return self.fg

    def restore_foreground(self, h):
        self.fg = h

    def mouse_pos(self):
        return (0, 0)

    def idle_seconds(self):
        return self.idle

    def client_rect(self):
        return 0, 0, 1920, 1080

    def is_minimized(self):
        return False


def _runner(monkeypatch, tmp_path, win):
    import yaml
    from autopot import app
    monkeypatch.setattr(calib, "TEMPLATE_DIR", LDT)
    monkeypatch.setattr(calib, "CALIB_FILE", ROOT / "ld" / "calibration.json")
    monkeypatch.setattr(calib, "DATA_DIR", tmp_path)
    cfg = yaml.safe_load((ROOT / "config_ld.yaml").read_text(encoding="utf-8"))
    cfg["notify"] = {"beep": False}
    cfg["safety"]["defer_if_user_active_sec"] = 60
    return app, app.Runner(cfg, win)


def test_covered_game_is_monitored_with_background_capture(monkeypatch, tmp_path):
    w = Win()
    app, r = _runner(monkeypatch, tmp_path, w)
    _, hp, mp, _ = r.read_state()
    assert hp == pytest.approx(1497 / 2701, abs=0.03)
    assert r.background_mode and w.activations == 0
    w.screen = w.normal                                    # 遊戲又露出來了
    r.read_state()
    assert not r.background_mode


def test_covered_and_no_background_capture_raises_not_visible(monkeypatch, tmp_path):
    w = Win()
    w.bg = None
    app, r = _runner(monkeypatch, tmp_path, w)
    with pytest.raises(app.NotVisible):
        r.read_state()


def _loop(monkeypatch, r, app, w, ticks=200, refill_on_start=False):
    """跑主迴圈 ticks 圈，回傳「開始監控之後」把遊戲叫到前面的次數（不算程式剛啟動那一次）。"""
    t = [1_000_000.0]
    n = {"i": 0, "act": 0}
    orig = w.activate

    def act():
        if n["i"] > 0:
            n["act"] += 1
        orig()
        w.fg = 999                                # 你的視窗還在前面（不讓「遊戲在前面」影響判斷）
    w.activate = act

    def fake_sleep(s):
        t[0] += max(s, 1.0)
        n["i"] += 1
        if n["i"] >= ticks:
            r.quit = True
    monkeypatch.setattr(app.time, "time", lambda: t[0])
    monkeypatch.setattr(app.time, "sleep", fake_sleep)
    monkeypatch.setattr(r, "setup_hotkeys", lambda: None)
    r.quit = False
    r.run(refill_on_start=refill_on_start)
    w.activate = orig
    return n["act"]


def test_never_steals_focus_while_you_use_another_window(monkeypatch, tmp_path):
    """遊戲被蓋住、背景截圖也不行、你沒在動滑鼠鍵盤 → 以前 10 秒就把遊戲叫到前面；現在不叫（還不用補貨）。"""
    w = Win()
    w.bg = None
    app, r = _runner(monkeypatch, tmp_path, w)
    assert _loop(monkeypatch, r, app, w, ticks=300) == 0        # 15 分鐘都不叫


def test_timer_refill_due_waits_until_you_are_idle(monkeypatch, tmp_path):
    """該定時補貨了，但遊戲被蓋住：你正在別的視窗打字 → 不叫；你離開電腦 → 才叫出遊戲去補貨。"""
    w = Win()
    w.bg = None
    w.idle = 2.0                                  # 你一直在打字
    app, r = _runner(monkeypatch, tmp_path, w)
    monkeypatch.setattr(r, "refill", lambda *a, **k: True)
    assert _loop(monkeypatch, r, app, w, ticks=100, refill_on_start=True) == 0
    w.idle = 1e9                                  # 你離開電腦了
    assert _loop(monkeypatch, r, app, w, ticks=30, refill_on_start=True) >= 1


def test_covered_game_with_background_capture_never_activates(monkeypatch, tmp_path):
    """背景截圖可以用 → 照常監控，從頭到尾不用把遊戲叫到前面。"""
    w = Win()
    app, r = _runner(monkeypatch, tmp_path, w)
    assert _loop(monkeypatch, r, app, w, ticks=100) == 0
    assert r.background_mode


def test_keyboard_in_game_does_not_count_as_busy(monkeypatch, tmp_path):
    """你在遊戲裡按鍵打怪（遊戲在前面）不算「在忙別的事」；在別的視窗打字才算。"""
    w = Win()
    app, r = _runner(monkeypatch, tmp_path, w)
    w.idle = 1.0
    w.fg = w.hwnd
    assert not r.user_active(10_000.0)
    w.fg = 999
    assert r.user_active(10_000.0)
