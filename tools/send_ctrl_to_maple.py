# -*- coding: utf-8 -*-
"""只做一件事：把 Ctrl 送到「楓之谷」那個視窗。

支援兩種送法，讓你實測哪個對你的遊戲有效：

  postmessage : 用 PostMessage 把按鍵訊息「直接丟進楓之谷視窗的佇列」，
                不用把它切到前景、不搶你的滑鼠鍵盤。可送背景視窗。
                缺點：DirectInput 類遊戲常常不讀視窗訊息佇列 → 可能無效。

  sendinput   : 先把楓之谷切到前景，再用 SendInput 送（硬體層 scancode）。
                只要它在前景就吃得到，但會搶焦點；焦點被別的視窗搶走就失敗，
                所以會「有時成功有時失敗」。這裡每次送之前都重新確認+搶回前景，
                把那個「有時失敗」補起來。

用法：
    # 先確認找不找得到視窗、有哪些子視窗（不送鍵）
    python tools/send_ctrl_to_maple.py --list

    # 每 1 秒送一次 Ctrl，用 PostMessage（背景、不搶焦點）
    python tools/send_ctrl_to_maple.py --method postmessage --interval 1

    # 每 0.8 秒送一次，用 SendInput（每次先搶回前景）
    python tools/send_ctrl_to_maple.py --method sendinput --interval 0.8

    # 只送 5 次就結束（測試用）
    python tools/send_ctrl_to_maple.py --method postmessage --times 5

按 F12 或 Ctrl+C 停止。送鍵需要「以系統管理員身分」執行才進得去。
僅供學習研究，自動化操作線上遊戲風險自負（會被反外掛偵測、有封號風險）。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import sys
import time

_user32 = ctypes.windll.user32

# ---- Windows 常數 ----
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_CONTROL = 0x11
VK_F12 = 0x7B
SCAN_LCTRL = 0x1D          # 左 Ctrl 的硬體掃描碼


# ============================================================
#  找視窗 + 列出子視窗
# ============================================================
def find_window(keyword: str):
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
            found.append((hwnd, buf.value))
            return False
        return True

    _user32.EnumWindows(_cb, 0)
    return found[0] if found else (None, None)


def list_children(hwnd):
    kids = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def _cb(child, _lp):
        cls = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(child, cls, 256)
        title = ctypes.create_unicode_buffer(256)
        _user32.GetWindowTextW(child, title, 256)
        kids.append((child, cls.value, title.value))
        return True

    _user32.EnumChildWindows(hwnd, _cb, 0)
    return kids


# ============================================================
#  兩種送 Ctrl 的方式
# ============================================================
def _lparam(scan, keyup=False):
    """組 WM_KEY* 的 lParam：repeat=1, scancode, 轉態旗標。"""
    lp = 1 | (scan << 16)
    if keyup:
        lp |= (1 << 30) | (1 << 31)  # 之前是按下的、放開
    return lp


def send_ctrl_postmessage(hwnd) -> bool:
    """把 Ctrl 的 KEYDOWN/KEYUP 直接 Post 到指定視窗佇列（不搶前景）。"""
    ok1 = _user32.PostMessageW(hwnd, WM_KEYDOWN, VK_CONTROL, _lparam(SCAN_LCTRL, False))
    time.sleep(0.04)
    ok2 = _user32.PostMessageW(hwnd, WM_KEYUP, VK_CONTROL, _lparam(SCAN_LCTRL, True))
    return bool(ok1 and ok2)


# ---- SendInput 定義（scancode）----
PUL = ctypes.POINTER(ctypes.c_ulong)


class _KBD(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", PUL)]


class _II(ctypes.Union):
    _fields_ = [("ki", _KBD)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", _II)]


KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002


def _sendinput_scan(scan, keyup):
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if keyup else 0)
    extra = ctypes.c_ulong(0)
    ii = _II(); ii.ki = _KBD(0, scan, flags, 0, ctypes.pointer(extra))
    cmd = _INPUT(1, ii)
    return _user32.SendInput(1, ctypes.pointer(cmd), ctypes.sizeof(cmd))


def ensure_foreground(hwnd) -> bool:
    if _user32.GetForegroundWindow() == hwnd:
        return True
    _user32.keybd_event(0x12, 0, 0, 0)   # 空按 ALT 解前景鎖
    _user32.keybd_event(0x12, 0, 2, 0)
    _user32.SetForegroundWindow(hwnd)
    time.sleep(0.05)
    return _user32.GetForegroundWindow() == hwnd


def send_ctrl_sendinput(hwnd) -> bool:
    """先確認/搶回前景，再用 SendInput 送 Ctrl。回傳是否成功送出。"""
    if not ensure_foreground(hwnd):
        return False
    r1 = _sendinput_scan(SCAN_LCTRL, False)
    time.sleep(0.05)
    r2 = _sendinput_scan(SCAN_LCTRL, True)
    return bool(r1 and r2)


def _pressed(vk):
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


# ============================================================
#  主程式
# ============================================================
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="把 Ctrl 送到楓之谷視窗（兩種方法可測）")
    p.add_argument("--window", default="新楓之谷", help="視窗標題關鍵字")
    p.add_argument("--method", choices=("postmessage", "sendinput"), default="postmessage",
                   help="postmessage=指定視窗背景送 / sendinput=搶前景送")
    p.add_argument("--child", action="store_true",
                   help="postmessage 時改送到第一個子視窗（有些遊戲的畫面是子視窗）")
    p.add_argument("--interval", type=float, default=1.0, help="每幾秒送一次 Ctrl")
    p.add_argument("--times", type=int, default=0, help="總共送幾次（0=無限，直到 F12）")
    p.add_argument("--list", action="store_true", help="只列出視窗與子視窗，不送鍵")
    return p.parse_args(argv)


def main(argv=None):
    if sys.platform != "win32":
        print("[錯誤] 僅支援 Windows。")
        return 1
    args = parse_args(argv)

    hwnd, title = find_window(args.window)
    if not hwnd:
        print(f"[錯誤] 找不到標題含「{args.window}」的視窗，請先開好遊戲。")
        return 1
    print(f"找到視窗：hwnd={hwnd}  標題「{title}」")

    kids = list_children(hwnd)
    if args.list or args.child:
        print(f"子視窗共 {len(kids)} 個：")
        for h, cls, t in kids[:20]:
            print(f"  hwnd={h}  class={cls!r}  title={t!r}")
        if args.list:
            return 0

    target = hwnd
    if args.child:
        if not kids:
            print("[警告] 沒有子視窗，改用主視窗。")
        else:
            target = kids[0][0]
            print(f"改送到子視窗 hwnd={target}")

    print("=" * 56)
    print(f" 送 Ctrl → 楓之谷  |  方法={args.method}  間隔={args.interval}s")
    print(f" 停止：F12 或 Ctrl+C")
    print("=" * 56)

    sent = ok = 0
    try:
        while True:
            if _pressed(VK_F12):
                print("\n[F12] 停止。")
                break
            if args.method == "postmessage":
                good = send_ctrl_postmessage(target)
            else:
                good = send_ctrl_sendinput(hwnd)
            sent += 1
            ok += 1 if good else 0
            flag = "OK" if good else "送出失敗(API 回 0)"
            print(f"  第 {sent} 次送 Ctrl → {flag}")
            if args.times and sent >= args.times:
                print(f"\n送完 {args.times} 次。API 成功 {ok}/{sent}。")
                break
            time.sleep(max(0.05, args.interval))
    except KeyboardInterrupt:
        print("\n[Ctrl+C] 停止。")

    print(f"總共送 {sent} 次，API 回報成功 {ok} 次。")
    print("※ 注意：API 回 OK 只代表『訊息送出去了』，遊戲有沒有真的收到要看畫面。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
