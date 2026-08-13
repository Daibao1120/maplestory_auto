"""模板匹配：在畫面中找出怪物、NPC、UI 等已知圖樣的位置。

經典版為高解析重繪，模板必須以「實際遊戲截圖」重建
（見 tools/capture_template.py）。cv2/numpy 採延遲載入。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _CV_AVAILABLE = True
except ImportError:
    cv2 = None  # type: ignore
    np = None  # type: ignore
    _CV_AVAILABLE = False


@dataclass
class Match:
    """一次匹配結果。"""
    name: str
    x: int
    y: int
    w: int
    h: int
    score: float

    @property
    def center(self):
        """回傳中心點 (cx, cy)。"""
        return (self.x + self.w // 2, self.y + self.h // 2)


class TemplateMatcher:
    """模板匹配器。

    用法：
        >>> tm = TemplateMatcher(threshold=0.8)
        >>> tm.load_directory("assets/templates")
        >>> matches = tm.match(frame, "slime")   # -> List[Match]
    """

    def __init__(self, threshold=0.8):
        self.threshold = threshold
        self._templates: Dict[str, "np.ndarray"] = {}

    def _require_cv(self):
        if not _CV_AVAILABLE:
            raise RuntimeError("尚未安裝 opencv-python / numpy。請執行： pip install opencv-python numpy")

    def load_template(self, name, path):
        """載入單一模板圖片並以 name 命名。"""
        self._require_cv()
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"無法讀取模板圖片：{path}")
        self._templates[name] = img
        return self

    def load_directory(self, directory):
        """載入資料夾內所有圖片作為模板（檔名去副檔名即為 name）。"""
        self._require_cv()
        if not os.path.isdir(directory):
            raise FileNotFoundError(f"模板資料夾不存在：{directory}")
        for fname in os.listdir(directory):
            stem, ext = os.path.splitext(fname)
            if ext.lower() in (".png", ".jpg", ".jpeg", ".bmp"):
                self.load_template(stem, os.path.join(directory, fname))
        return self

    def match(self, frame, template_name, threshold=None):
        """在 frame 中尋找指定模板，回傳所有超過門檻的 Match（已做簡易去重）。"""
        self._require_cv()
        if template_name not in self._templates:
            raise KeyError(f"尚未載入模板：{template_name}")
        thr = self.threshold if threshold is None else threshold
        template = self._templates[template_name]
        h, w = template.shape[:2]

        result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= thr)
        raw = [Match(template_name, int(x), int(y), w, h, float(result[y, x]))
               for x, y in zip(xs, ys)]
        return self._dedupe(raw)

    def match_all(self, frame, threshold=None):
        """對所有已載入模板各做一次 match，回傳 {name: [Match, ...]}。"""
        return {name: self.match(frame, name, threshold) for name in self._templates}

    @staticmethod
    def _dedupe(matches, min_dist=8):
        """簡易非極大值抑制：位置過近者只保留分數最高的。"""
        kept: List[Match] = []
        for m in sorted(matches, key=lambda x: x.score, reverse=True):
            if all(abs(m.x - k.x) > min_dist or abs(m.y - k.y) > min_dist for k in kept):
                kept.append(m)
        return kept

    @property
    def template_names(self):
        return list(self._templates.keys())
