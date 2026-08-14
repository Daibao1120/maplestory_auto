# -*- coding: utf-8 -*-
"""連續點擊攻擊鍵、每隔一段時間原地挪一下；按方向鍵就暫停，按 Ctrl 恢復。

用途：最單純的掛機節奏——連點攻擊鍵 farm，每隔 45~90 秒原地左右挪一下重定位，
移動完立刻接回攻擊。你隨時可用方向鍵接管（腳本會讓出控制），處理完按 Ctrl 續掛。

為何用「連點」而非「按住」：一次 SendInput keydown 不會 auto-repeat，靠按住撐
容易打一陣子就停、移動後接不回攻擊。連點（keydown+keyup 快速重複）不管遊戲是
「按一下打一下」還是「按住連打」都吃得到，移動後也能無縫接回。

  ┌─ 執行中：每 --attack-interval 秒點一下 --key；每 45~90 秒原地挪一下
  ├─ 你按 方向鍵 介入 → 暫停（放開按鍵，把控制權還你）
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

    def __init__(self, attack_key="ctrl", interval_min=45.0, interval_max=90.0,
                 move_time=0.08, attack_interval=0.22, enable_move=True, dry_run=False):
        self.attack_key = attack_key
        self.interval_min = float(interval_min)
        self.interval_max = float(interval_max)
        self.move_time = float(move_time)          # 每個方向按住秒數（越小走越少）
        self.attack_interval = float(attack_interval)  # 兩次攻擊點擊的間隔（秒）
        self.enable_move = enable_move              # False = 完全不移動，只連點攻擊
        self.dry_run = dry_run

    def _next_interval(self):
        """下一次移動的等待秒數：區間內隨機，避免固定週期像機器。"""
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
        time.sleep(hold if hold is not None else random.uniform(0.04, 0.10))
        self._up(key)

    def _attack_once(self):
        """點一下攻擊鍵。連續呼叫 = 連續攻擊，比『按住不放』可靠——
        不管遊戲是『按一下打一下』還是『按住連打』都吃得到，移動後也能無縫接回。"""
        self._tap(self.attack_key, hold=random.uniform(0.04, 0.09))

    def _user_intervened(self):
        """使用者是否按著方向鍵（攻擊鍵是我們自己在點，不算介入）。"""
        return any(_pressed(_VK[k]) for k in self.INTERVENE_KEYS)

    def _move(self):
        """往一邊走一小步再走回來（淨位移≈0）。走完直接 return，
        主迴圈下一圈就會馬上接回連點攻擊——這就是『移動後繼續攻擊』的關鍵。"""
        if not self.enable_move:
            return
        first = random.choice(("left", "right"))
        second = "right" if first == "left" else "left"
        t = self.move_time
        print(f"  ↻ 原地挪一下（{first}↔{second}，每邊 {t:.2f}s）→ 接回攻擊")
        self._tap(first, hold=t + random.uniform(-0.01, 0.01))
        time.sleep(random.uniform(0.04, 0.1))
        self._tap(second, hold=t + random.uniform(-0.01, 0.01))
        time.sleep(random.uniform(0.05, 0.12))

    def run(self, window_keyword):
        hwnd = _find_window(window_keyword)
        if hwnd is None:
            print(f"[錯誤] 找不到視窗標題含「{window_keyword}」的遊戲。請先開好遊戲。")
            return 1
        if not self.dry_run and not _foreground(hwnd):
            print("[錯誤] 無法把遊戲切到前景，按鍵不會生效。")
            return 1

        move_desc = "不移動" if not self.enable_move else \
            f"每 {self.interval_min:.0f}~{self.interval_max:.0f} 秒原地挪一下"
        print("=" * 56)
        print(f" 連點「{self.attack_key}」攻擊（每 {self.attack_interval:.2f}s 一下）；{move_desc}")
        print(f" 介入=方向鍵(暫停) | 恢復=Ctrl | 結束=F12")
        if self.dry_run:
            print(" [dry-run] 只印節奏、不送實體按鍵")
        print("=" * 56)

        state = "RUNNING"
        cycle_start = time.time()
        wait = self._next_interval()
        last_attack = 0.0
        if self.enable_move:
            print(f"  開始攻擊；下次移動：{wait:.0f} 秒後")
        else:
            print("  開始攻擊（不移動）")
        try:
            while True:
                if _pressed(_VK[self.QUIT_KEY]):
                    print("\n[F12] 結束。")
                    break

                if state == "RUNNING":
                    if self._user_intervened():
                        state = "PAUSED"
                        print("  ⏸ 偵測到你按方向鍵 → 暫停攻擊，控制權還你。按 Ctrl 恢復…")
                        while self._user_intervened():
                            time.sleep(self.POLL)
                        continue
                    now = time.time()
                    if self.enable_move and now - cycle_start >= wait:
                        self._move()
                        cycle_start = time.time()
                        wait = self._next_interval()
                        last_attack = 0.0  # 移動後立刻接回攻擊（下一圈馬上點）
                        print(f"  （下次移動：{wait:.0f} 秒後）")
                    elif now - last_attack >= self.attack_interval:
                        self._attack_once()
                        last_attack = time.time()

                elif state == "PAUSED":
                    if _pressed(_VK[self.RESUME_KEY]):
                        while _pressed(_VK[self.RESUME_KEY]):
                            time.sleep(self.POLL)
                        state = "RUNNING"
                        cycle_start = time.time()
                        wait = self._next_interval()
                        last_attack = 0.0
                        print(f"  ▶ 已恢復攻擊。" +
                              (f"（下次移動：{wait:.0f} 秒後）" if self.enable_move else ""))

                time.sleep(self.POLL)
        except KeyboardInterrupt:
            print("\n[Ctrl+C] 中斷。")
        finally:
            # 保險起見放開攻擊鍵與方向鍵，別讓遊戲卡在按住狀態
            for k in (self.attack_key, "left", "right"):
                try:
                    self._up(k)
                except Exception:
                    pass
        return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="按住鍵 + 定時左右動一下的簡易掛機節奏器")
    p.add_argument("--key", default="ctrl", choices=sorted(_SCAN),
                   help="攻擊鍵（預設 ctrl）；會被連續點擊")
    p.add_argument("--attack-interval", type=float, default=0.22,
                   help="兩次攻擊點擊的間隔秒數（預設 0.22，越小打越快）")
    p.add_argument("--interval-min", type=float, default=45.0,
                   help="兩次移動之間最短秒數（預設 45）")
    p.add_argument("--interval-max", type=float, default=90.0,
                   help="兩次移動之間最長秒數（預設 90）；實際每次在 min~max 隨機")
    p.add_argument("--move-time", type=float, default=0.08,
                   help="每個方向按住秒數，越小走越少（預設 0.08）；左右對稱走回原點")
    p.add_argument("--no-move", action="store_true",
                   help="完全不移動，只連點攻擊（換小圖或只想刷攻擊時用）")
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
    hw = HoldWiggle(attack_key=args.key, interval_min=args.interval_min,
                    interval_max=args.interval_max, move_time=args.move_time,
                    attack_interval=args.attack_interval,
                    enable_move=not args.no_move, dry_run=args.dry_run)
    return hw.run(args.window)


if __name__ == "__main__":
    sys.exit(main())
