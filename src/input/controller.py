"""鍵鼠模擬封裝。

預設使用 pydirectinput（DirectInput，相容多數全螢幕遊戲）；未安裝時本模組仍可
import，實際送鍵時才提示安裝。所有動作皆加入可設定的隨機延遲，降低機械化特徵
（注意：這並非防偵測保證）。

註：pydirectinput 僅支援 Windows。若需更底層控制，可改以 ctypes 呼叫 SendInput
（TODO），對外介面維持不變。
"""
from __future__ import annotations

import random
import time

try:
    import pydirectinput  # type: ignore
    _PDI_AVAILABLE = True
except ImportError:
    pydirectinput = None  # type: ignore
    _PDI_AVAILABLE = False


class InputController:
    """鍵鼠控制器。

    參數：
        humanize: 對應 settings.yaml 的 humanize 區塊（隨機延遲範圍）。
        dry_run: 為 True 時只印出動作、不實際送鍵（測試 / 無遊戲環境用）。
    """

    def __init__(self, humanize=None, dry_run=False):
        humanize = humanize or {}
        self.press_min = humanize.get("key_press_min", 0.03)
        self.press_max = humanize.get("key_press_max", 0.09)
        self.delay_min = humanize.get("action_delay_min", 0.05)
        self.delay_max = humanize.get("action_delay_max", 0.18)
        self.dry_run = dry_run
        if _PDI_AVAILABLE:
            # pydirectinput 預設每次送鍵後自動再睡 PAUSE=0.1 秒，會把「極短轉向
            # 按鍵」硬拉長到 ~0.13 秒——超過 0.1 秒角色就會走一步，貼邊打怪時
            # 每圈都往怪那側滑、慢慢滑出平台。節奏改由 humanize 隨機延遲控制。
            pydirectinput.PAUSE = 0.0

    def _require_backend(self):
        if self.dry_run:
            return
        if not _PDI_AVAILABLE:
            raise RuntimeError("尚未安裝 pydirectinput。請執行： pip install pydirectinput")

    # ---- 內部工具 ----
    @staticmethod
    def _rand(lo, hi):
        return random.uniform(lo, hi)

    def _sleep_action(self):
        time.sleep(self._rand(self.delay_min, self.delay_max))

    # ---- 按鍵 ----
    def key_down(self, key):
        self._require_backend()
        if self.dry_run:
            print(f"[dry_run] keyDown {key}")
        else:
            pydirectinput.keyDown(key)

    def key_up(self, key):
        self._require_backend()
        if self.dry_run:
            print(f"[dry_run] keyUp {key}")
        else:
            pydirectinput.keyUp(key)

    def tap(self, key, duration=None):
        """按一下按鍵（含隨機按住時間與後續延遲）。"""
        self._require_backend()
        hold = duration if duration is not None else self._rand(self.press_min, self.press_max)
        self.key_down(key)
        time.sleep(hold)
        self.key_up(key)
        self._sleep_action()

    def hold(self, key, seconds):
        """按住按鍵指定秒數（用於持續移動）。"""
        self._require_backend()
        self.key_down(key)
        time.sleep(seconds)
        self.key_up(key)
        self._sleep_action()

    # ---- 滑鼠（rune 解謎 / 撿物可能用到）----
    def move_mouse(self, x, y):
        self._require_backend()
        if self.dry_run:
            print(f"[dry_run] moveTo ({x}, {y})")
        else:
            pydirectinput.moveTo(x, y)

    def click(self, x=None, y=None):
        self._require_backend()
        if self.dry_run:
            print(f"[dry_run] click ({x}, {y})")
        elif x is not None and y is not None:
            pydirectinput.click(x, y)
        else:
            pydirectinput.click()
        self._sleep_action()
