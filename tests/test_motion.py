"""免模板怪物活動偵測測試（三畫格差分）＋自動撿物。

換地圖就要重新採集怪物截圖太不實用；怪會動、背景不會，用這點就能在任何
地圖立即運作。這些測試釘住「該動的才算、鏡頭捲動不算、自己不算」。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from src.vision.motion import MotionDetector, split_activity  # noqa: E402
from tools.overnight import ClassProfile, NightWatchCore, WorldState  # noqa: E402


def bg(h=400, w=600, v=120):
    return np.full((h, w, 3), v, dtype=np.uint8)


def feed(det, frames):
    out = None
    for f in frames:
        out = det.update(f)
    return out


# ---------------- 純函式 ----------------

def test_split_activity_excludes_center():
    m = np.zeros((10, 100), dtype=np.uint8)
    m[:, 5:15] = 1        # 左
    m[:, 45:55] = 1       # 中央（應被排除）
    m[:, 85:95] = 1       # 右
    left, right = split_activity(m, 50, 20)
    assert left == 100 and right == 100      # 中央那塊沒被算進來


def test_split_activity_full_width_when_no_dead_zone():
    m = np.ones((4, 40), dtype=np.uint8)
    left, right = split_activity(m, 20, 0)
    assert left + right == m.size


# ---------------- 偵測器 ----------------

def test_needs_warmup_before_ready():
    d = MotionDetector()
    assert d.update(bg())["ready"] is False
    assert d.update(bg())["ready"] is False
    assert d.update(bg())["ready"] is True       # 第 3 幀起背景模型可用


def test_static_scene_has_no_activity():
    d = MotionDetector()
    r = feed(d, [bg()] * 6)
    assert r["ready"] and r["activity"] == 0.0 and r["blobs"] == 0
    assert d.hint_side() is None


def test_moving_blob_on_the_right_is_detected():
    d = MotionDetector(min_blob=20)
    fs = [bg(), bg()]                                  # 先讓背景模型建立
    for i in range(6):
        f = bg()
        f[150:200, 460 + i * 12:500 + i * 12] = 250    # 右側持續移動的物體
        fs.append(f)
    r = feed(d, fs)
    assert r["ready"] and r["blobs"] >= 1
    assert r["right"] >= 1 and r["left"] == 0      # 右側 1 個移動物體
    assert d.hint_side() == "right"


def test_camera_pan_is_ignored():
    d = MotionDetector()
    fs = [bg(), bg()] + [np.full((400, 600, 3), 40 + i * 70, dtype=np.uint8)
                         for i in range(3)]
    r = feed(d, fs)
    assert r["panning"] is True and r["ready"] is False
    assert d.hint_side() is None


def test_single_frame_flash_is_filtered_out():
    # 只在中間那幀出現的東西（技能特效閃一下）不算持續移動
    d = MotionDetector(min_blob=20)
    flash = bg()
    flash[100:150, 500:560] = 255
    r = feed(d, [bg(), bg(), bg(), flash, bg(), bg()])
    assert r["blobs"] == 0 or r["activity"] < 0.01


def test_hint_side_needs_clear_difference():
    d = MotionDetector()
    d.last = {"ready": True, "left": 5, "right": 4, "activity": 0.1,
              "blobs": 9, "panning": False}
    assert d.hint_side() is None                  # 差距不明顯 → 不給提示
    d.last["left"] = 12
    assert d.hint_side() == "left"


# ---------------- 核心整合：面向後備與撿物 ----------------

def W(now, **kw):
    return WorldState(now=now, **kw)


def SPAN(width=20.0, dl=10.0, dr=10.0):
    return {"width": width, "dist_left": dl, "dist_right": dr}


def farming(profile=None):
    c = NightWatchCore(heal_mode="external", profile=profile or ClassProfile())
    c.tick(W(0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    c.tick(W(1.0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    assert c.state == "FARM"
    return c


def test_motion_hint_drives_facing_when_templates_find_nothing():
    c = farming()
    acts = c.tick(W(5.0, hp=0.9, pos=(50, 90), span=SPAN(), mon_hint="left"))
    assert [a.arg for a in acts if a.verb == "turn"] == ["left"]
    assert c.facing == "left"
    # 同一側不重複轉身
    acts = c.tick(W(9.0, hp=0.9, pos=(50, 90), span=SPAN(), mon_hint="left"))
    assert not any(a.verb == "turn" for a in acts)


def test_template_counts_take_priority_over_motion_hint():
    c = farming()
    acts = c.tick(W(5.0, hp=0.9, pos=(50, 90), span=SPAN(),
                    mon_left=3, mon_right=0, mon_hint="right"))
    assert [a.arg for a in acts if a.verb == "turn"] == ["left"]


def test_pickup_fires_periodically_only_when_configured():
    c = farming(ClassProfile(pickup_key="z", pickup_every=4.0))
    got = []
    t = 2.0
    for _ in range(12):
        got += [a.arg for a in c.tick(W(t, hp=0.9, pos=(50, 90), span=SPAN()))
                if a.verb == "tap" and a.arg == "z"]
        t += 2.0
    assert 4 <= len(got) <= 8                     # 約每 4 秒一次（含抖動）
    c2 = farming(ClassProfile())                  # 沒設定撿物鍵 → 完全不按
    t = 2.0
    for _ in range(12):
        assert not [a for a in c2.tick(W(t, hp=0.9, pos=(50, 90), span=SPAN()))
                    if a.verb == "tap"]
        t += 2.0
