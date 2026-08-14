"""以「畫面上的角色位置」做平台邊緣探測——防掉落的第二道防線。

小地圖解析度太低（1 小地圖 px ≈ 數十畫面 px），貼近平台邊緣時不夠精細。
這裡直接看畫面：

    腳下取樣區 = 角色錨點下方一小塊（正站著的地面，必定可走）
    前方取樣區 = 同一高度、往移動方向前面一段距離

兩塊的平均顏色差太大 → 前方不是同一種地面（水面／懸空／地圖邊界）→
不要再往那個方向走。以「腳下」當基準即自我校準，免逐地圖調顏色。

numpy 缺席、讀不到畫面、或取樣不到腳下基準時**不否決**（回傳安全），
由小地圖邊界那道防線把關；前方取樣超出畫面則視為不安全（已貼到畫面邊）。
"""
from __future__ import annotations

try:
    import numpy as np  # type: ignore
except ImportError:
    np = None  # type: ignore

_DEFAULTS = {
    "ahead_px": 90,        # 探測點在角色前方多遠（畫面 px）
    "foot_offset_y": 40,   # 腳底取樣中心在角色錨點下方多遠（畫面 px）
    "patch": (40, 16),     # 取樣區大小 (寬, 高)
    "max_color_diff": 60,  # 前方 vs 腳下平均色差門檻（BGR 歐氏距離，0~441）
}


def _patch_mean(frame, cx, cy, pw, ph):
    """取樣以 (cx, cy) 為中心、pw×ph 區域的平均 BGR；有效面積不足一半回 None。"""
    h, w = frame.shape[:2]
    x1, x2 = int(cx - pw // 2), int(cx + pw // 2)
    y1, y2 = int(cy - ph // 2), int(cy + ph // 2)
    cx1, cy1 = max(0, x1), max(0, y1)
    cx2, cy2 = min(w, x2), min(h, y2)
    if (cx2 - cx1) * (cy2 - cy1) < (pw * ph) / 2:
        return None
    region = frame[cy1:cy2, cx1:cx2]
    return region.reshape(-1, region.shape[-1]).astype("float64").mean(axis=0)


def probe_ahead_safe(frame, anchor, direction, config=None):
    """角色往 direction 再走是否安全（前方地面與腳下同色）。

    frame: BGR 畫面；anchor: 角色在畫面上的 (x, y)；direction: "left"/"right"。
    """
    if np is None or frame is None or not hasattr(frame, "shape"):
        return True
    if direction not in ("left", "right"):
        return True
    cfg = dict(_DEFAULTS)
    cfg.update({k: v for k, v in (config or {}).items() if v is not None})
    pw, ph = (int(v) for v in cfg["patch"])
    ax, ay = anchor
    foot_y = int(ay) + int(cfg["foot_offset_y"])

    ref = _patch_mean(frame, int(ax), foot_y, pw, ph)
    dx = int(cfg["ahead_px"]) if direction == "right" else -int(cfg["ahead_px"])
    ahead = _patch_mean(frame, int(ax) + dx, foot_y, pw, ph)

    if ahead is None:
        return False   # 前方取樣超出畫面 → 已貼畫面邊，別再往外走
    if ref is None:
        return True    # 腳下取樣不到 → 沒有基準，不否決（交給小地圖那道防線）
    diff = float(np.linalg.norm(ref - ahead))
    return diff <= float(cfg["max_color_diff"])
