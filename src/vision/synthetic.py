"""dry-run 用的合成畫面產生器（無截圖時的後備）。

依 vision 設定畫一張與參考視窗同尺寸(約 1370x865)的假畫面：小地圖黃點（已知座標）、
HP／MP 條（已知比例）、以及兩隻「怪物」（把隨機紋理小圖貼在平台帶上當模板）。
這樣即使手邊沒有遊戲截圖，engine 的 dry-run 也能把「感知 → 決策」整條跑給你看。
若有真實截圖，請改用 `--demo-image <檔案>`，會直接在真畫面上偵測鱷魚、讀 HP/MP。
cv2／numpy 缺席時 build_demo() 回傳 None。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _CV_AVAILABLE = True
except ImportError:
    cv2 = None
    np = None
    _CV_AVAILABLE = False

_PLAYER_DOT_BGR = (0, 255, 255)   # 黃（對應小地圖玩家菱形）
_HP_BGR = (0, 0, 255)             # 紅
_MP_BGR = (238, 159, 0)           # 藍（貼近截圖 MP 條色）


@dataclass
class DemoScene:
    """一組合成場景（供 engine dry-run 與測試共用）。"""
    frame: object
    monster_templates: Dict[str, object]
    monster_positions: List[Tuple[str, int, int]]  # (name, x, y) 左上角視窗座標
    player_screen: Tuple[int, int]
    player_minimap_xy: Tuple[int, int]
    hp_ratio: float
    mp_ratio: float


def _paint_bar(frame, roi, ratio, color_bgr):
    if not roi:
        return
    l, t, w, h = (int(v) for v in roi)
    fill = max(0, min(w, int(round(ratio * w))))
    if fill > 0:
        cv2.rectangle(frame, (l, t), (l + fill - 1, t + h - 1), color_bgr, thickness=-1)


def build_demo(vision_config, hp_ratio=0.42, mp_ratio=0.63,
               player_minimap_xy=(30, 20), size=(865, 1370)):
    """組出一個 DemoScene；cv2／numpy 缺席時回傳 None。

    size 預設為參考視窗大小 (H, W)=(865, 1370)，好讓 settings 裡的真實 ROI 都落在畫面內。
    """
    if not _CV_AVAILABLE or np is None:
        return None

    h, w = size
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    vc = vision_config or {}

    # 小地圖 + 玩家黃點
    mm = vc.get("minimap", {})
    roi = mm.get("roi")
    if roi:
        l, t, ww, hh = (int(v) for v in roi)
        cv2.rectangle(frame, (l, t), (l + ww - 1, t + hh - 1), (60, 60, 60), thickness=1)
        px, py = player_minimap_xy
        cv2.circle(frame, (l + int(px), t + int(py)), 3, _PLAYER_DOT_BGR, thickness=-1)

    # HP／MP 條
    hb = vc.get("health_bar", {})
    _paint_bar(frame, hb.get("hp_bar_roi"), hp_ratio, _HP_BGR)
    _paint_bar(frame, hb.get("mp_bar_roi"), mp_ratio, _MP_BGR)

    # 怪物：固定隨機紋理小圖，貼在平台帶的左右兩側（模擬鱷魚分佈）
    rng = np.random.default_rng(7)
    tw, th = 100, 34
    tmpl = rng.integers(0, 256, (th, tw, 3), dtype=np.uint8)
    positions = [("demo_croc", 1010, 500), ("demo_croc", 150, 470)]
    for _name, mx, my in positions:
        frame[my:my + th, mx:mx + tw] = tmpl

    return DemoScene(
        frame=frame,
        monster_templates={"demo_croc": tmpl},
        monster_positions=positions,
        player_screen=(w // 2, h // 2),
        player_minimap_xy=player_minimap_xy,
        hp_ratio=hp_ratio,
        mp_ratio=mp_ratio,
    )
