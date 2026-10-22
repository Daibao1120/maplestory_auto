"""EXP 條讀取、自動狩獵按鈕、快升等自動停止的判斷（用真實截圖）。"""
import sys
from pathlib import Path

import cv2
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autopot.autostop import AutoStop  # noqa: E402
from autopot.expbar import box_rows, exp_ratio, find_autohunt_button, find_bars, ratio_to_fill  # noqa: E402

D = ROOT / "tests" / "samples" / "exp"


def img(name):
    return cv2.imread(str(D / name))


@pytest.mark.parametrize("name,truth", [
    ("main_lv191_36.61pct.png", 0.3661),            # 主角 191 等，遊戲畫面 2764 寬
    ("alt_lv88_4.57pct_nocursor.png", 0.0457),      # 方塊仔 88 等，遊戲畫面 1278 寬
])
def test_exp_ratio_matches_game_percentage(name, truth):
    r = exp_ratio(img(name))
    assert r is not None and abs(r - truth) < 0.005, r


@pytest.mark.parametrize("name", ["main_synth_99.0pct.png", "main_synth_99.5pct.png"])
def test_nearly_full_reads_as_full(name):
    """最後 2% 左右看起來都是全滿（填色兩端藏在框下面）→ 讀成「最大值」約 98%。"""
    r = exp_ratio(img(name))
    assert r is not None and r > 0.975


def test_cursor_on_exp_bar_is_not_misread():
    """滑鼠停在 EXP 條上（白色手套把框切斷）→ 這次不算，不能讀出錯的數字。"""
    assert exp_ratio(img("alt_lv88_4.57pct.png")) is None


@pytest.mark.parametrize("name", ["main_lv191_36.61pct.png", "alt_lv88_4.57pct.png", "main_lv191_98.05pct.png"])
def test_autohunt_button_found_at_any_window_size(name):
    im = img(name)
    m = find_autohunt_button(im)
    assert m is not None
    H, W = im.shape[:2]
    assert m.center[0] < W * 0.15 and H * 0.25 < m.center[1] < H * 0.45


def test_no_button_on_unrelated_image():
    im = img("main_lv191_36.61pct.png").copy()
    im[: im.shape[0] // 2, : im.shape[1] // 2] = (40, 40, 40)
    assert find_autohunt_button(im) is None


def test_hp_mp_exp_boxes_in_order():
    y, bars = find_bars(img("main_lv191_36.61pct.png"))
    (h0, h1), (m0, m1), (e0, e1) = bars
    assert h1 < m0 < m1 < e0 < e1


# ---------- 什麼時候按 ----------
class Seq:
    """依序回傳 EXP 為指定比例的畫面（用真實截圖改 EXP 條）。"""
    base = None

    def __init__(self):
        if Seq.base is None:
            Seq.base = img("main_lv191_36.61pct.png")
        self.y, (_, _, (self.x0, self.x1)) = find_bars(Seq.base)

    def frame(self, pct):
        import numpy as np
        f = Seq.base.copy()
        w = self.x1 - self.x0
        r0, r1 = box_rows(f, self.y, self.x0, self.x1)
        empty = f[r0:r1, self.x1 - 3:self.x1 - 2].copy()
        fill = f[r0:r1, self.x0 + 20:self.x0 + 21].copy()
        f[r0:r1, self.x0:self.x1] = np.repeat(empty, w, axis=1)
        n = ratio_to_fill(pct, w)
        if n:
            f[r0:r1, self.x0:self.x0 + n] = np.repeat(fill, n, axis=1)
        return f


def test_clicks_once_when_crossing_threshold():
    s, st = Seq(), AutoStop(at=0.98)
    acts = [st.step(s.frame(p), t * 2.0) for t, p in enumerate([0.95, 0.96, 0.97, 0.979, 0.981, 0.985, 0.99])]
    # 要連續 2 次都在門檻以上才按（避免一次讀錯就按）
    assert acts.count("click") == 1 and acts.index("click") in (4, 5)


def test_does_not_click_if_already_above_at_start():
    """一開始就超過門檻（沒看到它在漲）→ 不確定自動狩獵開著，不按（避免把關著的打開）。"""
    s, st = Seq(), AutoStop(at=0.98)
    acts = [st.step(s.frame(p), t * 2.0) for t, p in enumerate([0.985, 0.986, 0.99])]
    assert "click" not in acts


def test_level_up_after_click_is_reported():
    s, st = Seq(), AutoStop(at=0.98)
    acts = [st.step(s.frame(p), t) for t, p in enumerate([0.97, 0.982, 0.983])]
    assert acts == ["wait", "wait", "click"]
    assert st.step(s.frame(0.01), 10) == "leveled" and st.leveled_after_click


# ---------- 公司電腦（遊戲畫面 1280x720）回報 EXP 70% 卻讀成 100% ----------
# 小視窗的 EXP 填色在中線是偏白的黃色 (238,246,127)，之前被當成白框跳過。
@pytest.mark.parametrize("pct", [0.10, 0.30, 0.50, 0.70, 0.75, 0.90, 0.97])
def test_small_window_real_fill_colour(pct):
    import numpy as np
    im = img("alt_lv88_4.57pct_nocursor.png")
    y, bars = find_bars(im)
    x0, x1 = bars[2]
    s = im.copy()
    r0, r1 = box_rows(im, y, x0, x1)
    n = ratio_to_fill(pct, x1 - x0)
    col = s[r0:r1, x0 + 2:x0 + 3]                    # 真實的填色（含上下漸層）
    s[r0:r1, x0:x0 + n] = np.repeat(col, n, axis=1)
    r = exp_ratio(s)
    assert r is not None and abs(r - pct) < 0.01, (pct, r, find_bars(s))


# ---------- 10/20 11:58 公司電腦回報：EXP 75.57% 讀成 100% ----------
def test_office_full_hp_mp_reads_75pct_not_100():
    """這台視窗的 HP 最紅那一列在條的上緣，上下一列是白色亮線 → 之前只看 3 列，把空的部分當成有填。
    （這張是程式存的 exp_debug.png，上面有程式畫的框；框到的位置是對的，是讀數錯了）"""
    im = img("office2_75.57pct_debug.png")
    bars = (696, [(296, 438), (441, 583), (591, 742)])      # 當時程式找到的三個框
    r = exp_ratio(im, bars)
    assert r is not None and abs(r - 0.7557) < 0.01, r
