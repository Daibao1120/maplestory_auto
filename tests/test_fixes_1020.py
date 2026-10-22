"""10/20 早上實測回報的問題：
  1. 切換到別的視窗 → 誤判「角色可能死亡」
  2. 狀態列顯示「馴鹿奶 ? 個」
  3. 學道具／賣東西時，「消耗」分頁沒切過去（停在裝備分頁）
  4. 超級藥水學會了卻一組都沒賣（捲動一列的確認太嚴格）
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autopot.shop import ShopBot  # noqa: E402
from autopot.vision import bar_framed  # noqa: E402

CAL = json.loads((ROOT / "tests/samples/real/calibration.json").read_text())["regions"]
MAIN = cv2.imread(str(ROOT / "tests/samples/exp/main_lv191_36.61pct.png"))
MID = cv2.imread(str(ROOT / "tests/samples/real/shop_mid.png"))


def test_frame_visible_on_real_screen():
    assert bar_framed(MAIN, CAL["hp_bar"]) and bar_framed(MAIN, CAL["mp_bar"])


def test_really_dead_still_has_frame():
    im = MAIN.copy()
    x, y, w, h = CAL["hp_bar"]
    im[y:y + h, x:x + w] = (45, 51, 57)          # HP 條全空
    assert bar_framed(im, CAL["hp_bar"])


def test_covered_by_dark_window_is_not_dead():
    im = MAIN.copy()
    im[1400:, :1300] = (30, 30, 30)              # 深色主題的視窗蓋住左下角
    assert not bar_framed(im, CAL["hp_bar"])


def test_black_game_screen_is_not_dead():
    assert not bar_framed(np.zeros_like(MAIN), CAL["hp_bar"])


def test_use_tab_selected_detection_on_real_screen():
    pos = (1174 + 418, 587 + 199)                  # 離開商店 + 校正的相對位置
    assert ShopBot._tab_selected(MID, pos)
    assert not ShopBot._tab_selected(MID, (pos[0] - 100, pos[1]))     # 裝備分頁（沒選中）


def test_one_row_shift_tolerates_off_by_one_pixel():
    x, y, w, h = 765, 840, 575, 456          # 商品清單（每列都不一樣）
    a = MID[y:y + h, x:x + w]
    for real_pitch in (114, 115, 116):
        b = MID[y + real_pitch:y + real_pitch + h, x:x + w]
        assert ShopBot._shifted_one_row(a, b, 115, (100, 170), (110, 180)), real_pitch
    assert not ShopBot._shifted_one_row(a, a, 115, (100, 170), (110, 180))   # 滑塊動了但內容沒動
    assert not ShopBot._shifted_one_row(a, MID[y + 230:y + 230 + h, x:x + w], 115, (100, 170), (110, 180))  # 捲兩列
