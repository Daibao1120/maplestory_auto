"""判斷邏輯（不碰畫面、不碰滑鼠），單獨測試用。

每次檢查把 HP/MP 比例與快捷欄數量丟進 tick()，它回傳要做的事：
  None / "refill" / "emergency_refill" / "dead" / "alarm"
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class CountTracker:
    """把數字辨識結果過濾成「可信任的數量」。

    - 要連續兩次讀到相同（或非常接近）的數字才採用
    - 沒補貨卻變多、或一次掉太多，都視為辨識錯誤丟掉
    """
    max_use_per_sec: float = 3.0
    jump_buffer: int = 60
    trusted: int | None = None
    trusted_at: float = 0.0
    _pending: int | None = None
    _fail_streak: int = 0
    history: deque = field(default_factory=lambda: deque(maxlen=2000))
    expect_increase_until: float = 0.0   # 補貨後一段時間內允許數量變多
    _up_value: int | None = None
    _up_count: int = 0
    RECOVER = 15                         # 沒補貨卻變多：連續讀到這麼多次一樣的數字（約 30 秒）才採用

    def update(self, value: int | None, now: float) -> int | None:
        if value is None:
            self._fail_streak += 1
            return self.trusted
        if self._pending is None or abs(value - self._pending) > 2:
            self._pending = value
            return self.trusted
        # 連續兩次一致
        if self.trusted is not None:
            dt = max(1.0, now - self.trusted_at)
            max_drop = self.max_use_per_sec * dt + self.jump_buffer
            if value > self.trusted + 2 and now > self.expect_increase_until:
                # 你自己去買了（不是程式補的）→ 數字變多。連續 RECOVER 次都讀到同樣的新數字才相信
                if self._up_value is not None and abs(value - self._up_value) <= 2:
                    self._up_count += 1
                else:
                    self._up_value, self._up_count = value, 1
                if self._up_count < self.RECOVER:
                    self._fail_streak += 1
                    return self.trusted
                return self._accept(value, now)
            if self.trusted - value > max_drop:
                self._fail_streak += 1
                return self.trusted
            # 位數突然改變（例如 4778 讀成 778）通常是漏讀，除非剛好跨過 1000/100 這種邊界
            if len(str(value)) != len(str(self.trusted)) and now > self.expect_increase_until:
                boundary = 10 ** (len(str(max(value, self.trusted))) - 1)
                tol = max(30, boundary * 0.1)
                # 兩邊都要在邊界附近（1005 → 999 可以；1020 → 102 是最後一位被擋住，不行）
                if abs(self.trusted - boundary) > tol or abs(value - boundary) > tol:
                    self._fail_streak += 1
                    return self.trusted
        return self._accept(value, now)

    def _accept(self, value: int, now: float) -> int:
        self.trusted, self.trusted_at = value, now
        self._fail_streak = 0
        self._up_value, self._up_count = None, 0
        self.history.append((now, value))
        return self.trusted

    def stale(self, now: float, max_age: float) -> bool:
        return self.trusted is None or now - self.trusted_at > max_age

    def rate_per_hour(self, now: float, window_sec: float = 1800) -> float | None:
        """最近一段時間的平均消耗速度（個/小時），補貨造成的增加不算。"""
        pts = [(t, v) for t, v in self.history if now - t <= window_sec]
        if len(pts) < 2:
            return None
        used = 0
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if v1 < v0:
                used += v0 - v1
        span = pts[-1][0] - pts[0][0]
        if span < 120:
            return None
        return used / span * 3600


@dataclass
class Guardian:
    cfg: dict
    hp_low_streak: int = 0
    mp_low_streak: int = 0
    dead_streak: int = 0
    last_refill: float = 0.0
    last_emergency: float = 0.0
    dead: bool = False

    def tick(self, now: float, hp: float, mp: float,
             counts: dict[str, int | None], ocr_ok: bool,
             thresholds: dict[str, int]) -> tuple[str | None, str]:
        s = self.cfg
        # ---- 死亡偵測 ----
        if hp == hp and hp <= s["dead_hp"]:      # hp==hp 用來排除 NaN
            self.dead_streak += 1
        else:
            self.dead_streak = 0
        if self.dead_streak >= s["dead_checks"]:
            self.dead = True
            return "dead", f"HP 歸零已持續 {self.dead_streak} 次檢查，角色可能死亡"

        # ---- 緊急：血或魔一直很低 ----
        self.hp_low_streak = self.hp_low_streak + 1 if (hp == hp and hp < s["hp_emergency"]) else 0
        self.mp_low_streak = self.mp_low_streak + 1 if (mp == mp and mp < s["mp_emergency"]) else 0
        emergency = None
        if self.hp_low_streak >= s["hp_emergency_checks"]:
            emergency = f"HP 只剩 {hp:.0%}，已持續 {self.hp_low_streak} 次"
        elif self.mp_low_streak >= s["mp_emergency_checks"]:
            emergency = f"MP 只剩 {mp:.0%}，已持續 {self.mp_low_streak} 次"
        # 數量直接讀到 0 也算緊急
        for k, v in counts.items():
            if v == 0:
                emergency = emergency or f"{k} 補品數量為 0"
        gap_ok = now - self.last_refill >= s["min_refill_gap_sec"]
        if emergency:
            # 數字讀得到、而且補品都還很多 → 不是缺貨，是被打太痛，開商店也沒用
            if ocr_ok and counts and all(v is not None and v >= thresholds.get(k, 0) for k, v in counts.items()):
                return "alarm", f"{emergency}，但補品還很充足，可能怪物太強或自動喝水設定有問題"
            if gap_ok:
                return "emergency_refill", emergency
            # 剛補過貨還是危險 → 補貨沒有解決問題，要叫人
            if now - self.last_refill >= s["emergency_recheck_sec"]:
                return "alarm", f"補貨後仍然危險：{emergency}"
            return None, ""

        if not gap_ok:
            return None, ""
        # ---- 一般：數量低於門檻 ----
        if ocr_ok:
            for k, v in counts.items():
                if v is not None and v < thresholds.get(k, 0):
                    return "refill", f"{k} 剩 {v} 個，低於 {thresholds[k]}"
        # ---- 定時補貨 ----
        # 讀不到數量時靠這個；讀得到時也照樣定時去一次（順便把撿到的雜物賣掉，背包才不會滿）
        hours = s["timer_refill_hours"]
        if now - self.last_refill >= hours * 3600:
            if ocr_ok:
                return "refill", f"定時補貨（每 {hours} 小時，順便賣掉撿到的東西）"
            return "refill", f"數字辨識不可用，定時補貨（每 {hours} 小時）"
        return None, ""
