"""調參 UI 的純函式測試：量平台分析、建議值、調參檔套用（不開視窗）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.tuner import (  # noqa: E402
    analyze_walk_samples, suggest_from_geometry, load_tuning, save_tuning,
)
from tools.hold_and_wiggle import HoldWiggle  # noqa: E402


def _walk(samples, t0, key, x0, x1, seconds, hz=10):
    n = int(seconds * hz)
    for i in range(n + 1):
        t = t0 + i / hz
        x = x0 + (x1 - x0) * i / n
        samples.append((t, key, x))
    return t0 + seconds


def test_analyze_walk_samples_basic():
    # 往右 2 秒走 x 40→50（5 px/s），放開，往左走回去
    s = []
    t = _walk(s, 0.0, "right", 40, 50, 2.0)
    s.append((t + 0.1, None, 50))
    _walk(s, t + 0.3, "left", 50, 40, 2.0)
    geo = analyze_walk_samples(s)
    assert geo is not None
    assert abs(geo["width_px"] - 10) < 0.6
    assert abs(geo["speed_px_s"] - 5.0) < 0.6
    assert abs(geo["half_seconds"] - 1.0) < 0.3      # 中間走到邊 ≈ 1 秒
    assert abs(geo["cross_seconds"] - 2.0) < 0.6
    assert geo["segments"] == 2


def test_analyze_direction_flip_without_release_keeps_both_segments():
    # 左右鍵直接切換（沒有放開的空檔）→ 兩段速度都要算到
    s = []
    t = _walk(s, 0.0, "right", 40, 50, 2.0)
    _walk(s, t + 0.05, "left", 50, 40, 2.0)
    geo = analyze_walk_samples(s)
    assert geo is not None and geo["segments"] == 2


def test_analyze_insufficient_samples():
    assert analyze_walk_samples([(0.0, None, None)] * 10) is None
    assert analyze_walk_samples([]) is None


def test_suggestions_are_safe():
    geo = {"width_px": 20, "speed_px_s": 5.0, "cross_seconds": 4.0,
           "half_seconds": 2.0, "segments": 3, "x_min": 40, "x_max": 60}
    sug = suggest_from_geometry(geo)
    assert sug["edge_margin"] >= 2
    assert 0 < sug["max_step_seconds"] < geo["half_seconds"]  # 一步遠小於「走到邊」


def test_tuning_file_roundtrip(tmp_path):
    p = str(tmp_path / "tuning.yaml")
    save_tuning({"move_time": 0.25, "smart_face": True, "edge_margin": 4}, p)
    data = load_tuning(p)
    assert data["move_time"] == 0.25
    assert data["smart_face"] is True
    assert load_tuning(str(tmp_path / "nope.yaml")) == {}


def test_hold_wiggle_applies_tuning():
    hw = HoldWiggle(dry_run=True)
    changed = hw._apply_tuning({"move_time": 0.3, "interval_min": 30.0,
                                "interval_max": 20.0, "swap_every": 0})
    assert hw.move_time == 0.3 and "move_time" in changed
    assert hw.interval_min == 20.0 and hw.interval_max == 30.0   # 反了自動對調
    assert hw.swap_every == 1                                    # 下限保護


def test_hold_wiggle_max_step_caps_move_time():
    # 量平台得到的安全上限要能壓住過大的步伐
    hw = HoldWiggle(dry_run=True)
    hw._apply_tuning({"move_time": 0.5, "max_step_seconds": 0.2})
    assert hw.move_time == 0.2


def test_hold_wiggle_ignores_bad_values():
    hw = HoldWiggle(dry_run=True)
    before = hw.move_time
    changed = hw._apply_tuning({"move_time": "abc", "unknown_key": 1})
    assert hw.move_time == before
    assert "unknown_key" not in changed
