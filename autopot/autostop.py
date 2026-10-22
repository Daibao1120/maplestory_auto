"""快升等時自動按「自動狩獵」把它關掉。

判斷方式（不讀遊戲記憶體，只看畫面）：
  * 讀 EXP 條填滿的比例（框被擋住、畫面變黑、滑鼠停在條上時，這次就不算）。
  * 只有親眼看到 EXP 從「門檻以下」漲到「門檻以上」（而且連續 2 次都在門檻以上）才按
    —— 這代表角色正在打怪、自動狩獵是開著的，按一下就是關掉，不會把原本關著的打開。
  * 按完之後繼續看：EXP 還在漲（多漲了 1% 以上）→ 代表沒按到，再按一次（只補按一次）。
  * 按完後如果還是升級了（EXP 歸零），會警告你。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .expbar import exp_ratio, fill_to_ratio, find_autohunt_button, find_bars

log = logging.getLogger("autopot.autostop")

CONFIRM = 2          # 連續幾次在門檻以上才按（避免一次讀錯就按）
STILL_GROWING = 0.01  # 按完後又漲了這麼多 → 判斷沒停下來
MARGIN = 60          # EXP 漲很快時，至少在預估升級前這麼多秒就按（留時間補按）
SETTLE = 15          # 按完停止後，角色會把正在打的怪打完、撿完東西，這段時間 EXP 還會漲一點，不算


@dataclass
class AutoStop:
    at: float = 0.98                 # EXP 到幾 % 就停（0~1）
    clicked_at: float | None = None
    click_ratio: float = 0.0
    retried: bool = False
    leveled_after_click: bool = False
    seen_below: bool = False
    high_streak: int = 0
    grow_streak: int = 0
    base_ratio: float | None = None
    base_t: float = 0.0
    _bars: tuple | None = None
    _btn: object = None
    _fails: int = 0
    history: list = field(default_factory=list)       # [(時間, 比例)]

    # ---------- 讀 EXP ----------
    def read(self, img):
        if self._bars is None:
            self._bars = find_bars(img)
        r = exp_ratio(img, self._bars) if self._bars else None
        if r is None:
            self._fails += 1
            if self._fails >= 5:          # 連續讀不到 → 重新找 EXP 條（視窗大小可能變了）
                self._bars, self._fails = None, 0
        else:
            self._fails = 0
        return r

    def rate_per_min(self, now: float, window: float = 600.0, min_span: float = 60) -> float | None:
        pts = [(t, r) for t, r in self.history if now - t <= window]
        if len(pts) < 2 or pts[-1][0] - pts[0][0] < min_span:
            return None
        (t0, r0), (t1, r1) = pts[0], pts[-1]
        if r1 < r0:
            return None
        return (r1 - r0) / ((t1 - t0) / 60)

    def threshold(self, now: float | None = None) -> float:
        """實際用的門檻。
          * EXP 條快滿的最後一小段看起來都是「全滿」（填色左右藏在框下面），門檻設太高會永遠讀不到
            → 最多用到「再 1px 就全滿」的位置。
          * EXP 漲得很快時，提早一點按，保證離升級至少還有 MARGIN 秒
            （萬一第一下沒按到，還來得及發現並補按）。"""
        if self._bars is None:
            return self.at
        _, (_, _, (x0, x1)) = self._bars
        w = x1 - x0
        thr = min(self.at, fill_to_ratio(w - 1, w))
        if now is not None:
            rate = self.rate_per_min(now, window=120, min_span=10)
            if rate:
                thr = min(thr, fill_to_ratio(w, w) - rate / 60 * MARGIN)
        return thr

    # ---------- 判斷 ----------
    def step(self, img, now: float) -> str:
        """看一張畫面，回傳要做的事：'click' / 'retry_click' / 'wait' / 'leveled' / 'unknown'。"""
        r = self.read(img)
        if r is None:
            return "unknown"
        prev = self.history[-1][1] if self.history else None
        self.history.append((now, r))
        self.history = [(t, x) for t, x in self.history if now - t <= 1800]
        if prev is not None and prev > 0.8 and r < 0.2:
            self.seen_below = False
            self.high_streak = 0
            if self.clicked_at is not None:
                self.leveled_after_click = True
            return "leveled"
        if self.clicked_at is None:
            if r < self.threshold(now):
                self.seen_below = True
                self.high_streak = 0
                return "wait"
            self.high_streak += 1
            if self.seen_below and self.high_streak >= CONFIRM:
                self.clicked_at, self.click_ratio = now, r
                return "click"
            return "wait"
        # 已經按過了：EXP 還在漲 → 沒停下來，補按一次。
        # 實測（10/20 12:56）：按下停止後角色會把正在打的怪打完、撿東西，EXP 還會再漲一點點，
        # 之前馬上就判斷「沒停」又按一次 → 反而把自動狩獵打開了。
        # 所以按完先等 SETTLE 秒，用那時的讀數當基準，之後「再漲 1px 以上、連續 2 次、而且離基準至少 10 秒」才補按。
        if now - self.clicked_at < SETTLE:
            return "wait"
        if self.base_ratio is None:
            self.base_ratio, self.base_t = r, now
            return "wait"
        if self.retried:
            return "wait"
        if now - self.base_t >= 10 and r >= self.base_ratio + min(STILL_GROWING, self._px_ratio() * 0.9):
            self.grow_streak += 1
            if self.grow_streak >= CONFIRM:
                self.retried = True
                self.clicked_at, self.click_ratio = now, r
                self.base_ratio = None
                return "retry_click"
        else:
            self.grow_streak = 0
        return "wait"

    def _px_ratio(self) -> float:
        """EXP 條 1px 相當於多少比例。"""
        if self._bars is None:
            return 0.01
        _, (_, _, (x0, x1)) = self._bars
        w = x1 - x0
        return fill_to_ratio(w, w) - fill_to_ratio(w - 1, w)

    def stopped_for(self, now: float) -> float:
        """按下停止（並等 SETTLE 秒）後，EXP 已經多久沒有再漲（秒）。還不能判斷時回傳 0。"""
        if self.clicked_at is None or self.base_ratio is None:
            return 0.0
        last_growth = self.base_t
        for t, r in self.history:
            if t > self.base_t and r > self.base_ratio + self._px_ratio() * 0.5:
                last_growth = t
        return now - last_growth

    def button(self, img, fresh: bool = False):
        if self._btn is None or fresh:
            b = find_autohunt_button(img)
            if b is not None:
                self._btn = b
        return self._btn
