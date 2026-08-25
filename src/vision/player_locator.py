"""角色螢幕定位——用頭盔模板追蹤自己的角色。

為什麼需要：同層判定（怪在不在打得到的那一層）和傷害歸屬（那個數字是我打的
還是旁邊玩家打的）都要知道角色在畫面哪裡。實機量測顯示角色**不在畫面中央**：
在戰火之地，鏡頭被地圖底部夾住，角色比中央低約 230 個基準像素；水平方向也
偏離最多 146px。把中央當成角色位置，同層過濾就整個算錯——那正是「朝空氣
攻擊」的根源。

被實測否決的做法：
  - 名牌模板（原 assets/templates/player/nametag.png）：0/27 幀，score 恆為 0。
    兩個獨立原因——那個檔案根本是一張叢林草叢的截圖（裡面沒有文字），而且
    名牌畫在腳底下方 y>=680，正好被聊天面板整片蓋住。
  - 視差（背景會捲、角色不會）：這張圖鏡頭**完全不捲動**（|dx|<=0.48px），
    前提剛好相反——背景是靜的，角色才是唯一在動的大物體。
  - 衣服顏色色塊：0/27，最大的匹配色塊永遠是開著的 UI 視窗。

頭盔模板實測 25/27 幀鎖定、誤差中位數 0.61px、2.9ms/幀，且面向判斷 27/27 正確
（哪一個鏡像贏就是朝哪邊）。
"""

import os

try:
    import cv2
    import numpy as np
    _CV_AVAILABLE = True
except Exception:                                    # pragma: no cover
    cv2 = None
    np = None
    _CV_AVAILABLE = False


class PlayerLocator:
    """用頭盔模板追蹤角色，回傳腳底座標與面向（基準尺度）。

    三段搜尋（成本由低到高，實測值見各段註解）：
      A 每幀：在上次位置 ±WINDOW 內比對灰階，約 2.9 ms。
        角色每幀最多移動 52px，±70 有 34% 餘裕。
      B A 失敗：整條同高度帶用彩色比對，約 51 ms。彩色把誤匹配天花板從
        0.598 壓到 0.570，代價三倍，但這條路很少走。
      C B 失敗：沿用上次位置最多 COAST_MAX 幀（實測誤差 21~25px，相對
        平台間距 244px 可忽略），再失敗才全幀重掃。

    模板是「這個角色目前戴的帽子」。換裝備會失效——available 會轉 False 並
    印出提示，用 tools/grab_helmet.py 重截一張即可。
    """

    WINDOW = 70                 # 追蹤窗半徑（基準 px）
    STRIP_H = 40                # 同高度帶搜尋的上下範圍
    ACCEPT = 0.65               # A/B 段的接受門檻
    ACCEPT_COLD = 0.70          # 全幀重掃的門檻（要更嚴，避免亂鎖）
    COAST_MAX = 4               # 最多沿用上次位置幾幀
    FEET_DX_LEFT = 28
    FEET_DX_RIGHT = 32
    FEET_DY = 69

    def __init__(self, template_dir="assets/templates/player",
                 template_scale=0.5, root=""):
        self.pats = []          # [(圖, 是否鏡像)]
        self.pos = None         # 最近一次的帽子左上角（基準座標）
        self.facing = None
        self.score = 0.0
        self.stale = 0          # 已經沿用上次位置幾幀
        self._warned = False
        if not _CV_AVAILABLE:
            return
        d = os.path.join(root, template_dir) if root else template_dir
        if not os.path.isdir(d):
            return
        for fn in sorted(os.listdir(d)):
            if not fn.lower().startswith("helmet"):
                continue
            img = cv2.imread(os.path.join(d, fn), cv2.IMREAD_COLOR)
            if img is None:
                continue
            if template_scale and abs(template_scale - 1.0) > 1e-6:
                img = cv2.resize(img, None, fx=template_scale, fy=template_scale,
                                 interpolation=cv2.INTER_AREA)
            self.pats.append((img, False))
            self.pats.append((cv2.flip(img, 1), True))

    @property
    def available(self):
        return bool(self.pats)

    def _best(self, region, color):
        """在 region 內比對所有樣式，回傳 (分數, x, y, 是否鏡像)。"""
        best = (-1.0, 0, 0, False)
        src = region if color else cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        for img, mirrored in self.pats:
            t = img if color else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            if region.shape[0] < t.shape[0] or region.shape[1] < t.shape[1]:
                continue
            r = cv2.matchTemplate(src, t, cv2.TM_CCOEFF_NORMED)
            _mn, mx, _ml, ml = cv2.minMaxLoc(r)
            if mx > best[0]:
                best = (float(mx), int(ml[0]), int(ml[1]), mirrored)
        return best

    def find(self, frame):
        """回傳 (腳底x, 腳底y) 或 None。frame 必須是基準尺度畫面。"""
        if not self.available or frame is None:
            return None
        h, w = frame.shape[:2]
        th, tw = self.pats[0][0].shape[:2]

        # A 段：上次位置附近
        if self.pos is not None:
            x0 = max(0, self.pos[0] - self.WINDOW)
            y0 = max(0, self.pos[1] - self.WINDOW)
            x1 = min(w, self.pos[0] + self.WINDOW + tw)
            y1 = min(h, self.pos[1] + self.WINDOW + th)
            s, rx, ry, mir = self._best(frame[y0:y1, x0:x1], color=False)
            if s >= self.ACCEPT:
                return self._lock(s, x0 + rx, y0 + ry, mir)

            # B 段：同高度帶整條掃（彩色）
            y0 = max(0, self.pos[1] - self.STRIP_H)
            y1 = min(h, self.pos[1] + self.STRIP_H + th)
            s, rx, ry, mir = self._best(frame[y0:y1], color=True)
            if s >= self.ACCEPT:
                return self._lock(s, rx, y0 + ry, mir)

            # C 段：沿用上次位置
            if self.stale < self.COAST_MAX:
                self.stale += 1
                return self._feet(self.pos, self.facing)

        # 冷啟動／沿用次數用完 → 全幀重掃（門檻更嚴）
        s, rx, ry, mir = self._best(frame, color=True)
        if s >= self.ACCEPT_COLD:
            return self._lock(s, rx, ry, mir)
        self.score = s
        self.pos = None
        return None

    def _lock(self, s, x, y, mirrored):
        self.score, self.pos, self.stale = s, (int(x), int(y)), 0
        # 模板統一朝左；鏡像版本贏＝角色朝右
        self.facing = "right" if mirrored else "left"
        return self._feet(self.pos, self.facing)

    def _feet(self, pos, facing):
        dx = self.FEET_DX_RIGHT if facing == "right" else self.FEET_DX_LEFT
        return (pos[0] + dx, pos[1] + self.FEET_DY)
