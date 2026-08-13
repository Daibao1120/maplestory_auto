"""怪物偵測：多模板 template matching + 門檻 + 非極大值抑制（NMS）。

因為經典版是高解析重繪、且本專案不附遊戲美術，使用者需自行把怪物截圖放到
assets/templates/monsters/（可多張）。偵測流程：
    （可選）裁切搜尋 ROI → 對每個模板做 cv2.matchTemplate（TM_CCOEFF_NORMED）
    → 收集超過門檻的位置 → 跨模板做 IoU-NMS 去重 → 回傳 (x, y, score) 清單。
模板資料夾、門檻、搜尋 ROI、NMS IoU 皆可由 settings 設定。cv2/numpy 缺席時回傳 []。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _CV_AVAILABLE = True
except ImportError:
    cv2 = None
    np = None
    _CV_AVAILABLE = False


@dataclass
class Detection:
    """一次怪物偵測結果（座標為整張畫面的像素座標）。"""
    name: str
    x: int
    y: int
    w: int
    h: int
    score: float

    @property
    def center(self):
        return (self.x + self.w // 2, self.y + self.h // 2)


class MonsterDetector:
    """怪物偵測器（多模板 template matching）。

    參數 config 對應 settings.yaml 的 vision.monster 區塊：
        template_dir:     模板資料夾（放使用者自己的怪物截圖）
        match_threshold:  matchTemplate 相似度門檻 (0~1)
        roi:              搜尋範圍 [left, top, width, height]；None = 全畫面
        nms_iou:          NMS 的 IoU 門檻（重疊超過此值視為同一隻）
    """

    def __init__(self, config=None):
        config = config or {}
        self.template_dir = config.get("template_dir", "assets/templates/monsters")
        self.threshold = float(config.get("match_threshold", 0.75))
        self.roi = config.get("roi")
        self.iou_thresh = float(config.get("nms_iou", 0.30))
        self._templates: Dict[str, "np.ndarray"] = {}
        self._loaded = False

    # ---- 模板 ----
    def add_template(self, name, image):
        """直接注入模板（測試 / dry-run 合成用）。"""
        self._templates[name] = image
        self._loaded = True
        return self

    def load_templates(self):
        """從 template_dir 載入所有圖片作為模板；資料夾不存在則保持空（detect 回 []）。"""
        self._loaded = True
        if not _CV_AVAILABLE:
            return self
        d = self.template_dir
        if d and os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                stem, ext = os.path.splitext(fn)
                if ext.lower() in (".png", ".jpg", ".jpeg", ".bmp"):
                    img = cv2.imread(os.path.join(d, fn), cv2.IMREAD_COLOR)
                    if img is not None:
                        self._templates[stem] = img
        return self

    @property
    def template_names(self):
        return list(self._templates.keys())

    # ---- 偵測 ----
    def detect(self, frame):
        """回傳一組 Detection（已 NMS 去重、依分數由高到低排序）；無模板或缺套件回傳 []。"""
        if not _CV_AVAILABLE or np is None or frame is None:
            return []
        if not self._loaded:
            self.load_templates()
        if not self._templates:
            return []

        region, ox, oy = self._crop(frame)
        if region.size == 0:
            return []
        rh, rw = region.shape[:2]

        dets: List[Detection] = []
        for name, tmpl in self._templates.items():
            th, tw = tmpl.shape[:2]
            if rh < th or rw < tw:
                continue
            res = cv2.matchTemplate(region, tmpl, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= self.threshold)
            for x, y in zip(xs, ys):
                dets.append(Detection(name, int(x) + ox, int(y) + oy, tw, th, float(res[y, x])))

        return self._nms(dets, self.iou_thresh)

    # ---- 內部 ----
    def _crop(self, frame):
        if not self.roi:
            return frame, 0, 0
        l, t, w, h = (int(v) for v in self.roi)
        l, t = max(0, l), max(0, t)
        return frame[t:t + h, l:l + w], l, t

    @staticmethod
    def _iou(a, b):
        ax2, ay2 = a.x + a.w, a.y + a.h
        bx2, by2 = b.x + b.w, b.y + b.h
        ix1, iy1 = max(a.x, b.x), max(a.y, b.y)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        if inter == 0:
            return 0.0
        union = a.w * a.h + b.w * b.h - inter
        return inter / float(union)

    @classmethod
    def _nms(cls, dets, iou_thresh):
        """非極大值抑制：分數高者優先，與已保留框 IoU 超過門檻者剔除。"""
        kept: List[Detection] = []
        for d in sorted(dets, key=lambda m: m.score, reverse=True):
            if all(cls._iou(d, k) < iou_thresh for k in kept):
                kept.append(d)
        return kept
