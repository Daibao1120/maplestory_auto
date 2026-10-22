"""單元測試（不需要遊戲，Linux/Windows 都能跑）：python -m pytest tests -q"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autopot.digits import DigitReader, DigitTemplates  # noqa: E402
from autopot.guardian import CountTracker, Guardian  # noqa: E402
from autopot.vision import bar_ratio, find, find_all  # noqa: E402

S = ROOT / "tests" / "samples"
LABELS = json.loads((S / "labels.json").read_text(encoding="utf-8"))
SAFETY = {
    "timer_refill_hours": 4, "hp_emergency": 0.35, "hp_emergency_checks": 3,
    "mp_emergency": 0.08, "mp_emergency_checks": 10, "dead_hp": 0.02, "dead_checks": 3,
    "min_refill_gap_sec": 120, "emergency_recheck_sec": 60, "max_refill_failures": 3,
    "defer_if_user_active_sec": 20, "max_use_per_sec": 3, "ocr_jump_buffer": 60,
}


# ---------------- 數字辨識（真實截圖） ----------------
@pytest.mark.parametrize("fn", [f for f in LABELS if f.startswith("qs") and "s10" in f])
def test_digits_reference_size(fn):
    r = DigitReader(DigitTemplates.load(ROOT / "assets" / "digits_base.npz"))
    assert r.read(cv2.imread(str(S / fn))) == int(LABELS[fn])


@pytest.mark.parametrize("fn", [f for f in LABELS if f.startswith("qs") and "s11" in f])
def test_digits_other_size_never_reads_wrong_with_base(fn):
    # 視窗大小不同時，預設模板應該「讀不到」，而不是讀出錯的數字
    r = DigitReader(DigitTemplates.load(ROOT / "assets" / "digits_base.npz"))
    v = r.read(cv2.imread(str(S / fn)))
    assert v is None or v == int(LABELS[fn])


# ---------------- 血條 ----------------
def test_bar_ratio():
    assert bar_ratio(cv2.imread(str(S / "hp_full.png")), "hp") > 0.95
    assert abs(bar_ratio(cv2.imread(str(S / "mp_24pct.png")), "mp") - LABELS["mp_24pct.png"]) < 0.03


# ---------------- 模板比對 ----------------
def test_find_and_find_all():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)
    tpl = img[50:80, 100:160].copy()
    img[200:230, 300:360] = tpl
    ms = find_all(img, tpl, 0.95)
    assert [(m.x, m.y) for m in ms] == [(100, 50), (300, 200)]
    assert find(img, tpl, 0.95, region=[250, 150, 150, 150]).x == 300


# ---------------- 數量過濾 ----------------
def test_tracker_needs_two_reads_and_rejects_jumps():
    t = CountTracker(max_use_per_sec=3, jump_buffer=60)
    assert t.update(4360, 0) is None
    assert t.update(4360, 2) == 4360
    t.update(9999, 4); assert t.update(9999, 6) == 4360       # 沒補貨卻變多 → 丟掉
    t.update(30, 8); assert t.update(30, 10) == 4360          # 一下掉太多 → 丟掉
    t.update(4340, 12); assert t.update(4340, 14) == 4340     # 合理的減少 → 採用
    t.expect_increase_until = 100
    t.update(6750, 20); assert t.update(6750, 22) == 6750     # 補貨後變多 → 採用


def test_tracker_rate():
    t = CountTracker()
    for i in range(0, 1801, 2):
        v = 5000 - i // 4
        t.update(v, i); t.update(v, i + 0.5)
    rate = t.rate_per_hour(1801)
    assert 850 < rate < 950


# ---------------- 判斷邏輯 ----------------
def G():
    g = Guardian(SAFETY)
    g.last_refill = 0
    return g


def test_low_stock_triggers_refill():
    g = G()
    a, _ = g.tick(1000, 1.0, 0.5, {"hp": 1400, "mp": 500}, True, {"hp": 1500, "mp": 200})
    assert a == "refill"


def test_no_refill_when_enough():
    g = G()
    a, _ = g.tick(1000, 1.0, 0.5, {"hp": 4000, "mp": 500}, True, {"hp": 1500, "mp": 200})
    assert a is None


def test_hp_emergency_after_streak_then_alarm_if_not_fixed():
    g = G()
    for i in range(2):
        assert g.tick(1000 + i, 0.2, 0.5, {}, False, {})[0] is None
    assert g.tick(1002, 0.2, 0.5, {}, False, {})[0] == "emergency_refill"
    g.last_refill = 1002
    assert g.tick(1010, 0.2, 0.5, {}, False, {})[0] is None          # 剛補，先等
    assert g.tick(1070, 0.2, 0.5, {}, False, {})[0] == "alarm"       # 補完 60 秒還是低 → 警報
    assert g.tick(1130, 0.2, 0.5, {}, False, {})[0] == "emergency_refill"  # 再試一次


def test_mp_transient_dip_ignored():
    g = G()
    acts = [g.tick(1000 + i, 1.0, 0.05, {}, False, {})[0] for i in range(9)]
    assert all(a is None for a in acts)
    assert g.tick(1010, 1.0, 0.5, {}, False, {})[0] is None


def test_dead_detection():
    g = G()
    g.tick(1000, 0.0, 0.5, {}, False, {})
    g.tick(1001, 0.0, 0.5, {}, False, {})
    assert g.tick(1002, 0.0, 0.5, {}, False, {})[0] == "dead"


def test_timer_fallback_when_no_ocr():
    g = G()
    g.last_refill = 1000
    assert g.tick(1000 + 3 * 3600, 1.0, 0.5, {}, False, {})[0] is None
    assert g.tick(1000 + 4 * 3600, 1.0, 0.5, {}, False, {})[0] == "refill"


def test_timer_refill_also_when_ocr_ok():
    """數字讀得到、數量也夠，照樣每隔 timer_refill_hours 去一次（賣掉撿到的東西）。"""
    g = G()
    g.last_refill = 1000
    ok = {"hp": 4000, "mp": 500}
    th = {"hp": 1500, "mp": 200}
    assert g.tick(1000 + 3 * 3600, 1.0, 0.5, ok, True, th)[0] is None
    a, why = g.tick(1000 + 4 * 3600, 1.0, 0.5, ok, True, th)
    assert a == "refill" and "定時" in why


def test_tracker_digit_drop_needs_both_sides_near_boundary():
    t = CountTracker()
    t.update(1020, 0); t.update(1020, 2)
    t.update(102, 4); assert t.update(102, 6) == 1020        # 最後一位被擋住 → 丟掉
    t.update(1015, 8); assert t.update(1015, 10) == 1015
    t.update(990, 12); assert t.update(990, 14) == 990        # 真的用到 1000 以下 → 採用


def test_tracker_accepts_manual_purchase_after_stable_reads():
    """你自己去商店買（不是程式補的）→ 數字變多：先不信，連續讀到一樣的數字約 30 秒後採用。"""
    t = CountTracker()
    t.update(800, 0); t.update(800, 2)
    now = 4
    for _ in range(10):
        t.update(3000, now); now += 2
    assert t.trusted == 800
    for _ in range(10):
        t.update(3000, now); now += 2
    assert t.trusted == 3000
    # 讀到的數字一直在跳（不穩定）→ 不採用
    t2 = CountTracker()
    t2.update(800, 0); t2.update(800, 2)
    now = 4
    for i in range(40):
        t2.update(3000 if i % 2 else 3100, now); now += 2
    assert t2.trusted == 800


def test_zero_count_is_emergency():
    g = G()
    assert g.tick(1000, 1.0, 0.5, {"hp": 0, "mp": 500}, True, {"hp": 1500, "mp": 200})[0] == "emergency_refill"


def test_tracker_rejects_digit_count_change():
    t = CountTracker()
    t.update(4778, 0); t.update(4778, 2)
    t.update(778, 4); assert t.update(778, 6) == 4778        # 漏讀第一位 → 丟掉
    t2 = CountTracker()
    t2.update(1005, 0); t2.update(1005, 2)
    t2.update(999, 4); assert t2.update(999, 6) == 999       # 正常跨過 1000 → 採用


def test_start_x_prevents_skipping_first_digit():
    r = DigitReader(DigitTemplates.load(ROOT / "assets" / "digits_base.npz"))
    img = cv2.imread(str(S / "qs_1792308094323_2_s10.png"))
    assert r.read(img, start_x=14) == 4360
    assert r.read(img, start_x=45) is None


def test_low_hp_with_plenty_of_stock_alarms_instead_of_shopping():
    g = G()
    for i in range(3):
        a, _ = g.tick(1000 + i, 0.2, 0.5, {"hp": 4000, "mp": 500}, True, {"hp": 1500, "mp": 200})
    assert a == "alarm"
