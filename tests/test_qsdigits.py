"""快捷欄數字辨識（autopot/qsdigits.py）。

樣本是從遊戲實際截圖剪下來的快捷欄格子（家裡電腦 2764x1555），檔名裡有正確答案：
    qs_{hp|mp}_{數量}_{來源}.png
其中 qs_mp_721 那張格子是粉紅色的（選取中），讀不到是正常的，必須回傳 None。

除了原圖，也做各種「最壞情況」：擋住一部分、平移、加雜訊、換背景……
規則只有一條：可以讀不到（None），但絕對不能讀錯。
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

from autopot.qsdigits import QuickslotReader  # noqa: E402

SAMPLES = sorted((ROOT / "tests" / "samples" / "qs").glob("qs_*.png"))
UNREADABLE = {"qs_mp_721_refill_fail_0038.png"}     # 粉紅色選取中的格子
R = QuickslotReader()


def truth(p: Path) -> str:
    return re.search(r"qs_(?:hp|mp)_(\d+)_", p.name).group(1)


def readable():
    return [p for p in SAMPLES if p.name not in UNREADABLE]


def test_have_samples():
    assert len(readable()) >= 9


@pytest.mark.parametrize("p", readable(), ids=lambda p: p.name)
def test_reads_real_sample(p):
    text, info = R.read_text(cv2.imread(str(p)))
    assert text == truth(p), info


@pytest.mark.parametrize("p", [p for p in SAMPLES if p.name in UNREADABLE], ids=lambda p: p.name)
def test_tinted_slot_is_none(p):
    assert R.read(cv2.imread(str(p))) is None


def _never_wrong(img, p, label, allow_prefix=False):
    text, info = R.read_text(img)
    ok = text in (None, truth(p))
    if allow_prefix and text and truth(p).startswith(text):
        ok = True       # 後面的字整個被蓋掉，看起來就跟比較短的數字一樣（交給 CountTracker 擋，見下面的測試）
    assert ok, f"{p.name} {label}: 讀成 {text}（{info}）"
    return text


@pytest.mark.parametrize("p", readable(), ids=lambda p: p.name)
@pytest.mark.parametrize("dx", [-3, -2, -1, 1, 2, 3])
@pytest.mark.parametrize("dy", [-2, 0, 2])
def test_shifted_crop(p, dx, dy):
    """校正框差幾 px（框大一點、小一點、偏一點）：還是要讀對（字沒被切到的情況）。"""
    img = cv2.imread(str(p))
    pad = cv2.copyMakeBorder(img, 4, 4, 4, 4, cv2.BORDER_REPLICATE)
    H, W = img.shape[:2]
    shifted = pad[4 + dy: 4 + dy + H, 4 + dx: 4 + dx + W]
    text = _never_wrong(shifted, p, f"平移 {dx},{dy}")
    if dx >= -2:                       # 往左切掉太多，第一個字會被切到
        assert text == truth(p)


def _system_safe(v_true: int, got: int) -> bool:
    """讀錯的數字丟進 CountTracker（已經信任正確數字），連續 20 次（40 秒）：
    要嘛被丟掉，要嘛跟正確數字只差一點點（例如 1048 讀成 1043，對補貨判斷沒影響）。"""
    from autopot.guardian import CountTracker
    t = CountTracker()
    t.update(v_true, 0)
    t.update(v_true, 2)
    for k in range(20):
        t.update(got, 4 + 2 * k)
    return t.trusted == v_true or abs(t.trusted - v_true) <= 10


@pytest.mark.parametrize("p", readable(), ids=lambda p: p.name)
@pytest.mark.parametrize("color", [(0, 0, 0), (255, 255, 255), (120, 80, 40), (230, 230, 230)])
def test_occluded_digit_never_wrong(p, color):
    """滑鼠游標、說明框擋住某一個字：
      * 字被蓋掉一部分時，有時看起來真的就像別的數字（例如 8 的左半邊被黑色蓋住 = 3、
        最後一位整個被蓋住 = 少一位）。這種讀錯一定要被 CountTracker 擋掉（或差距小到沒影響）。"""
    img = cv2.imread(str(p))
    v = int(truth(p))
    for x0 in range(5, 100, 3):
        for w in (6, 12, 20):
            im = img.copy()
            im[70:105, x0:x0 + w] = color
            text, info = R.read_text(im)
            if text in (None, truth(p)):
                continue
            label = f"{p.name} 擋住 x={x0} 寬 {w} 顏色 {color}: 讀成 {text}（{info}）"
            assert _system_safe(v, int(text)), label


@pytest.mark.parametrize("p", readable(), ids=lambda p: p.name)
def test_noise_never_wrong(p):
    rng = np.random.default_rng(len(p.name))
    img = cv2.imread(str(p))
    for sigma in (2, 5, 15):
        noisy = np.clip(img.astype(np.int16) + rng.normal(0, sigma, img.shape), 0, 255).astype(np.uint8)
        text = _never_wrong(noisy, p, f"雜訊 {sigma}")
        if sigma <= 2:                 # 一點點雜訊還是要讀得到（實際截圖的顏色是完全一樣的）
            assert text == truth(p)


@pytest.mark.parametrize("p", readable(), ids=lambda p: p.name)
def test_darker_or_brighter_never_wrong(p):
    """整個畫面變暗（遊戲出現對話框遮罩）或變亮：顏色對不上了 → 只能 None 或讀對。"""
    img = cv2.imread(str(p)).astype(np.float32)
    for k in (0.5, 0.8, 0.95, 1.05):
        _never_wrong(np.clip(img * k, 0, 255).astype(np.uint8), p, f"亮度 x{k}")


def test_blank_and_random_slots_are_none():
    rng = np.random.default_rng(0)
    assert R.read(np.zeros((121, 116, 3), np.uint8)) is None
    assert R.read(np.full((121, 116, 3), 255, np.uint8)) is None
    for _ in range(30):
        img = rng.integers(0, 256, (121, 116, 3), dtype=np.uint8)
        assert R.read(img) is None


def test_empty_slot_icon_only_is_none():
    """數字整個被蓋掉（只剩圖示）→ None。"""
    for p in readable():
        img = cv2.imread(str(p))
        img[68:110, :] = (30, 30, 30)
        assert R.read(img) is None, p.name


def _render(text: str, bg: np.ndarray) -> np.ndarray:
    """用字形和遊戲的漸層顏色把數字畫到背景上（跟遊戲一樣：3 倍大、外框黑色）。"""
    from autopot.qsdigits import FONT, FONT_COLORS, GLYPH_TOP
    img = bg.copy()
    x = 12
    for ch in text:
        bm = FONT[ch]
        h, w = len(bm), len(bm[0])
        b = np.array([[c == "#" for c in row] for row in bm], bool)
        big = np.kron(b, np.ones((3, 3), bool))[:26]
        ring = np.zeros((big.shape[0] + 6, big.shape[1] + 6), bool)
        for dy, dx in ((0, 0), (3, 0), (-3, 0), (0, 3), (0, -3)):
            ring[3 + dy:3 + dy + big.shape[0], 3 + dx:3 + dx + big.shape[1]] |= big
        y0 = GLYPH_TOP - 3
        sub = img[y0:y0 + ring.shape[0], x - 3:x - 3 + ring.shape[1]]
        sub[ring[:sub.shape[0], :sub.shape[1]]] = (10, 10, 10)
        for yy in range(big.shape[0]):
            row = img[GLYPH_TOP + yy, x:x + big.shape[1]]
            row[big[yy, :row.shape[0]]] = FONT_COLORS[yy]
        x += w * 3 + 4
    return img


@pytest.mark.parametrize("text", ["0", "7", "11", "99", "100", "888", "1000", "3339", "7777", "6750", "1111", "9999", "4101"])
def test_synthetic_numbers(text):
    """畫出各種數字（樣本裡沒出現過的 3、7、8、9 組合、很多個 1）都要讀對。"""
    rng = np.random.default_rng(int(text))
    for bgv in ((60, 60, 60), (120, 140, 160)):
        bg = np.empty((121, 116, 3), np.uint8)
        bg[:] = bgv
        bg = np.clip(bg.astype(np.int16) + rng.integers(-20, 20, bg.shape), 0, 255).astype(np.uint8)
        img = _render(text, bg)
        got, info = R.read_text(img)
        assert got == text, (bgv, info)


def test_speed():
    import time
    img = cv2.imread(str(readable()[0]))
    best = 9.0
    for _ in range(5):
        t = time.time()
        R.read(img)
        best = min(best, time.time() - t)
    assert best < 0.3


@pytest.mark.parametrize("p", readable(), ids=lambda p: p.name)
def test_tracker_rejects_hidden_last_digit(p):
    """最後一個字整個被蓋住（讀成少一位）→ CountTracker 不能採用。"""
    from autopot.guardian import CountTracker
    img = cv2.imread(str(p))
    v = int(truth(p))
    t = CountTracker()
    t.update(v, 0)
    assert t.update(v, 2) == v
    texts = set()
    for x0 in range(30, 100, 2):
        im = img.copy()
        im[70:105, x0:] = (0, 0, 0)
        got = R.read(im)
        if got is not None and got != v:
            texts.add(got)
            t2 = CountTracker(); t2.update(v, 0); t2.update(v, 2)
            for k in range(20):
                t2.update(got, 4 + 2 * k)
            assert t2.trusted == v, (got, v)
    assert texts or len(truth(p)) <= 1
