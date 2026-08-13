"""功能面測試：以合成影像實際跑 OpenCV 程式路徑（不只是 import）。

cv2 / numpy 缺席時自動 skip，因此在未安裝 OpenCV 的機器上也不會失敗。
可用 `python -m pytest tests/test_vision_functional.py` 執行。
"""
import os
import sys

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_template_matcher_finds_known_patch(tmp_path):
    """在合成畫面放一塊有紋理的方塊，模板匹配應能定位它。"""
    from src.vision.template_matcher import TemplateMatcher

    rng = np.random.default_rng(0)
    frame = rng.integers(0, 40, (200, 320, 3), dtype=np.uint8)  # 低亮度雜訊背景（避免零變異）
    cv2.rectangle(frame, (100, 60), (140, 90), (0, 255, 0), thickness=-1)   # 綠底
    cv2.rectangle(frame, (110, 68), (125, 80), (255, 0, 0), thickness=-1)   # 內部藍塊 → 增加紋理
    template = frame[60:90, 100:140].copy()

    # 存成 PNG 再用公開 API 載入，一併驗證 imwrite / imread / load_directory
    cv2.imwrite(str(tmp_path / "green.png"), template)
    tm = TemplateMatcher(threshold=0.9)
    tm.load_directory(str(tmp_path))
    assert "green" in tm.template_names

    matches = tm.match(frame, "green")
    assert matches, "應至少找到一個匹配"
    best = matches[0]
    assert abs(best.x - 100) <= 2 and abs(best.y - 60) <= 2
    assert best.center == (best.x + best.w // 2, best.y + best.h // 2)


def test_minimap_locates_player_dot():
    """小地圖放一個玩家色（HSV 黃）點，locate_player 應命中其座標。"""
    from src.vision.minimap import MinimapLocator

    minimap = np.zeros((80, 120, 3), dtype=np.uint8)
    cv2.circle(minimap, (90, 40), 3, (0, 255, 255), thickness=-1)  # BGR 黃 → HSV H≈30
    pos = MinimapLocator().locate_player(minimap)

    assert pos is not None, "應偵測到玩家點"
    x, y = pos
    assert abs(x - 90) <= 3 and abs(y - 40) <= 3


def test_minimap_returns_none_when_absent():
    """畫面中沒有玩家色時應回傳 None，不應崩潰。"""
    from src.vision.minimap import MinimapLocator

    minimap = np.zeros((80, 120, 3), dtype=np.uint8)
    assert MinimapLocator().locate_player(minimap) is None


def test_health_bar_find_player_with_red():
    """畫面放一條紅色（HSV 預設 HP 色）像素塊，find_player 應回傳座標。"""
    from src.vision.health_bar import HealthBarDetector

    frame = np.zeros((120, 200, 3), dtype=np.uint8)
    cv2.rectangle(frame, (50, 30), (70, 34), (0, 0, 255), thickness=-1)  # BGR 紅
    pos = HealthBarDetector().find_player(frame)
    assert pos is not None
    assert HealthBarDetector().read_hp_ratio(frame) == 1.0  # stub 預設滿血
