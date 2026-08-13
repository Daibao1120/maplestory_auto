"""功能面測試：以合成影像實際跑 OpenCV 程式路徑（不只是 import）。

cv2 / numpy 缺席時自動 skip，因此在未安裝 OpenCV 的機器上也不會失敗。
"""
import os
import sys

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ---------------- 模板匹配 ----------------
def test_template_matcher_finds_known_patch(tmp_path):
    from src.vision.template_matcher import TemplateMatcher
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 40, (200, 320, 3), dtype=np.uint8)
    cv2.rectangle(frame, (100, 60), (140, 90), (0, 255, 0), thickness=-1)
    cv2.rectangle(frame, (110, 68), (125, 80), (255, 0, 0), thickness=-1)
    template = frame[60:90, 100:140].copy()

    cv2.imwrite(str(tmp_path / "green.png"), template)
    tm = TemplateMatcher(threshold=0.9)
    tm.load_directory(str(tmp_path))
    assert "green" in tm.template_names
    matches = tm.match(frame, "green")
    assert matches
    best = matches[0]
    assert abs(best.x - 100) <= 2 and abs(best.y - 60) <= 2


# ---------------- 小地圖定位 ----------------
def test_minimap_locate_player_no_roi():
    from src.vision.minimap import MinimapLocator
    mm = np.zeros((80, 120, 3), dtype=np.uint8)
    cv2.circle(mm, (90, 40), 3, (0, 255, 255), thickness=-1)  # 黃點
    pos = MinimapLocator().locate_player(mm)
    assert pos is not None
    assert abs(pos[0] - 90) <= 3 and abs(pos[1] - 40) <= 3


def test_minimap_locate_player_with_roi_and_picks_largest_blob():
    from src.vision.minimap import MinimapLocator
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    roi = [50, 40, 200, 150]                         # 小地圖區域
    cv2.circle(frame, (50 + 120, 40 + 90), 4, (0, 255, 255), thickness=-1)  # 玩家（大）
    frame[40 + 10, 50 + 10] = (0, 255, 255)          # 單像素雜訊（小）
    frame[40 + 120, 50 + 150] = (0, 255, 255)
    loc = MinimapLocator({"roi": roi, "min_blob_area": 3})
    pos = loc.locate_player(frame)
    assert pos is not None
    assert abs(pos[0] - 120) <= 3 and abs(pos[1] - 90) <= 3   # ROI 內座標、挑最大 blob


def test_minimap_none_when_absent():
    from src.vision.minimap import MinimapLocator
    assert MinimapLocator().locate_player(np.zeros((50, 50, 3), np.uint8)) is None


# ---------------- 血條讀值 ----------------
def _make_bar(width, ratio, color_bgr, roi):
    frame = np.zeros((60, roi[0] + roi[2] + 40, 3), dtype=np.uint8)
    l, t, w, h = roi
    fill = int(round(ratio * w))
    if fill > 0:
        cv2.rectangle(frame, (l, t), (l + fill - 1, t + h - 1), color_bgr, thickness=-1)
    return frame


def test_health_hp_ratio_accuracy():
    from src.vision.health_bar import HealthBarDetector
    roi = [10, 10, 200, 12]
    det = HealthBarDetector({"hp_bar_roi": roi, "hp_color_lower": [0, 120, 120],
                             "hp_color_upper": [10, 255, 255]})
    for ratio in (0.0, 0.25, 0.5, 0.9, 1.0):
        frame = _make_bar(200, ratio, (0, 0, 255), roi)
        est = det.read_hp_ratio(frame)
        assert abs(est - ratio) <= 0.02, (ratio, est)


def test_health_mp_ratio_accuracy():
    from src.vision.health_bar import HealthBarDetector
    roi = [10, 30, 200, 12]
    det = HealthBarDetector({"mp_bar_roi": roi, "mp_color_lower": [95, 120, 120],
                             "mp_color_upper": [135, 255, 255]})
    for ratio in (0.1, 0.356, 0.75):
        frame = _make_bar(200, ratio, (238, 159, 0), roi)   # 貼近截圖 MP 藍
        est = det.read_mp_ratio(frame)
        assert abs(est - ratio) <= 0.02, (ratio, est)


def test_health_defaults_safe_without_roi():
    from src.vision.health_bar import HealthBarDetector
    assert HealthBarDetector().read_hp_ratio(np.zeros((10, 10, 3), np.uint8)) == 1.0


# ---------------- 怪物偵測 + NMS ----------------
def test_monster_detect_and_nms():
    from src.vision.monster import MonsterDetector
    rng = np.random.default_rng(1)
    tmpl = rng.integers(0, 256, (20, 20, 3), dtype=np.uint8)
    frame = np.zeros((200, 300, 3), dtype=np.uint8)
    frame[50:70, 40:60] = tmpl
    frame[120:140, 220:240] = tmpl
    det = MonsterDetector({"match_threshold": 0.8, "nms_iou": 0.3})
    det.add_template("m", tmpl)
    dets = det.detect(frame)
    assert len(dets) == 2                              # NMS 後恰兩隻，而非數十個峰
    xs = sorted(d.center[0] for d in dets)
    assert any(abs(x - 50) <= 2 for x in xs) and any(abs(x - 230) <= 2 for x in xs)


def test_monster_empty_when_no_templates():
    from src.vision.monster import MonsterDetector
    det = MonsterDetector({"template_dir": "does/not/exist"})
    assert det.detect(np.zeros((50, 50, 3), np.uint8)) == []


def test_monster_roi_limits_search():
    from src.vision.monster import MonsterDetector
    rng = np.random.default_rng(2)
    tmpl = rng.integers(0, 256, (20, 20, 3), dtype=np.uint8)
    frame = np.zeros((200, 300, 3), dtype=np.uint8)
    frame[10:30, 10:30] = tmpl                         # ROI 外
    frame[120:140, 150:170] = tmpl                     # ROI 內
    det = MonsterDetector({"match_threshold": 0.8, "roi": [100, 100, 200, 80]})
    det.add_template("m", tmpl)
    dets = det.detect(frame)
    assert len(dets) == 1
    assert abs(dets[0].center[0] - 160) <= 2 and abs(dets[0].center[1] - 130) <= 2


# ---------------- engine 整合：dry-run 打怪鏈 ----------------
def test_engine_dry_run_hunts(capsys):
    from src.engine import BotEngine
    cfg = {
        "capture": {"backend": "mss", "fps_limit": 60},
        "window": {"title": "新楓之谷"},
        "vision": {
            "minimap": {"roi": [18, 131, 205, 74]},
            "health_bar": {"hp_bar_roi": [513, 807, 105, 13], "mp_bar_roi": [621, 807, 104, 13],
                           "hp_color_lower": [0, 120, 120], "hp_color_upper": [10, 255, 255],
                           "mp_color_lower": [95, 120, 120], "mp_color_upper": [135, 255, 255]},
            "monster": {"match_threshold": 0.6},
        },
        "combat": {"attack_key": "ctrl", "combo": 3, "attack_range_px": 700},
        "keys": {"hp_potion": "delete", "mp_potion": "end", "attack": "ctrl",
                 "move_left": "left", "move_right": "right"},
        "survival": {"hp_threshold": 0.5, "mp_threshold": 0.25, "potion_cooldown": 0.0},
    }
    eng = BotEngine(cfg, dry_run=True)
    eng.setup()
    eng.start(max_loops=1)
    assert eng._last_monsters, "合成場景應注入並偵測到鱷魚"
    out = capsys.readouterr().out
    assert "打怪" in out and "面向" in out
