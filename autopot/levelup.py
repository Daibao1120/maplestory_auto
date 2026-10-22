"""預估下一次升等、和升到 200 等的時間（看 EXP 條漲的速度）。

* 下一次升等：只看 EXP 條（%）漲多快，不需要知道經驗值表。
* 到 200 等：要知道每一級要多少經驗 → 用經典版的經驗值表（EXP_TABLE）。
  196 級 = 1,713,628,833 跟你遊戲裡顯示的一樣。
* 現在幾等：程式讀不到等級數字，所以第一次要在 config.yaml 寫 level，
  之後每次看到 EXP 條從快滿掉回 0，就自動 +1，存在 data/level.json。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .expbar import exp_ratio, find_bars

log = logging.getLogger("autopot.levelup")

# 每一級升到下一級需要的經驗（經典版經驗值表，來源：RaGEZONE「EXP table (Level 1-254)」；
# 196 級 1,713,628,833 跟你遊戲裡顯示的一樣）。只列快到 200 等會用到的幾級。
EXP_TABLE = {
    194: 1540197871, 195: 1624600714, 196: 1713628833, 197: 1807535693,
    198: 1906558648, 199: 2011069705,
}
MAX_LEVEL = 200
WINDOW = 60 * 60        # 用最近 60 分鐘算速度（EXP 條 1px ≈ 0.3%，看越久越準：10 分鐘約 ±10%、60 分鐘約 ±2%）
MIN_SPAN = 5 * 60       # 至少看 5 分鐘才開始預估（EXP 條 1px 要好幾十秒才漲一次）


def fmt_dur(sec: float) -> str:
    m = int(round(sec / 60))
    if m < 60:
        return f"{m} 分鐘"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h} 小時 {m} 分"
    d, h = divmod(h, 24)
    return f"{d} 天 {h} 小時"


def fmt_at(ts: float, now: float) -> str:
    lt, ln = time.localtime(ts), time.localtime(now)
    if (lt.tm_year, lt.tm_yday) == (ln.tm_year, ln.tm_yday):
        return time.strftime("%H:%M", lt)
    return time.strftime("%m/%d %H:%M", lt)


@dataclass
class LevelForecast:
    level: int | None = None
    save_path: Path | None = None
    history: list = field(default_factory=list)       # [(時間, 累積經驗)]
    last_ratio: float | None = None
    _bars: tuple | None = None
    _fails: int = 0

    # ---------- 等級存檔 ----------
    @classmethod
    def load(cls, config_level, save_path: Path) -> "LevelForecast":
        """config.yaml 的 level 是你自己填的；data/level.json 是程式偵測到升等後記下來的。
        如果你改過 config.yaml 的 level（跟上次存的不一樣）→ 以你填的為準。"""
        lv = int(config_level) if config_level else None
        try:
            d = json.loads(save_path.read_text(encoding="utf-8"))
            if lv is None or d.get("config_level") == lv:
                lv = int(d["level"])
        except Exception:
            pass
        f = cls(level=lv, save_path=save_path)
        f._config_level = int(config_level) if config_level else None
        return f

    def _save(self) -> None:
        if not self.save_path or self.level is None:
            return
        try:
            self.save_path.write_text(json.dumps({"level": self.level, "config_level": self._config_level,
                                                  "updated": time.strftime("%Y-%m-%d %H:%M:%S")}),
                                      encoding="utf-8")
        except Exception:
            pass

    _config_level: int | None = None
    # 畫面上讀到的等級（左下角橘色方塊）：讀得到就以畫面為準
    reader: object = None
    screen_level: int | None = None
    screen_at: float = 0.0
    _lv_pending: int | None = None
    _lv_count: int = 0
    _lv_read_at: float = -1e9
    _screen_bumped_at: float = -1e9
    LV_EVERY = 10.0           # 每幾秒讀一次等級
    LV_CONFIRM = 3            # 連續幾次一樣才採用

    def see_level(self, lv: int | None, now: float) -> None:
        """畫面上讀到等級 lv（None = 讀不到）。連續 LV_CONFIRM 次一樣才採用。"""
        if lv is None:
            self._lv_pending, self._lv_count = None, 0
            return
        if lv == self._lv_pending:
            self._lv_count += 1
        else:
            self._lv_pending, self._lv_count = lv, 1
        if self._lv_count < self.LV_CONFIRM:
            return
        self.screen_level, self.screen_at = lv, now
        if lv == self.level:
            return
        old = self.level
        if old is not None and lv == old + 1 and (self.last_ratio or 0) > 0.8:
            # 畫面上的等級比 EXP 條先變 → 等一下 EXP 條歸零時不要再 +1
            self._screen_bumped_at = now
            self.level = lv
            log.info("偵測到升等！現在 %d 等（畫面上的等級）", lv)
        else:
            if old is None:
                log.info("畫面上的等級是 %d", lv)
            else:
                log.info("畫面上的等級是 %d（設定是 %d），改用畫面上的", lv, old)
            self.level = lv
            self.history = []          # 單位（每一級要多少經驗）變了 → 重新累積
        self._save()

    # ---------- 讀 EXP ----------
    def read(self, img):
        if self._bars is None:
            self._bars = find_bars(img)
        r = exp_ratio(img, self._bars) if self._bars else None
        if r is None:
            self._fails += 1
            if self._fails >= 5:
                self._bars, self._fails = None, 0
        else:
            self._fails = 0
        return r

    def need(self, lv: int | None) -> int | None:
        return EXP_TABLE.get(lv) if lv is not None else None

    def _cum(self, r: float) -> float:
        """換成「從現在這一級開始算」的累積經驗（不知道等級時用 % 當單位）。"""
        n = self.need(self.level)
        return r * n if n else r

    def update(self, img, now: float) -> float | None:
        if self.reader is not None and now - self._lv_read_at >= self.LV_EVERY:
            self._lv_read_at = now
            try:
                self.see_level(self.reader.read(img, self._bars), now)
            except Exception:
                pass
        r = self.read(img)
        if r is None:
            return None
        return self.add(r, now)

    def add(self, r: float, now: float) -> float | None:
        prev = self.last_ratio
        if prev is not None and prev > 0.8 and r < 0.2:
            # 升等了：之前的紀錄換算到新的一級（新一級從 0 開始，舊的點變成負的）
            already = now - self._screen_bumped_at < 120      # 畫面上的等級剛剛已經 +1 了
            old_need = self.need(self.level - 1 if (already and self.level) else self.level)
            if already:
                self._screen_bumped_at = -1e9
            elif self.level is not None:
                self.level += 1
                self._save()
                log.info("偵測到升等！現在 %d 等", self.level)
                if self.level >= MAX_LEVEL:
                    log.info("到 200 等了，恭喜！")
            new_need = self.need(self.level)
            if already:
                log.info("EXP 條歸零（升到 %s 等）", self.level)
            shift = (old_need if old_need else 1.0)
            self.history = [(t, c - shift) for t, c in self.history]
            if old_need and not new_need:
                self.history = []      # 單位變了（不知道新一級要多少）→ 重新累積
        elif prev is not None and r < prev - 0.05:
            # 掉經驗（死掉）或讀錯 → 重新算速度
            self.history = []
        self.last_ratio = r
        # EXP 條最前面、最後面一小段（約 1.7%）看起來都一樣（全空/全滿，填色藏在框下面），
        # 這兩段的讀數不準 → 不拿來算速度（但還是用來偵測升等）
        if self._edge(r):
            return r
        self.history.append((now, self._cum(r)))
        self.history = [(t, c) for t, c in self.history if now - t <= WINDOW]
        return r

    def _edge(self, r: float) -> bool:
        if r <= 0.0:
            return True
        if self._bars is not None:
            from .expbar import fill_to_ratio
            _, (_, _, (x0, x1)) = self._bars
            w = x1 - x0
            return r >= fill_to_ratio(w - 1, w)
        return r >= 0.98

    def rate(self, now: float) -> float | None:
        """每秒漲多少（單位跟 _cum 一樣）。用最小平方法，避免一兩個讀數跳動影響太大。"""
        pts = [(t, c) for t, c in self.history if now - t <= WINDOW]
        if len(pts) < 5 or pts[-1][0] - pts[0][0] < MIN_SPAN:
            return None
        n = len(pts)
        mt = sum(t for t, _ in pts) / n
        mc = sum(c for _, c in pts) / n
        var = sum((t - mt) ** 2 for t, _ in pts)
        if var <= 0:
            return None
        k = sum((t - mt) * (c - mc) for t, c in pts) / var
        return k if k > 0 else 0.0

    # ---------- 預估 ----------
    def forecast(self, now: float) -> dict:
        """回傳 {'next': 秒 或 None, 'to200': 秒 或 None, 'per_hour': 每小時經驗 或 None, 'reason': 文字}"""
        out = {"next": None, "to200": None, "per_hour": None, "reason": ""}
        if self.last_ratio is None:
            out["reason"] = "還沒讀到 EXP 條"
            return out
        k = self.rate(now)
        if k is None:
            out["reason"] = "觀察中（要看幾分鐘 EXP 漲多快才算得出來）"
            return out
        if k <= 0:
            out["reason"] = "EXP 最近沒有在漲（沒在打怪？）"
            return out
        n = self.need(self.level)
        r = self.last_ratio
        if n:
            out["per_hour"] = k * 3600
            out["next"] = (1 - r) * n / k
            if self.level is not None and self.level < MAX_LEVEL:
                rest = [self.need(lv) for lv in range(self.level + 1, MAX_LEVEL)]
                if all(rest):
                    out["to200"] = out["next"] + sum(rest) / k
        else:
            out["next"] = (1 - r) / k          # 單位是 %，不需要經驗值表
        return out

    def text(self, now: float) -> str:
        f = self.forecast(now)
        lv = f"Lv{self.level} " if self.level else ""
        exp = f"EXP {self.last_ratio * 100:.1f}%" if self.last_ratio is not None else "EXP ?"
        if f["next"] is None:
            return f"{lv}{exp}（{f['reason']}）"
        s = f"{lv}{exp}｜下次升等：約 {fmt_dur(f['next'])}（{fmt_at(now + f['next'], now)}）"
        if self.level is not None and self.level >= MAX_LEVEL - 1:
            return s
        if f["to200"] is not None:
            s += f"｜到 200 等：約 {fmt_dur(f['to200'])}（{fmt_at(now + f['to200'], now)}）"
        elif self.level is None:
            s += "｜到 200 等：請在 config.yaml 設定 level（現在幾等）才算得出來"
        if f["per_hour"]:
            s += f"｜每小時約 {f['per_hour'] / 1e6:.0f}M 經驗"
        if self.history:
            span = (self.history[-1][0] - self.history[0][0]) / 60
            s += f"（根據最近 {span:.0f} 分鐘）"
        return s
