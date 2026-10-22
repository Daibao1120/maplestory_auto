"""從畫面讀等級（autopot/levelread.py），以及預估程式用畫面上的等級（autopot/levelup.py）。

樣本：tests/samples/level/lv_{等級}_{畫面寬}_{來源}.png，都是你的真實截圖剪下來的
（家裡 2764 寬的遊戲畫面、3840 寬的桌面截圖；1278/1280 寬是公司電腦的小畫面）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from autopot.levelread import FONT, LevelReader, find_boxes, GX0, GX1, GY0, GY1  # noqa: E402
from autopot.levelup import EXP_TABLE, LevelForecast  # noqa: E402

SAMPLES = sorted((ROOT / "tests" / "samples" / "level").glob("lv_*.png"))
R = LevelReader()


def truth(p):
    return int(re.search(r"lv_(\d+)_", p.name).group(1))


def small(p):
    return int(re.search(r"_(\d+)w_", p.name).group(1)) < 2000


def test_have_all_digits_in_samples():
    digits = set("".join(str(truth(p)) for p in SAMPLES))
    assert set("123456789") <= digits


@pytest.mark.parametrize("p", SAMPLES, ids=lambda p: p.name)
def test_reads_real_sample(p):
    got, info = R.read_detail(cv2.imread(str(p)))
    if small(p):
        assert got in (None, str(truth(p))), info      # 公司的小畫面：讀不到可以，不能讀錯
    else:
        assert got == str(truth(p)), info


@pytest.mark.parametrize("p", SAMPLES, ids=lambda p: p.name)
def test_noise_and_occlusion_never_wrong(p):
    img = cv2.imread(str(p))
    boxes = find_boxes(img)
    rng = np.random.default_rng(truth(p))
    for sigma in (3, 8, 20):
        n = np.clip(img.astype(np.int16) + rng.normal(0, sigma, img.shape), 0, 255).astype(np.uint8)
        assert R.read(n) in (None, truth(p))
    if not boxes:
        return
    for (x, y, w, h) in boxes:                  # 滑鼠游標（白色/黑色小方塊）蓋住某一格數字的一部分
        for color in ((255, 255, 255), (0, 0, 0)):
            for fx in (0.2, 0.5):
                im = img.copy()
                cx, cy = x + int(w * fx), y + int(h * 0.3)
                im[cy:cy + h // 3, cx:cx + w // 3] = color
                assert R.read(im) in (None, truth(p))


def test_blank_and_random_are_none():
    rng = np.random.default_rng(1)
    assert R.read(np.zeros((300, 800, 3), np.uint8)) is None
    for _ in range(20):
        assert R.read(rng.integers(0, 256, (200, 600, 3), dtype=np.uint8)) is None


# ---------- 在真實畫面上「改掉」等級數字（模擬升等），測試預估程式 ----------
HOME = ROOT / "tests" / "samples" / "exp" / "main_lv191_36.61pct.png"


def paint_level(img, lv: int):
    """把畫面上的等級方塊重畫成 lv（用同一個字型，白字在橘色方塊上）。"""
    out = img.copy()
    boxes = find_boxes(img)
    assert boxes and len(boxes) == len(str(lv))
    for (x, y, w, h), d in zip(boxes, str(lv)):
        inner = out[y + 3:y + h - 3, x + 3:x + w - 3]
        inner[:] = (20, 140, 250)                       # 橘色
        bm = FONT[d]
        for r in range(7):
            for k in range(7):
                if bm[r][k] == "#":
                    ya = y + int(round(h * (GY0 + (GY1 - GY0) * r / 7)))
                    yb = y + int(round(h * (GY0 + (GY1 - GY0) * (r + 1) / 7)))
                    xa = x + int(round(w * (GX0 + (GX1 - GX0) * k / 7)))
                    xb = x + int(round(w * (GX0 + (GX1 - GX0) * (k + 1) / 7)))
                    out[ya:yb, xa:xb] = 255
    return out


@pytest.mark.parametrize("lv", [191, 196, 197, 198, 199, 200])
def test_painted_levels_read_back(lv):
    img = paint_level(cv2.imread(str(HOME)), lv)
    assert R.read(img) == lv


def test_screen_level_overrides_wrong_config(tmp_path):
    """10/21 實測：你已經 197，config 還寫 196 → 讀到畫面上的 197 就改用 197。"""
    img = paint_level(cv2.imread(str(HOME)), 197)
    f = LevelForecast.load(196, tmp_path / "level.json")
    f.reader = R
    t = 0.0
    for _ in range(40):
        f.update(img, t)
        t += 2
    assert f.level == 197
    assert LevelForecast.load(196, tmp_path / "level.json").level == 197     # 存起來了


def test_screen_unreadable_keeps_config(tmp_path):
    img = cv2.imread(str(HOME))
    for (x, y, w, h) in find_boxes(img):
        img[y:y + h, x:x + w] = (40, 40, 40)            # 等級被蓋住
    f = LevelForecast.load(196, tmp_path / "level.json")
    f.reader = R
    for i in range(30):
        f.update(img, i * 2.0)
    assert f.level == 196


def test_level_up_counted_once_when_screen_changes_first():
    """畫面上的等級先變 197、EXP 條才歸零 → 只能 +1（不能變 198）。"""
    f = LevelForecast(level=196)
    t = 0.0
    for i in range(100):
        f.add(0.90 + i * 0.0008, t); t += 2
    for _ in range(3):
        f.see_level(197, t); t += 10
    assert f.level == 197
    f.add(0.01, t); t += 2
    f.add(0.02, t)
    assert f.level == 197


def test_level_up_counted_once_when_exp_drops_first():
    f = LevelForecast(level=196)
    t = 0.0
    for i in range(100):
        f.add(0.90 + i * 0.0008, t); t += 2
    f.add(0.01, t); t += 2                     # EXP 條先歸零 → 197
    assert f.level == 197
    for _ in range(3):
        f.see_level(197, t); t += 10           # 畫面跟著變 197 → 一致，不變
    assert f.level == 197


def test_forecast_correct_after_screen_level_fix():
    """改用畫面上的等級之後，速度用新的一級重新算，預估要對。"""
    f = LevelForecast(level=196)
    per_sec = 265_600_000 / 3600
    for _ in range(3):
        f.see_level(197, 0.0)
    t, exp = 0.0, 0.3 * EXP_TABLE[197]
    while t < 900:
        f.add(exp / EXP_TABLE[197], t)
        t += 2
        exp += per_sec * 2
    out = f.forecast(t)
    true_next = (EXP_TABLE[197] - exp) / per_sec
    assert out["next"] == pytest.approx(true_next, rel=0.05)
