"""控制台的純邏輯測試（不開視窗）：EXP 速率估算、疊圖繪製、參數套用。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from tools.overnight import exp_per_hour  # noqa: E402
from tools.control_panel import Worker, draw_overlay, PREVIEW_W  # noqa: E402


# ---------------- EXP 速率 ----------------

def test_exp_per_hour_needs_enough_observation():
    assert exp_per_hour([], 100.0) == 0.0
    assert exp_per_hour([100.0], 100.0) == 0.0          # 只有一筆不外推
    assert exp_per_hour([90.0, 100.0], 100.0) == 0.0    # 觀察不足 60 秒


def test_exp_per_hour_rate_and_window():
    events = [i * 60.0 for i in range(11)]              # 每分鐘一次，共 10 分鐘
    assert 55 <= exp_per_hour(events, 600.0) <= 70
    # 只看最近 window 秒：更早的事件不影響
    old = [-5000.0, -4000.0] + events
    assert 55 <= exp_per_hour(old, 600.0, window=900.0) <= 70


# ---------------- 疊圖 ----------------

def test_draw_overlay_marks_monsters_and_scales():
    frame = np.zeros((1596, 2736, 3), dtype=np.uint8)
    snap = {"anchor": (1400, 980), "y_band": (-60, 60),
            "monsters": [(1200, 940, 180, 60, 0.8, True),
                         (600, 500, 180, 60, 0.7, False)]}
    out = draw_overlay(frame, snap)
    assert out.shape[1] == PREVIEW_W                     # 等比縮放到預覽寬
    assert abs(out.shape[0] - int(1596 * PREVIEW_W / 2736)) <= 1
    assert out.max() > 0                                 # 有畫上東西
    # 同層用紅框、別層用灰框 → 兩種顏色都要出現
    flat = out.reshape(-1, 3)
    has_red = ((flat[:, 2] > 150) & (flat[:, 0] < 90)).any()
    has_grey = ((abs(flat[:, 0].astype(int) - flat[:, 2].astype(int)) < 30)
                & (flat[:, 0] > 100)).any()
    assert has_red and has_grey


def test_draw_overlay_without_anchor_uses_center():
    frame = np.zeros((800, 1200, 3), dtype=np.uint8)
    out = draw_overlay(frame, {"anchor": None, "y_band": (-50, 50), "monsters": []})
    assert out.shape[1] == PREVIEW_W


# ---------------- 參數套用 ----------------

def test_worker_applies_params_to_core():
    from tools.overnight import NightWatchCore

    class FakePer:
        y_band = [-60, 60]

    w = Worker(lambda m: None)
    w._core = NightWatchCore(heal_mode="external")
    w._per = FakePer()
    w.params = {"facing_mode": "left", "exp_flip_after": "35",
                "y_band": (-80.0, 90.0)}
    w._apply_params()
    assert w._core.facing == "left"
    assert w._core.EXP_FLIP_AFTER == 35.0
    assert w._per.y_band == (-80.0, 90.0)


def test_worker_ignores_bad_params_and_auto_facing():
    from tools.overnight import NightWatchCore
    w = Worker(lambda m: None)
    w._core = NightWatchCore(heal_mode="external")
    w._core.facing = "right"
    w.params = {"facing_mode": "auto", "exp_flip_after": "abc"}
    w._apply_params()
    assert w._core.facing == "right"                      # auto 不覆寫
    assert w._core.EXP_FLIP_AFTER == NightWatchCore.EXP_FLIP_AFTER


def test_worker_starts_stopped_and_stop_is_idempotent():
    w = Worker(lambda m: None)
    assert w.snap["state"] == "STOPPED" and w.running is False
    w.stop()
    w.stop()
    assert w.running is False


# ---------------- ROI 尺寸變動（實機崩潰過的路徑）----------------

def test_region_changed_survives_roi_resize():
    """自動重新校準後 ROI 尺寸會變（實測 EXP 區寬 220→224）。

    舊版直接相減 → ValueError 讓整支工具當掉。這裡釘住：尺寸不同時不比較、
    只重建基準，下一幀才恢復偵測。
    """
    from tools.overnight import region_changed
    a = np.zeros((26, 220, 3), dtype=np.int16)
    b = np.zeros((26, 224, 3), dtype=np.int16)      # 重新校準後變寬
    changed, prev = region_changed(a, b)
    assert changed is False and prev.shape == b.shape
    # 下一幀同尺寸就恢復正常比較
    c = b.copy()
    c[:, :, :] = 40
    changed, prev = region_changed(prev, c)
    assert changed is True


def test_region_changed_handles_none_and_no_change():
    from tools.overnight import region_changed
    changed, prev = region_changed(None, np.zeros((4, 4, 3), dtype=np.int16))
    assert changed is False and prev is not None
    same = np.zeros((4, 4, 3), dtype=np.int16)
    assert region_changed(same, same.copy())[0] is False
