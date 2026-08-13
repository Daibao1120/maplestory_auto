# -*- coding: utf-8 -*-
"""持續按住一個鍵、每隔 N 秒左右動一下；偵測到你親自按方向鍵就暫停，按 Ctrl 恢復。

用途：最單純的掛機節奏——按住攻擊鍵farm，每 60 秒左右各點一下重定位，
避免長時間定點。你隨時可用方向鍵接管（腳本會讓出控制），處理完按 Ctrl 續掛。

  ┌─ 執行中：按住 --key，每 --interval 秒放開→點左→點右→再按住
  ├─ 你按 方向鍵 介入 → 暫停（放開所有鍵，把控制權還你）
  ├─ 暫停中按 Ctrl → 恢復執行
  └─ 任何時候按 F12 → 完全結束

⚠️ 送鍵需求：遊戲多半以系統管理員權限執行，本腳本也必須「以系統管理員身分」
   執行，送出的按鍵才進得去（UIPI 權限隔離；否則按鍵會被 Windows 靜默丟棄）。

用法（在「以系統管理員身分」開的終端機裡）：
    python tools/hold_and_wiggle.py                     # 按住 ctrl，每 60 秒 wiggle
    python tools/hold_and_wiggle.py --key ctrl --interval 60
    python tools/hold_and_wiggle.py --window 新楓之谷 --dry-run   # 只印節奏、不送鍵

僅供學習研究，使用自動化操作線上遊戲風險自負。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import random
import sys
import time

# ============================================================
#  低階送鍵：ctypes SendInput（scancode；方向鍵加 EXTENDEDKEY）
#  — 比 pydirectinput 更底層，較能被全螢幕遊戲接受。
# ============================================================
PUL = ctypes.POINTER(ctypes.c_ulong)


class _KeyBdInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", PUL)]


class _HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_short),
                ("wParamH", ctypes.c_ushort)]


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]


class _InputI(ctypes.Union):
    _fields_ = [("ki", _KeyBdInput), ("mi", _MouseInput), ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", _InputI)]


_KEYEVENTF_SCANCODE = 0x0008
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_EXTENDEDKEY = 0x0001

# 名稱 → (scancode, 是否為擴充鍵)。方向鍵必須是擴充鍵，否則遊戲可能收不到。
_SCAN = {
    "left":  (0x4B, True),
    "right": (0x4D, True),
    "up":    (0x48, True),
    "down":  (0x50, True),
    "ctrl":  (0x1D, False),
    "alt":   (0x38, False),
    "space": (0x39, False),
    "shift": (0x2A, False),
}

# 名稱 → 虛擬鍵碼（給 GetAsyncKeyState 讀「使用者有沒有按」）
_VK = {
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "ctrl": 0x11, "alt": 0x12, "space": 0x20, "shift": 0x10, "f12": 0x7B,
}

_user32 = ctypes.windll.user32


def _send_key(name: str, keyup: bool) -> None:
    scan, extended = _SCAN[name]
    flags = _KEYEVENTF_SCANCODE
    if keyup:
        flags |= _KEYEVENTF_KEYUP
    if extended:
        flags |= _KEYEVENTF_EXTENDEDKEY
    extra = ctypes.c_ulong(0)
    ii = _InputI()
    ii.ki = _KeyBdInput(0, scan, flags, 0, ctypes.pointer(extra))
    cmd = _Input(1, ii)  # INPUT_KEYBOARD = 1
    _user32.SendInput(1, ctypes.pointer(cmd), ctypes.sizeof(cmd))


def _pressed(vk: int) -> bool:
    """使用者當下是否按著這個鍵（最高位元 = 現在被按住）。"""
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


# ============================================================
#  視窗前景化（送鍵前必須讓遊戲成為前景視窗）
# ============================================================
def _find_window(keyword: str):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _lp):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        if keyword in buf.value:
            found.append(hwnd)
            return False
        return True

    _user32.EnumWindows(_cb, 0)
    return found[0] if found else None


def _foreground(hwnd) -> bool:
    # 空按一下 ALT 解除 Windows 的前景切換鎖，再切前景
    _user32.keybd_event(0x12, 0, 0, 0)
    _user32.keybd_event(0x12, 0, 2, 0)
    _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    _user32.SetForegroundWindow(hwnd)
    time.sleep(0.6)
    return _user32.GetForegroundWindow() == hwnd


# ============================================================
#  主控制器
# ============================================================
class HoldWiggle:
    """狀態機：RUNNING（按住+定時 wiggle）↔ PAUSED（讓出控制，等 Ctrl）。"""

    INTERVENE_KEYS = ("left", "right", "up", "down")  # 使用者按這些 = 介入
    RESUME_KEY = "ctrl"
    QUIT_KEY = "f12"
    POLL = 0.03  # 秒；輪詢間隔

    def __init__(self, hold_key="ctrl", interval_min=45.0, interval_max=90.0,
                 dry_run=False):
        self.hold_key = hold_key
        self.interval_min = float(interval_min)
        self.interval_max = float(interval_max)
        self.dry_run = dry_run
        self._holding = False

    def _next_interval(self):
        """下一次 wiggle 的等待秒數：區間內隨機，避免固定週期像機器。"""
        return random.uniform(self.interval_min, self.interval_max)

    # ---- 送鍵包裝（dry_run 只印不送）----
    def _down(self, key):
        if self.dry_run:
            print(f"    [dry] keyDown {key}")
        else:
            _send_key(key, keyup=False)

    def _up(self, key):
        if self.dry_run:
            print(f"    [dry] keyUp {key}")
        else:
            _send_key(key, keyup=True)

    def _tap(self, key, hold=None):
        self._down(key)
        # 按住時間帶隨機（人不會每次都按一樣久）
        time.sleep(hold if hold is not None else random.uniform(0.06, 0.16))
        self._up(key)

    def _hold_key_down(self):
        if not self._holding:
            self._down(self.hold_key)
            self._holding = True

    def _hold_key_up(self):
        if self._holding:
            self._up(self.hold_key)
            self._holding = False

    def _user_intervened(self):
        # hold_key 是腳本自己按著的，排除；只看方向鍵是否被「使用者」按下。
        return any(_pressed(_VK[k]) for k in self.INTERVENE_KEYS if k != self.hold_key)

    def _wiggle(self):
        """放開按住鍵 → 隨機左右移動幾下 → 重新按住（模擬真人重定位）。"""
        # 隨機方向起手、隨機步數、每步間隔不固定；偶爾多繞一下
        first = random.choice(("left", "right"))
        seq = [first, "left" if first == "right" else "right"]
        if random.random() < 0.4:            # 40% 機率多動一下，避免每次都剛好兩步
            seq.append(random.choice(("left", "right")))
        print(f"  ↻ 時間到 → 移動一下（{'→'.join(seq)}）")
        self._hold_key_up()
        time.sleep(random.uniform(0.08, 0.2))
        for d in seq:
            # 走一小段（帶住方向鍵一段隨機時間），比單點更像真的走位
            self._tap(d, hold=random.uniform(0.12, 0.35))
            time.sleep(random.uniform(0.05, 0.18))
        time.sleep(random.uniform(0.1, 0.25))
        self._hold_key_down()

    def run(self, window_keyword):
        hwnd = _find_window(window_keyword)
        if hwnd is None:
            print(f"[錯誤] 找不到視窗標題含「{window_keyword}」的遊戲。請先開好遊戲。")
            return 1
        if not self.dry_run and not _foreground(hwnd):
            print("[錯誤] 無法把遊戲切到前景，按鍵不會生效。")
            return 1

        print("=" * 56)
        print(f" 按住「{self.hold_key}」掛機；每 {self.interval_min:.0f}~{self.interval_max:.0f} 秒"
              f"隨機移動一下")
        print(f" 介入=方向鍵(暫停) | 恢復=Ctrl | 結束=F12")
        if self.dry_run:
            print(" [dry-run] 只印節奏、不送實體按鍵")
        print("=" * 56)

        state = "RUNNING"
        self._hold_key_down()
        cycle_start = time.time()
        wait = self._next_interval()
        print(f"  （下次移動：{wait:.0f} 秒後）")
        try:
            while True:
                if _pressed(_VK[self.QUIT_KEY]):
                    print("\n[F12] 結束。")
                    break

                if state == "RUNNING":
                    if self._user_intervened():
                        self._hold_key_up()
                        state = "PAUSED"
                        print("  ⏸ 偵測到你按方向鍵 → 暫停，控制權還你。按 Ctrl 恢復掛機…")
                        # 等使用者放開方向鍵，避免立刻又被判為 Ctrl 誤觸
                        while self._user_intervened():
                            time.sleep(self.POLL)
                        continue
                    if time.time() - cycle_start >= wait:
                        self._wiggle()
                        cycle_start = time.time()
                        wait = self._next_interval()
                        print(f"  （下次移動：{wait:.0f} 秒後）")

                elif state == "PAUSED":
                    if _pressed(_VK[self.RESUME_KEY]):
                        # 等放開 Ctrl 再開始，避免 wiggle 前的按住卡住
                        while _pressed(_VK[self.RESUME_KEY]):
                            time.sleep(self.POLL)
                        state = "RUNNING"
                        self._hold_key_down()
                        cycle_start = time.time()
                        wait = self._next_interval()
                        print(f"  ▶ 已恢復，繼續按住掛機。（下次移動：{wait:.0f} 秒後）")

                time.sleep(self.POLL)
        except KeyboardInterrupt:
            print("\n[Ctrl+C] 中斷。")
        finally:
            self._hold_key_up()  # 收尾一定放開，別讓遊戲卡在按住狀態
        return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="按住鍵 + 定時左右動一下的簡易掛機節奏器")
    p.add_argument("--key", default="ctrl", choices=sorted(_SCAN),
                   help="要持續按住的鍵（預設 ctrl＝攻擊）")
    p.add_argument("--interval-min", type=float, default=45.0,
                   help="兩次移動之間最短秒數（預設 45）")
    p.add_argument("--interval-max", type=float, default=90.0,
                   help="兩次移動之間最長秒數（預設 90）；實際每次在 min~max 隨機")
    p.add_argument("--window", default="新楓之谷", help="遊戲視窗標題關鍵字")
    p.add_argument("--dry-run", action="store_true", help="只印節奏、不送實體按鍵")
    args = p.parse_args(argv)
    if args.interval_min > args.interval_max:
        args.interval_min, args.interval_max = args.interval_max, args.interval_min
    return args


def main(argv=None):
    if sys.platform != "win32":
        print("[錯誤] 本工具僅支援 Windows（需 SendInput 送鍵）。")
        return 1
    args = parse_args(argv)
    hw = HoldWiggle(hold_key=args.key, interval_min=args.interval_min,
                    interval_max=args.interval_max, dry_run=args.dry_run)
    return hw.run(args.window)


if __name__ == "__main__":
    sys.exit(main())
