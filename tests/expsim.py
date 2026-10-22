"""用真實截圖做的「快升等自動停止」模擬遊戲（給 autostop 的單元測試用）。

  * 畫面：你的真實截圖（公司電腦 1280x720 的方塊仔、家裡 2764x1555 的主角），
    EXP 條用截圖裡真正的填色重新畫
  * 自動狩獵開著 → EXP 依速度一直漲，到 100% 升級歸零；按左上角「自動狩獵」按鈕 → 開/關
  * 可以模擬：滑鼠停在 EXP 條上、別的視窗蓋住、畫面變黑、第一下沒按到
  * 所有時間都是虛擬時間
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from autopot.expbar import box_rows, find_autohunt_button, find_bars, ratio_to_fill

D = Path(__file__).resolve().parent / "samples" / "exp"
BASES = {
    "office": "alt_lv88_4.57pct_nocursor.png",     # 公司電腦（遊戲畫面 1278x720）
    "home": "main_lv191_36.61pct.png",             # 家裡（遊戲畫面 2764x1555）
}


class Clock:
    def __init__(self):
        self.t = 50_000.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, float(s))


class ExpGame:
    def __init__(self, base="office", ratio=0.90, pct_per_min=4.0, hunting=True, clock=None,
                 events=(), click_drop=0, seed=0, coast=8.0):
        self.img0 = cv2.imread(str(D / BASES[base]))
        self.clock = clock or Clock()
        self.y, bars = find_bars(self.img0)
        self.x0, self.x1 = bars[2]
        self.r0, self.r1 = box_rows(self.img0, self.y, self.x0, self.x1)   # 框內部的上下範圍
        self.fill = self.img0[self.r0:self.r1, self.x0 + 2:self.x0 + 3].copy()
        self.empty = self.img0[self.r0:self.r1, self.x1 - 4:self.x1 - 3].copy()
        self.btn = find_autohunt_button(self.img0)
        self.ratio = ratio
        self.rate = pct_per_min / 100 / 60          # 每秒漲多少
        self.hunting = hunting
        self.level = 0
        self.events = list(events)                  # [(開始, 結束, 種類)]，時間是相對於開始的秒數
        self.t0 = self.clock.t
        self._last = self.clock.t
        self.click_drop = click_drop                # 前幾下點擊沒反應
        self.clicks = []
        self.topmost = []
        self.rng = np.random.default_rng(seed)
        self.coast = coast             # 關掉自動狩獵後，還會把正在打的怪打完（EXP 再漲幾秒）
        self._stopped_at = None

    # ---- 遊戲狀態 ----
    def _advance(self):
        dt = self.clock.t - self._last
        self._last = self.clock.t
        coasting = (not self.hunting and self._stopped_at is not None
                    and self.clock.t - self._stopped_at < self.coast)
        if self.hunting or coasting:
            self.ratio += self.rate * dt
            while self.ratio >= 1.0:
                self.ratio -= 1.0
                self.level += 1

    def _active(self, kind):
        rel = self.clock.t - self.t0
        return any(a <= rel < b and k == kind for a, b, k in self.events)

    def capture(self):
        self._advance()
        if self._active("black"):
            return np.zeros_like(self.img0)
        f = self.img0.copy()
        w = self.x1 - self.x0
        y0, y1 = self.r0, self.r1
        f[y0:y1, self.x0:self.x1] = np.repeat(self.empty, w, axis=1)
        n = ratio_to_fill(self.ratio, w)
        if n:
            f[y0:y1, self.x0:self.x0 + n] = np.repeat(self.fill, n, axis=1)
        if self._active("cursor"):          # 白色手套游標停在 EXP 條上
            cx = self.x0 + int(w * 0.6)
            cv2.rectangle(f, (cx, self.y - 14), (cx + 18, self.y + 14), (255, 255, 255), -1)
            cv2.rectangle(f, (cx, self.y - 14), (cx + 18, self.y + 14), (0, 0, 0), 1)
        if self._active("covered"):         # 深色視窗蓋住遊戲下半部
            H, W = f.shape[:2]
            f[H // 3:, : W * 2 // 3] = (32, 30, 30)
        return f

    # ---- 輸入（跟 GameWindow 一樣的介面） ----
    def click(self, x, y, jitter=0):
        self._advance()
        self.clicks.append((round(self.clock.t - self.t0), int(x), int(y), round(self.ratio, 4)))
        if self.click_drop > 0:
            self.click_drop -= 1
            return
        b = self.btn
        if b.x <= x < b.x + b.w and b.y <= y < b.y + b.h:
            self.hunting = not self.hunting
            self._stopped_at = None if self.hunting else self.clock.t

    def activate(self):
        pass

    def foreground(self):
        return 1

    def restore_foreground(self, h):
        pass

    def is_minimized(self):
        return False

    def set_topmost(self, on=True):
        self.topmost.append(on)
