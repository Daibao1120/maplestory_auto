"""免模板的怪物活動偵測：怪會動，背景不會。

為什麼需要：模板匹配要先幫每種怪截圖，換地圖就得重新採集。所有怪物都有一個
共同特徵——牠們會移動/有動畫。

作法（實測修正過）：一開始用三畫格差分，但它的數學意義是「只出現在中間幀的
像素」——正好會抓到技能特效閃光，而慢速移動的大型怪因為前後幀重疊太多反而
抓不到（測試當場證實：移動的怪 blobs=0、單幀閃光 blobs=1，完全相反）。
改用背景模型（MOG2）取前景，再取「連續兩幀前景的交集」：
    - 持續移動的怪：兩幀都在前景 → 交集留下 ✔
    - 只閃一幀的特效：只有一幀在前景 → 交集為空 ✘

用途：
    activity  這一帶「有沒有怪」的強度——判斷這個點還值不值得掛。
    left/right 相對畫面中央的活動分佈，當作面向的弱提示（EXP 回饋才是主要依據）。

排除：上下 UI 帶、畫面中央（自己的角色與寵物）。鏡頭移動時整片都會變成前景，
以比例門檻偵測並跳過該幀。
"""
from __future__ import annotations

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _CV_AVAILABLE = True
except ImportError:
    cv2 = None
    np = None
    _CV_AVAILABLE = False


def split_activity(mask, center_x, dead_zone):
    """把動態遮罩依中央分成左右兩側的像素量（純函式，可測試）。

    dead_zone: 中央排除半寬（像素）——自己的角色與寵物都在那裡。
    """
    w = mask.shape[1]
    lo = max(0, int(center_x - dead_zone))
    hi = min(w, int(center_x + dead_zone))
    left = int(mask[:, :lo].sum())
    right = int(mask[:, hi:].sum())
    return left, right


class MotionDetector:
    """三畫格差分的活動偵測器。

    參數：
        diff_thresh   單一像素被視為「有變化」的亮度差門檻
        pan_frac      整體變動比例超過此值 = 鏡頭在移動 → 這幀不用
        top/bottom    上下要排除的畫面比例（UI）
        dead_zone_frac 中央排除半寬（畫面寬比例）
    """

    def __init__(self, diff_thresh=25, pan_frac=0.35, top=0.20, bottom=0.86,
                 dead_zone_frac=0.07, min_blob=250, history=40,
                 dilate_x=17, dilate_y=9):
        self.diff_thresh = int(diff_thresh)
        self.pan_frac = float(pan_frac)
        self.top = float(top)
        self.bottom = float(bottom)
        self.dead_zone_frac = float(dead_zone_frac)
        self.min_blob = int(min_blob)
        self.dilate_x = int(dilate_x)   # 容許的每幀水平位移（怪多為水平移動）
        self.dilate_y = int(dilate_y)
        self._bg = None
        self._history = int(history)
        self._prev_fg = None
        self._seen = 0
        self.last = {"activity": 0.0, "left": 0, "right": 0, "blobs": 0,
                     "panning": False, "ready": False}

    def _ensure_bg(self):
        if self._bg is None:
            self._bg = cv2.createBackgroundSubtractorMOG2(
                history=self._history, varThreshold=float(self.diff_thresh),
                detectShadows=False)
        return self._bg

    def reset(self):
        """重建背景模型（換地圖/鏡頭大幅移動後呼叫）。"""
        self._bg = None
        self._prev_fg = None
        self._seen = 0

    def update(self, frame):
        """餵入一幀，回傳最新的活動統計 dict。"""
        if not _CV_AVAILABLE or frame is None:
            return self.last
        h, w = frame.shape[:2]
        y0, y1 = int(h * self.top), int(h * self.bottom)
        band = frame[y0:y1]
        fg = self._ensure_bg().apply(band)
        fg = (fg > 200).astype(np.uint8)          # 去掉陰影/不確定值
        self._seen += 1
        if float(fg.mean()) > self.pan_frac:      # 整片都在動 = 鏡頭捲動
            self._prev_fg = None
            self.last = {"activity": 0.0, "left": 0, "right": 0, "blobs": 0,
                         "panning": True, "ready": False}
            return self.last
        prev, self._prev_fg = self._prev_fg, fg
        if prev is None or prev.shape != fg.shape or self._seen < 3:
            self.last = {**self.last, "ready": False, "panning": False}
            return self.last
        # 連續兩幀都在前景 = 持續移動的東西（濾掉只閃一幀的特效）。
        # 先各自膨脹再取交集：背景模型會把「前後幀重疊的部分」很快學成背景，
        # 只剩下移動前緣，而前後兩幀的前緣並不重疊——不膨脹的話交集恆為空
        # （實測：移動物體 blobs=0）。膨脹量約等於可容許的每幀位移。
        k = np.ones((self.dilate_y, self.dilate_x), np.uint8)
        moving = cv2.morphologyEx((cv2.dilate(prev, k) & cv2.dilate(fg, k)),
                                  cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        num, _lab, stats, cents = cv2.connectedComponentsWithStats(moving, connectivity=8)
        cx0, dead = w // 2, int(w * self.dead_zone_frac)
        left = right = blobs = 0
        for i in range(1, num):
            if int(stats[i, cv2.CC_STAT_AREA]) < self.min_blob:
                continue
            blobs += 1
            x = float(cents[i][0])
            if x < cx0 - dead:
                left += 1
            elif x > cx0 + dead:
                right += 1
        # left/right 是「夠大的移動物體數量」而非像素總量——實機上其他玩家的
        # 大型技能特效會灌爆像素數（單側 20 萬+）而讓判斷失真。
        self.last = {"activity": float(moving.mean()), "left": left, "right": right,
                     "blobs": blobs, "panning": False, "ready": True}
        return self.last

    def hint_side(self, ratio=1.4, min_blobs=1):
        """依左右「移動物體數量」給面向提示；差距不明顯回 None（交給 EXP 回饋）。"""
        d = self.last
        if not d.get("ready"):
            return None
        l, r = d["left"], d["right"]
        if l + r < min_blobs:
            return None          # 一隻怪也算有效訊號（min_blobs=1）
        if l > r * ratio:
            return "left"
        if r > l * ratio:
            return "right"
        return None
