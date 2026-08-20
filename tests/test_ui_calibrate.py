"""UI 自動校準測試：解析度無關地找出 HP/MP 條、小地圖面板、EXP 文字區。

真實情境（實測）：使用者把遊戲從 2736 改成 3840 寬時，UI 不隨世界等比縮放，
所有硬寫座標整組失效——這些測試釘住「自動找」的行為。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from src.vision.ui_calibrate import (longest_run, find_bar, find_minimap_rect,  # noqa: E402
                                     exp_text_roi_from_bars, BarReader)


def make_frame(w=1920, h=1080, hp_len=200, mp_len=200):
    """合成一張畫面：底部一紅一藍長條（HP/MP），左上角深色小地圖面板。"""
    # 背景要夠亮：真實遊戲世界比小地圖面板亮得多，暗背景會讓整個象限都
    # 被當成「深色」而糊成一塊（合成素材不真實會測出假結果）。
    f = np.full((h, w, 3), 170, dtype=np.uint8)
    y = int(h * 0.95)
    f[y - 5:y + 5, 400:400 + hp_len] = (40, 40, 230)      # 紅（BGR）
    f[y - 5:y + 5, 610:610 + mp_len] = (230, 120, 40)     # 藍
    f[60:260, 20:260] = (30, 30, 30)                      # 小地圖面板（深色）
    return f, y


def test_longest_run_basics():
    assert longest_run([]) == (0, 0)
    assert longest_run([False, False]) == (0, 0)
    assert longest_run([True, True, False, True]) == (2, 0)
    assert longest_run([False, True, True, True, False]) == (3, 1)


def test_find_bars_at_two_resolutions():
    for w, h in ((1920, 1080), (3840, 2160)):
        f, y = make_frame(w, h, hp_len=int(w * 0.1), mp_len=int(w * 0.1))
        hp = find_bar(f, "red")
        mp = find_bar(f, "blue")
        assert hp and mp, (w, h)
        assert abs(hp["x"] - 400) <= 2 and abs(hp["y"] - y) <= 6
        assert mp["x"] > hp["x"]


def test_find_bar_none_when_absent():
    f = np.full((600, 800, 3), 170, dtype=np.uint8)
    assert find_bar(f, "red") is None


def test_find_minimap_rect_ignores_big_ui_window():
    f, _ = make_frame()
    # 玩家開了一個很大的深色視窗（技能欄）→ 不可誤認為小地圖
    f[300:900, 300:1200] = (25, 25, 25)
    r = find_minimap_rect(f)
    assert r is not None
    x, y, w_, h_ = r
    assert x < 60 and y < 120          # 貼左上角的才是小地圖
    assert w_ < 400 and h_ < 400


def test_exp_text_roi_sits_right_of_mp_bar():
    f, y = make_frame()
    hp, mp = find_bar(f, "red"), find_bar(f, "blue")
    roi = exp_text_roi_from_bars(f, hp, mp)
    assert roi is not None
    x, ry, w_, h_ = roi
    assert x >= mp["x"] + mp["len"]     # 在 MP 條右邊
    assert ry < mp["y"]                 # 文字在條的上方
    assert w_ > 0 and h_ > 0


def test_exp_text_roi_none_without_mp():
    f, _ = make_frame()
    assert exp_text_roi_from_bars(f, None, None) is None


def test_bar_reader_self_calibrates_and_reads_ratio():
    f, _ = make_frame(hp_len=200)
    r = BarReader("red")
    assert r.calibrate(f) is True
    assert r.full >= 190
    assert r.read(f) == pytest.approx(1.0, abs=0.05)
    # 血量掉一半
    half, _ = make_frame(hp_len=100)
    assert r.read(half) == pytest.approx(0.5, abs=0.08)


def test_bar_reader_updates_full_when_bar_grows():
    r = BarReader("red")
    r.calibrate(make_frame(hp_len=120)[0])
    r.read(make_frame(hp_len=200)[0])          # 升等後條變長
    assert r.full >= 190


def test_bars_pair_accepts_partially_filled_mp():
    """MP 沒滿時長度會比 HP 短很多——不可因此拒絕。

    實機案例：HP 滿（196px）、MP 剩 56%（109px），舊的「長度需為 HP 的
    0.6~1.6 倍」限制把正確的 MP 條擋掉，連帶整個 UI 校準判定失敗。
    """
    from src.vision.ui_calibrate import find_bars_pair
    f, y = make_frame(hp_len=196, mp_len=109)
    hp, mp = find_bars_pair(f)
    assert hp and mp
    assert abs(mp["x"] - 610) <= 3 and abs(mp["len"] - 109) <= 3


def test_bars_pair_ignores_far_away_blue_ui():
    """遠處的藍色 UI（聊天視窗、圖示）不可被當成 MP 條。"""
    from src.vision.ui_calibrate import find_bars_pair
    f, y = make_frame(hp_len=196, mp_len=0)
    f[y - 5:y + 5, 1500:1750] = (230, 120, 40)      # 很遠的藍色區塊
    hp, mp = find_bars_pair(f)
    assert hp is not None and mp is None


def test_bars_pair_returns_hp_even_without_mp():
    from src.vision.ui_calibrate import find_bars_pair
    f, _ = make_frame(hp_len=196, mp_len=0)
    hp, mp = find_bars_pair(f)
    assert hp is not None and mp is None
