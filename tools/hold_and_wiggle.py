# -*- coding: utf-8 -*-
"""連續點擊攻擊鍵、定時在小範圍內巡邏挪一步；按方向鍵就暫停，按 Ctrl 恢復。

用途：最單純的掛機節奏——連點攻擊鍵 farm，每隔幾秒往同方向挪一小步、走到
±patrol_steps 步就折返，讓角色在小平台內來回掃（定點打會無效，但平台又很小
不能亂走）。移動完立刻接回攻擊。方向鍵接管、Ctrl 續掛、F12 結束。

為何用「連點」而非「按住」：一次 SendInput keydown 不會 auto-repeat，靠按住撐
容易打一陣子就停、移動後接不回攻擊。連點（keydown+keyup 快速重複）不管遊戲是
「按一下打一下」還是「按住連打」都吃得到，移動後也能無縫接回。

  ┌─ 執行中：每 --attack-interval 秒點一下 --key；每 40~55 秒巡邏挪一步（±patrol_steps 內來回）
  ├─ 攻擊方向沿用你角色當下的面向；移動往反方向走完會自動轉回
  ├─ 手動換邊：按 方向鍵 暫停 → 轉到你要的方向 → 按 Ctrl 恢復（會朝你最後面對的方向打）
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
import os
import random
import sys
import threading
import time

# repo 根目錄（本檔在 tools/ 底下）——edge-guard 需要 import src.capture / src.vision
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010

# 名稱 → (scancode, 是否為擴充鍵)。方向鍵/編輯區鍵必須是擴充鍵，否則遊戲可能收不到。
_SCAN = {
    "left":  (0x4B, True),
    "right": (0x4D, True),
    "up":    (0x48, True),
    "down":  (0x50, True),
    "ctrl":  (0x1D, False),
    "alt":   (0x38, False),
    "space": (0x39, False),
    "shift": (0x2A, False),
    "delete": (0x53, True),
    "end":    (0x4F, True),
    "insert": (0x52, True),
    "home":   (0x47, True),
    "pageup": (0x49, True),
    "pagedown": (0x51, True),
    "f1": (0x3B, False), "f2": (0x3C, False), "f3": (0x3D, False), "f4": (0x3E, False),
}

# 名稱 → 虛擬鍵碼（給 GetAsyncKeyState 讀「使用者有沒有按」）
_VK = {
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "ctrl": 0x11, "alt": 0x12, "space": 0x20, "shift": 0x10, "f12": 0x7B,
    "delete": 0x2E, "end": 0x23, "insert": 0x2D, "home": 0x24,
    "pageup": 0x21, "pagedown": 0x22,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
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


class _PhysicalKeys:
    """低階鍵盤鉤子執行緒：只追蹤「實體」按鍵狀態（忽略注入事件）。

    GetAsyncKeyState 分不出實體按鍵和我們自己 SendInput 注入的按鍵——
    誤把使用者按住當卡鍵去強制放開，會讓玩家「動不了」；反過來把我們
    卡死的注入鍵當成使用者介入，工具會袖手旁觀讓角色走到掉出平台。
    WH_KEYBOARD_LL 的事件帶 LLKHF_INJECTED 旗標，可精準只記實體鍵。
    """

    _WM_DOWN = (0x0100, 0x0104)   # WM_KEYDOWN, WM_SYSKEYDOWN
    _WM_UP = (0x0101, 0x0105)     # WM_KEYUP, WM_SYSKEYUP
    _LLKHF_INJECTED = 0x10

    def __init__(self, vk_codes):
        self.state = {vk: False for vk in vk_codes}
        self.ok = False
        self._proc = None            # 防 GC
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD),
                        ("flags", wt.DWORD), ("time", wt.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                      wt.WPARAM, wt.LPARAM)
        # 64 位元下必須明確宣告簽章：預設把指標當 32 位元 int 傳會 OverflowError
        _user32.SetWindowsHookExW.restype = ctypes.c_void_p
        _user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC,
                                              wt.HINSTANCE, wt.DWORD)
        _user32.CallNextHookEx.restype = ctypes.c_ssize_t
        _user32.CallNextHookEx.argtypes = (ctypes.c_void_p, ctypes.c_int,
                                           wt.WPARAM, wt.LPARAM)

        def proc(n_code, w_param, l_param):
            try:
                if n_code >= 0:
                    kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if not (kb.flags & self._LLKHF_INJECTED) and kb.vkCode in self.state:
                        if w_param in self._WM_DOWN:
                            self.state[kb.vkCode] = True
                        elif w_param in self._WM_UP:
                            self.state[kb.vkCode] = False
            except Exception:
                pass  # 鉤子 callback 絕不能拋例外（會拖慢全系統鍵盤）
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._proc = HOOKPROC(proc)
        hook = _user32.SetWindowsHookExW(13, self._proc, None, 0)  # WH_KEYBOARD_LL
        if not hook:
            return  # ok 維持 False → 呼叫端退回 GetAsyncKeyState
        self.ok = True
        msg = wt.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0):
            pass


def _right_click_at(sx: int, sy: int) -> None:
    """在螢幕座標 (sx, sy) 按一下滑鼠右鍵，點完把游標移回原位。"""
    pt = wt.POINT()
    had_old = bool(_user32.GetCursorPos(ctypes.byref(pt)))
    _user32.SetCursorPos(int(sx), int(sy))
    time.sleep(0.04)
    for flags in (_MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP):
        extra = ctypes.c_ulong(0)
        ii = _InputI()
        ii.mi = _MouseInput(0, 0, 0, flags, 0, ctypes.pointer(extra))
        cmd = _Input(0, ii)  # INPUT_MOUSE = 0
        _user32.SendInput(1, ctypes.pointer(cmd), ctypes.sizeof(cmd))
        time.sleep(0.03)
    if had_old:
        _user32.SetCursorPos(pt.x, pt.y)


# ============================================================
#  視窗前景化（送鍵前必須讓遊戲成為前景視窗）
# ============================================================
# 標題比對會誤中瀏覽器分頁（Chrome 開著遊戲官網登入頁時，分頁標題就叫
# 「新楓之谷：經典版」）——對瀏覽器送鍵是危險的誤操作，一律排除。
_NON_GAME_EXES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "iexplore.exe", "explorer.exe", "code.exe", "windowsterminal.exe",
    "powershell.exe", "cmd.exe", "conhost.exe", "notepad.exe", "python.exe",
    "pythonw.exe", "claude.exe", "discord.exe", "linefortab.exe", "line.exe",
}


def _window_exe(hwnd) -> str:
    """回傳視窗所屬行程的執行檔名（小寫）；失敗回空字串。"""
    pid = wt.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = ctypes.c_ulong(520)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
    finally:
        k32.CloseHandle(h)
    return ""


def _window_class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def is_game_window(title: str, exe: str, cls: str, keyword: str,
                   width: int = 0, height: int = 0) -> bool:
    """判斷這個視窗是不是「真的遊戲視窗」（純函式，可測試）。

    規則：標題要含關鍵字；執行檔不得是瀏覽器/終端機等（否則就是誤中分頁
    標題）；視窗尺寸要像遊戲畫面（避免抓到小型工具視窗）。
    遊戲本體的 window class 通常是 MapleStoryClass，命中即直接採用。
    """
    if keyword and keyword not in (title or ""):
        return False
    if cls and "maplestory" in cls.lower():
        return True
    if (exe or "") in _NON_GAME_EXES:
        return False
    if width and height and (width < 640 or height < 480):
        return False
    return True


def _find_window(keyword: str):
    """找出真正的遊戲視窗（排除瀏覽器分頁等同名視窗）。"""
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
        title = buf.value
        if keyword not in title:
            return True
        rect = wt.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w_, h_ = rect.right - rect.left, rect.bottom - rect.top
        exe, cls = _window_exe(hwnd), _window_class(hwnd)
        if is_game_window(title, exe, cls, keyword, w_, h_):
            # class 命中遊戲本體 → 最優先；否則先記著繼續找更好的
            found.append((0 if "maplestory" in (cls or "").lower() else 1, hwnd))
        return True

    _user32.EnumWindows(_cb, 0)
    if not found:
        return None
    found.sort(key=lambda p: p[0])
    return found[0][1]


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

    def __init__(self, attack_key="ctrl", interval_min=35.0, interval_max=50.0,
                 move_time=0.18, attack_interval=0.22, hold_attack=False, patrol_steps=2,
                 attack_facing="left", enable_move=True, refocus=True,
                 jump_in_place=False, jump_key="alt",
                 edge_guard=False, edge_margin=6,
                 dispel_buff=False, dispel_interval=5.0,
                 alternate_face=True, swap_every=1,
                 smart_face=True, shuffle=False, tuning_path=None, dry_run=False):
        self.attack_key = attack_key
        self.interval_min = float(interval_min)
        self.interval_max = float(interval_max)
        self.move_time = float(move_time)          # 每步按住方向鍵秒數（越小步伐越小）
        self.attack_interval = float(attack_interval)  # 兩次攻擊點擊的間隔（秒）
        # 按住攻擊：壓著不放（遊戲按住連打的最大輸出），每 2~4 秒快速鬆壓
        # 補一下——單次 SendInput keydown 不會 auto-repeat，遊戲偶爾會把
        # 按住狀態吃掉（「打一陣子就停」），定期補壓就不會斷
        self.hold_attack = hold_attack
        self._atk_held = False
        self._atk_refresh = 0.0
        self.patrol_steps = max(1, int(patrol_steps))  # 從中心往單邊最多走幾步就折返
        self.attack_facing = attack_facing          # 固定攻擊方向（left/right）；移動後轉回這邊
        self.enable_move = enable_move              # False = 完全不移動，只連點攻擊
        self.jump_in_place = jump_in_place          # True = 改用原地跳重定位（小平台不會掉下去）
        self.jump_key = jump_key                    # 跳躍鍵
        # 小碎步：往一邊走一小步再走回來。攻擊失效判定看「水平位移」（原地跳
        # 沒用），這樣有真實位移、淨位移≈0，細樹枝平台也安全；回程刻意稍短，
        # 每次淨往中心靠一點。
        self.shuffle = shuffle
        self.edge_guard = edge_guard                # True = 用小地圖真實 x 巡邏、到邊界折返（不會走出平台）
        self.edge_margin = int(edge_margin)         # 安全界：起始 x ± 這麼多小地圖 px
        self._edge_guard_wanted = edge_guard        # 啟動時失敗不放棄：之後每次挪步前再試著啟用
        self._edge_guard_broken = False             # 缺套件等致命錯 → 不再重試
        # 常駐位置警衛：挪步之間怪物擊退也會把角色往外推（不按鍵也位移），
        # 所以每 guard_interval 秒就看一次位置，出界立刻推回，不等挪步時機。
        self.guard_interval = 2.5
        self._guard_next = 0.0
        self._phys = None            # 實體按鍵鉤子（run() 時建立）
        self._stuck_note_ts = 0.0
        self._last_y = None                         # 最近一次玩家點的小地圖 y
        self._edge_center_y = None                  # 啟用時的高度（偵測掉層用）
        self._y_off_count = 0
        self.dispel_buff = dispel_buff              # True = 偵測「要點掉的 buff」（如速度激發）並右鍵移除
        self.dispel_interval = float(dispel_interval)
        # 防攻擊失效的關鍵是「真的換邊打」：挪步後朝新的一側持續輸出到下一次
        # 挪步（不是轉一下又轉回來——那樣位置抖了但攻擊模式沒變，防不了失效）。
        self.alternate_face = alternate_face        # True = 每 swap_every 次挪步換一次攻擊方向
        self.swap_every = max(1, int(swap_every))
        self._cycle_count = 0
        # 擬人化：看哪邊「動靜多」（怪會走動/有動畫）就朝哪邊打，而不是機械輪流。
        # 不需要怪物模板——比較角色左右兩側的畫格差異量即可；判斷不了時退回輪流換邊。
        self.smart_face = smart_face and not dry_run
        self._smart_ready = False
        self._np = None                             # numpy（延遲載入）
        self._smart_next = 0.0                      # 下次動靜檢查的時間
        self._next_attack = 0.0                     # 下次攻擊時間（帶隨機抖動）
        # 調參 UI（tools/tuner.py）寫的檔；執行中每 2 秒偵測異動並套用，免重啟
        self.tuning_path = tuning_path
        self.max_step_seconds = None                # 每步按方向鍵秒數上限（量平台後由 UI 設定）
        self._tuning_mtime = 0.0
        self._tuning_next_check = 0.0
        self._tm = None                             # TemplateMatcher（延遲建立）
        self._dispel_roi = None                     # buff 列 ROI；None = 右上角自動推算
        self._dispel_last = 0.0
        self._cap = None                            # ScreenCapture（延遲建立）
        self._mmloc = None                          # MinimapLocator
        self._edge_lo = None
        self._edge_hi = None
        self._edge_center = None                    # 起始中心 x：移動一律往這裡回歸（小平台最安全）
        self._last_x = None                         # 最近一次成功讀到的玩家 x（抓不到時的後備）
        self.max_jump = max(25, self.edge_margin * 4)  # x 一次跳超過這麼多 px 視為誤判、丟棄
        self.refocus = refocus                      # 焦點被搶走時自動切回楓之谷
        self.dry_run = dry_run
        self._hwnd = None
        self._window_keyword = ""
        self._was_fg = True                         # 上一圈楓之谷是否在前景（refocus 模式用）
        self.focus_grace = 1.0                      # 焦點離開超過幾秒才算『你真的切走』（過濾短暫閃視窗）
        self._fg_lost_at = None                     # 焦點何時離開楓之谷（None=在前景）
        self._pause_announced = False               # 是否已印過『暫停』（避免每圈洗版）
        self._patrol_pos = 0                        # 目前離出發點幾步（正=右、負=左）
        self._patrol_dir = 1                        # 目前巡邏方向（+1 右 / -1 左）
        self._pending_face = None                   # 暫停期間記下你最後按的方向，恢復時採用

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
        try:
            # 按住時間帶隨機（人不會每次都按一樣久）；夾住下限避免負值
            time.sleep(max(0.0, hold if hold is not None else random.uniform(0.04, 0.10)))
        finally:
            self._up(key)   # 無論如何都要放開——keyup 掉了角色會一直走到掉下平台

    def _release_moves(self):
        """保險絲：補送左右鍵 keyup。

        SendInput 的 keyup 偶爾會被掉包（遊戲掉幀/焦點切換瞬間），方向鍵
        就會卡在按住狀態——角色自己一直走、直到走出平台，任何位置防護都
        推不贏一顆按死的鍵。在每次送過移動鍵之後都補放開一次。
        """
        if self.dry_run:
            return
        for k in ("left", "right"):
            _send_key(k, keyup=True)

    def _attack_once(self):
        """點一下攻擊鍵。連續呼叫 = 連續攻擊，比『按住不放』可靠——
        不管遊戲是『按一下打一下』還是『按住連打』都吃得到，移動後也能無縫接回。"""
        self._tap(self.attack_key, hold=random.uniform(0.04, 0.09))

    def _attack_hold_tick(self, now):
        """按住模式：壓著攻擊鍵；到補壓時間就快速鬆壓一次（防按住狀態被吃掉）。"""
        if not self._atk_held:
            self._down(self.attack_key)
            self._atk_held = True
            self._atk_refresh = now + random.uniform(2.0, 4.0)
        elif now >= self._atk_refresh:
            self._up(self.attack_key)
            time.sleep(0.03)
            self._down(self.attack_key)
            self._atk_refresh = now + random.uniform(2.0, 4.0)

    def _attack_release(self):
        """放開按住的攻擊鍵（暫停/切視窗/移動前必呼叫，絕不卡著 Ctrl）。"""
        if self._atk_held:
            self._up(self.attack_key)
            self._atk_held = False

    def _reface(self, face=None):
        """把面向定到指定方向（極短按，只轉身不走路）。未指定 = 目前攻擊方向。"""
        self._tap(face or self.attack_facing, hold=0.03)

    def _next_face(self):
        """這一次挪步後要朝哪一邊打。

        優先看「哪邊怪多」（動靜偵測）；判斷不了才用輪流換邊——防攻擊失效
        不能只靠位移，實測有效的人工節奏是「移動一下＋換邊打」。
        """
        self._cycle_count += 1
        if self.alternate_face and self._cycle_count % self.swap_every == 0:
            fallback = "left" if self.attack_facing == "right" else "right"
        else:
            fallback = self.attack_facing
        face, counts = self._pick_face_by_motion(fallback)
        if counts is not None and face != fallback:
            pass  # 動靜壓過輪流：訊息由呼叫端印
        return face

    # ---- 調參檔熱載入（tools/tuner.py 的 UI 寫檔，這裡即時套用）----
    _TUNABLE = {
        # 檔案鍵名 → (屬性名, 轉型)
        "attack_interval": ("attack_interval", float),
        "interval_min": ("interval_min", float),
        "interval_max": ("interval_max", float),
        "move_time": ("move_time", float),
        "max_step_seconds": ("max_step_seconds", float),
        "edge_margin": ("edge_margin", int),
        "guard_interval": ("guard_interval", float),
        "swap_every": ("swap_every", int),
        "alternate_face": ("alternate_face", bool),
        "smart_face": ("smart_face", bool),
        "dispel_interval": ("dispel_interval", float),
    }

    def _apply_tuning(self, data):
        """套用調參 dict，回傳實際變更的 {鍵: 新值}。"""
        changed = {}
        for key, (attr, cast) in self._TUNABLE.items():
            if key not in data or data[key] is None:
                continue
            try:
                val = cast(data[key])
            except (TypeError, ValueError):
                continue
            if getattr(self, attr, None) != val:
                setattr(self, attr, val)
                changed[key] = val
        if self.interval_min > self.interval_max:
            self.interval_min, self.interval_max = self.interval_max, self.interval_min
        self.swap_every = max(1, self.swap_every)
        # 量平台得到的安全上限：每步按鍵秒數不得超過（防走過頭掉平台）
        if self.max_step_seconds and self.move_time > self.max_step_seconds:
            self.move_time = self.max_step_seconds
            changed["move_time(受max_step上限)"] = self.move_time
        # 邊界安全界改了 → 以現有中心重算上下界
        if "edge_margin" in changed and self._edge_center is not None:
            self._edge_lo = self._edge_center - self.edge_margin
            self._edge_hi = self._edge_center + self.edge_margin
        return changed

    def _maybe_reload_tuning(self):
        """每 2 秒看一次調參檔有沒有變，有就套用（執行中即時生效、免重啟）。"""
        if not self.tuning_path:
            return
        now = time.time()
        if now < self._tuning_next_check:
            return
        self._tuning_next_check = now + 2.0
        try:
            mtime = os.path.getmtime(self.tuning_path)
        except OSError:
            return
        if mtime == self._tuning_mtime:
            return
        self._tuning_mtime = mtime
        try:
            import yaml
            with open(self.tuning_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"[警告] 調參檔讀取失敗：{e}")
            return
        changed = self._apply_tuning(data)
        if changed:
            desc = "、".join(f"{k}={v}" for k, v in changed.items())
            print(f"  ⚙ 已套用調參：{desc}")

    # ---- 擬人化：動靜偵測（哪邊怪多打哪邊，不需怪物模板）----
    def _init_smart(self):
        """延遲初始化動靜偵測：需要 mss 擷取＋numpy。失敗 → 退回輪流換邊。"""
        if self._smart_ready:
            return True
        try:
            if ROOT not in sys.path:
                sys.path.insert(0, ROOT)
            import numpy as np
            from src.capture import ScreenCapture
            self._np = np
            if self._cap is None:
                self._cap = ScreenCapture(backend="mss", window_title=self._window_keyword)
            self._smart_ready = True
            return True
        except Exception as e:
            print(f"[提示] 動靜偵測初始化失敗（缺 numpy/mss？{e}）→ 改用輪流換邊")
            self.smart_face = False
            return False

    def _motion_sides(self):
        """量角色左右兩側的畫面動靜量（怪走動/動畫產生的畫格差異像素數）。

        排除：畫面上下緣的 UI 帶、中央一條（自己角色與技能動畫的動靜）。
        """
        np = self._np
        f1 = self._cap.grab()
        time.sleep(0.15)
        f2 = self._cap.grab()
        if f1 is None or f2 is None or f1.shape != f2.shape:
            return None
        h, w = f1.shape[:2]
        y0, y1 = int(h * 0.20), int(h * 0.85)          # 去掉上下 UI
        cx, dead = w // 2, int(w * 0.08)               # 中央排除帶（自己）
        diff = np.abs(f1[y0:y1].astype(np.int16) - f2[y0:y1].astype(np.int16)).max(axis=2)
        moving = diff > 20
        left = int(moving[:, :cx - dead].sum())
        right = int(moving[:, cx + dead:].sum())
        return left, right

    def _pick_face_by_motion(self, fallback):
        """回傳 (要面向的邊, (左動靜, 右動靜) 或 None)。

        單邊動靜明顯較多（>1.25 倍）才換過去；兩邊差不多或都沒動靜
        （怪被清光）→ 用 fallback（輪流換邊，兼顧防失效）。
        """
        if not self.smart_face or not self._init_smart():
            return fallback, None
        try:
            sides = self._motion_sides()
        except Exception:
            return fallback, None
        if sides is None:
            return fallback, None
        left, right = sides
        if left + right < 400:                         # 兩邊都靜悄悄：沒怪
            return fallback, sides
        if left > right * 1.25:
            return "left", sides
        if right > left * 1.25:
            return "right", sides
        return fallback, sides

    def _set_face_after_step(self, facing_now, target_face):
        """挪步結束後把面向定到 target_face；回傳給訊息用的後綴字串。

        facing_now：這一步結束時角色實際面對的方向（走路會轉向；原地跳不變）。
        """
        swapped = target_face != self.attack_facing
        if facing_now != target_face:
            self._reface(target_face)
        self.attack_facing = target_face
        if swapped:
            return f"，換邊改朝{'左' if target_face == 'left' else '右'}打"
        return f"，續朝{'左' if target_face == 'left' else '右'}打"

    # ---- edge-guard：用小地圖真實 x 判斷邊界 ----
    @staticmethod
    def _load_settings():
        """讀 config/settings.yaml（沒有就用 example）。"""
        import yaml
        cfgpath = os.path.join(ROOT, "config", "settings.yaml")
        if not os.path.exists(cfgpath):
            cfgpath = os.path.join(ROOT, "config", "settings.example.yaml")
        with open(cfgpath, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _init_edge_guard(self):
        """延遲載入辨識模組、建立擷取器與小地圖定位器。缺套件會丟例外，由呼叫端接。"""
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from src.capture import ScreenCapture   # 需要 mss
        from src.vision import MinimapLocator    # 需要 opencv/numpy
        cfg = self._load_settings()
        if self._cap is None:
            self._cap = ScreenCapture(backend="mss", window_title=self._window_keyword)
        self._mmloc = MinimapLocator(cfg["vision"]["minimap"])

    def _try_enable_edge_guard(self, retries=3, verbose=False):
        """嘗試（重新）啟用 edge-guard；成功回傳 True。

        啟動時小地圖可能還沒開/在切圖，一次失敗不該永久降級成盲走——
        盲走正是「偶爾掉出平台」的元凶，之後每次挪步前都會再試。
        """
        if self._edge_guard_broken or self.dry_run:
            return False
        if self._mmloc is None:
            try:
                self._init_edge_guard()
            except Exception as e:
                print(f"[警告] edge-guard 初始化失敗（缺 opencv/mss？{e}）→ 改用一般巡邏")
                self._edge_guard_broken = True
                self.edge_guard = False
                return False
        sx = self._player_x(retries=retries, gap=0.15, fresh=True)
        if sx is None:
            if verbose:
                print("[警告] edge-guard 暫時抓不到小地圖玩家點 → 先用一般巡邏，"
                      "每次挪步前會自動重試（請確認小地圖有開）")
            self.edge_guard = False
            return False
        self._last_x = sx
        self._edge_center = sx
        self._edge_lo = sx - self.edge_margin
        self._edge_hi = sx + self.edge_margin
        self._edge_center_y = self._last_y
        self._y_off_count = 0
        self.edge_guard = True
        print(f"  ✓ edge-guard 啟用：中心 x={sx}"
              f"（每 {self.guard_interval:.1f}s 巡界，出界即推回；被擊退也擋得住）")
        return True

    def _edge_guard_tick(self):
        """常駐位置警衛：出安全界立刻推回；高度變了（掉層）就以新位置重設中心。

        怪物碰撞擊退不需要按鍵就會推動角色，只靠挪步時檢查（40~55 秒一次）
        中間有大段盲區——細樹枝平台上累積十幾秒就出界了。
        """
        if not self.edge_guard or self._edge_center is None:
            return
        x = self._player_x(retries=1, gap=0.0)
        if x is None:
            return
        y = self._last_y
        # 掉層/被打下平台：高度連續兩次偏離 → 舊中心已無意義，以現在位置重設
        if (self._edge_center_y is not None and y is not None
                and abs(y - self._edge_center_y) >= 3):
            self._y_off_count += 1
            if self._y_off_count >= 2:
                print(f"  ⚠ 高度變了（y {self._edge_center_y}→{y}，掉層/被打下平台？）"
                      "→ 以現在位置重設中心")
                self._edge_center, self._edge_center_y = x, y
                self._edge_lo = x - self.edge_margin
                self._edge_hi = x + self.edge_margin
                self._y_off_count = 0
            return
        self._y_off_count = 0
        if self._edge_lo <= x <= self._edge_hi:
            return
        d = "right" if x < self._edge_center else "left"
        self._tap(d, hold=max(0.12, min(self.move_time, 0.25)))
        if d != self.attack_facing:
            self._reface(self.attack_facing)
        self._release_moves()   # 保險絲：防 keyup 掉包卡鍵
        print(f"  ⛑ 滑出安全界（x={x}，中心 {self._edge_center}±{self.edge_margin}，"
              f"被擊退？）→ 往{'右' if d == 'right' else '左'}推回")

    # ---- dispel-buff：偵測「要點掉的 buff」（如速度激發）並右鍵移除 ----
    def _init_dispel(self):
        """建立模板匹配器並載入 buff 圖示。沒有模板／缺套件 → 停用並提示。"""
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from src.capture import ScreenCapture     # 需要 mss
        from src.vision import TemplateMatcher    # 需要 opencv/numpy
        cfg = self._load_settings()
        bd = (cfg.get("vision", {}) or {}).get("buff_dispel", {}) or {}
        self._dispel_roi = bd.get("roi")
        tmdir = os.path.join(ROOT, bd.get("template_dir", "assets/templates/buffs"))
        self._tm = TemplateMatcher(threshold=float(bd.get("match_threshold", 0.80)))
        try:
            self._tm.load_directory(tmdir)
        except FileNotFoundError:
            pass
        if not self._tm.template_names:
            print(f"[提示] --dispel-buff：{tmdir} 沒有 buff 圖示截圖 → 停用。"
                  "（把「速度激發」等要點掉的 buff 圖示截圖放進去即可啟用）")
            self.dispel_buff = False
            return
        if self._cap is None:
            self._cap = ScreenCapture(backend="mss", window_title=self._window_keyword)
        print(f"  ✓ dispel-buff 啟用：監看 {len(self._tm.template_names)} 個 buff 圖示，"
              f"每 {self.dispel_interval:.0f} 秒檢查一次，偵測到就右鍵點掉")

    def _dispel_check(self):
        """右上角出現要點掉的 buff（如速度激發）→ 右鍵移除。

        速度激發會讓移速變快，校準好的步伐全部走過頭，一動就掉出平台。
        任何例外都吞掉（絕不讓掛機主迴圈因此中斷）。
        """
        try:
            frame = self._cap.grab()
            if frame is None:
                return
            h, w = frame.shape[:2]
            roi = self._dispel_roi or [int(w * 0.55), 0, w - int(w * 0.55), int(h * 0.25)]
            l, t, rw, rh = (int(v) for v in roi)
            region = frame[t:t + rh, l:l + rw]
            if region.size == 0:
                return
            best = None
            for name in self._tm.template_names:
                for m in self._tm.match(region, name):
                    if best is None or m.score > best.score:
                        best = m
            if best is None:
                return
            # frame 是視窗相對座標 → 加上視窗左上角換算成螢幕座標
            win = self._cap.locate_window()
            ox, oy = (win.left, win.top) if win is not None else (0, 0)
            cx, cy = best.center
            print(f"  ✂ 偵測到要點掉的 buff「{best.name}」→ 右鍵移除")
            _right_click_at(ox + l + cx, oy + t + cy)
        except Exception:
            pass

    def _player_x(self, retries=5, gap=0.12, fresh=False):
        """讀玩家在小地圖上的 x（只讀畫面，不送鍵、不搶焦點）。抓不到回 None。

        小地圖玩家點會閃爍、又常有別的黃色標記混淆，所以：
        - 連抓多張（跨過閃爍週期）。
        - 有上次已知 x 且非 fresh：挑「最接近上次 x」的塊；若最近的都跳超過
          max_jump（掛機不可能瞬移）→ 判定誤判、不採用（避免像 x=59 那種亂讀害你掉平台）。
        - fresh=True 或沒有上次位置：取最大塊（第一次定位 / 手動換點後重新定位用）。
        """
        for i in range(max(1, retries)):
            try:
                frame = self._cap.grab()
                cands = self._mmloc.locate_player_candidates(frame)
            except Exception:
                cands = []
            if cands:
                if fresh or self._last_x is None:
                    self._last_y = cands[0][1]
                    return cands[0][0]                       # 最大塊
                best = min(cands, key=lambda b: abs(b[0] - self._last_x))
                if abs(best[0] - self._last_x) <= self.max_jump:
                    self._last_y = best[1]
                    return best[0]
                # 最接近的都跳太遠 → 這張可能是誤判/雜訊，再抓一張
            if i < retries - 1:
                time.sleep(gap)
        return None

    def _user_intervened(self):
        """使用者是否「實體」按著方向鍵（攻擊鍵是我們自己在點，不算介入）。

        以低階鉤子的實體按鍵狀態為準：你的手優先、絕不干擾。實體沒按、
        但系統狀態卻顯示按住 = 我們注入的 keyup 掉包（按鍵卡死）→ 就地
        補放開（不然角色會一直走到掉出平台）。鉤子裝不起來時退回舊行為。
        """
        if self._phys is not None and self._phys.ok:
            if any(self._phys.state.get(_VK[k], False) for k in self.INTERVENE_KEYS):
                if any(_pressed(_VK[k]) for k in self.INTERVENE_KEYS):
                    return True   # 實體按住 → 真的介入，完全不出手
                # 實體=按住但系統=放開：鉤子狀態過期（callback 被系統解掛？）
                # → 重置，避免永久誤判成介入而卡在暫停
                for vk in self._phys.state:
                    self._phys.state[vk] = False
                return False
            stuck = [k for k in self.INTERVENE_KEYS if _pressed(_VK[k])]
            if stuck and not self.dry_run:
                for k in stuck:
                    if k in _SCAN:
                        _send_key(k, keyup=True)
                now = time.time()
                if now - self._stuck_note_ts > 2.0:
                    print(f"  🔧 方向鍵卡死（{'/'.join(stuck)} keyup 掉包）→ 已強制放開")
                    self._stuck_note_ts = now
            return False
        return any(_pressed(_VK[k]) for k in self.INTERVENE_KEYS)

    def _user_direction(self):
        """使用者當下實體按的是左還右（記住你手動換邊的方向）；都沒按回 None。"""
        if self._phys is not None and self._phys.ok:
            if self._phys.state.get(_VK["left"], False):
                return "left"
            if self._phys.state.get(_VK["right"], False):
                return "right"
            return None
        if _pressed(_VK["left"]):
            return "left"
        if _pressed(_VK["right"]):
            return "right"
        return None

    def _game_is_foreground(self):
        return bool(self._hwnd) and _user32.GetForegroundWindow() == self._hwnd

    def _ensure_foreground(self):
        """確認楓之谷是前景視窗；不是就切回來。回傳最終是否在前景。

        視窗若不見了（例如遊戲重開）→ 依標題重新尋找。這是『跳出其他視窗會干擾』
        的解法：焦點被搶走時把楓之谷切回來，真的切不回來就回報 False，主迴圈
        會跳過這一圈不送鍵——避免把 Ctrl 打進別的視窗。
        """
        if self.dry_run:
            return True
        if not self._hwnd or not _user32.IsWindow(self._hwnd):
            self._hwnd = _find_window(self._window_keyword)  # 視窗換了 handle → 重找
            if not self._hwnd:
                return False
        if _user32.GetForegroundWindow() == self._hwnd:
            return True
        # 被別的視窗搶走 → 空按 ALT 解前景鎖再切回楓之谷
        _user32.keybd_event(0x12, 0, 0, 0)
        _user32.keybd_event(0x12, 0, 2, 0)
        _user32.ShowWindow(self._hwnd, 9)  # SW_RESTORE
        _user32.SetForegroundWindow(self._hwnd)
        time.sleep(0.05)
        return _user32.GetForegroundWindow() == self._hwnd

    def _move(self):
        """有邊界的巡邏：往目前方向挪『一小步』，走到 ±patrol_steps 步就折返。

        角色只在小範圍內來回掃（不是回到同一點，也不會愈走愈遠走出小平台）。
        每次只走一步 → 位置真的有變（解決定點攻擊無效），但被 patrol_steps 綁住
        （解決平台很小）。走完直接 return，主迴圈下一圈立刻接回連點攻擊。
        """
        if not self.enable_move:
            return
        self._attack_release()   # 走位前放開攻擊鍵，避免 Ctrl+方向鍵組合
        # edge-guard 想開卻還沒開成（啟動時小地圖沒就緒）→ 每次挪步前重試
        if self._edge_guard_wanted and not self.edge_guard:
            self._try_enable_edge_guard(retries=3)
        target = self._next_face()   # 這一輪之後要朝哪邊打（防失效的「換邊」在這裡）
        if self.shuffle:
            # 小碎步：去程優先往中心（絕不先往外），回程走回來但稍短
            if self.edge_guard and self._edge_center is not None:
                x = self._player_x(retries=2, gap=0.1)
                ref = x if x is not None else self._last_x
                if ref is not None and abs(ref - self._edge_center) > 1:
                    d1 = "right" if ref < self._edge_center else "left"
                else:
                    d1 = "right" if self._patrol_dir > 0 else "left"
                    self._patrol_dir *= -1
            else:
                d1 = "right" if self._patrol_dir > 0 else "left"
                self._patrol_dir *= -1
            d2 = "left" if d1 == "right" else "right"
            t1 = self.move_time + random.uniform(-0.01, 0.01)
            self._tap(d1, hold=t1)
            time.sleep(random.uniform(0.08, 0.18))
            self._tap(d2, hold=t1 * 0.8)     # 回程稍短：淨位移往 d1（中心側）一點點
            note = self._set_face_after_step(d2, target)
            arrow = "→右→左" if d1 == "right" else "→左→右"
            print(f"  ↻ 小碎步 {arrow}（有水平位移、淨移≈0）{note} → 接回攻擊")
            time.sleep(random.uniform(0.05, 0.12))
            return
        if self.jump_in_place:
            # 原地跳：直上直下、水平不位移 → 平台再小也掉不出去（前提是跳也算移動）
            self._tap(self.jump_key, hold=random.uniform(0.04, 0.08))
            note = self._set_face_after_step(self.attack_facing, target)
            print(f"  ↻ 原地跳一下（不位移、掉不出平台）{note} → 接回攻擊")
            time.sleep(random.uniform(0.1, 0.2))
            return
        if self.edge_guard:
            self._move_edge_guarded(target)
            return
        direction = "right" if self._patrol_dir > 0 else "left"
        t = self.move_time
        self._tap(direction, hold=t + random.uniform(-0.01, 0.01))
        self._patrol_pos += self._patrol_dir
        # 到單邊上限 → 反向，讓下一步往回走
        if self._patrol_pos >= self.patrol_steps:
            self._patrol_dir = -1
        elif self._patrol_pos <= -self.patrol_steps:
            self._patrol_dir = 1
        note = self._set_face_after_step(direction, target)
        arrow = "→右" if direction == "right" else "←左"
        print(f"  ↻ 巡邏挪一步 {arrow}（位置 {self._patrol_pos:+d}/±{self.patrol_steps}）{note} → 接回攻擊")
        time.sleep(random.uniform(0.05, 0.12))

    def _move_edge_guarded(self, target_face):
        """往起始中心回歸（不走去邊界）——小平台最安全的移動。

        - 偏離中心 → 往中心那側走一步（把自己拉回來，永遠不往外走）。
        - 已在中心（±1px）→ 只做一步極小交替，位置變一下夠重置定點失效即可。
        - 抓不到當下 x → 用最近已知 x 判斷（掛機站著不動，位置幾乎沒變），照樣移動不定在原地。
        - 完全沒讀到過 → 保守極小交替一步。
        挪完把面向定到 target_face（換邊打），持續到下一次挪步。
        """
        x = self._player_x()
        if x is not None:
            self._last_x = x
        ref = x if x is not None else self._last_x
        c = self._edge_center

        if ref is None or c is None:
            direction = "right" if self._patrol_dir > 0 else "left"
            self._patrol_dir *= -1
            self._tap(direction, hold=min(self.move_time, 0.1))
            note = self._set_face_after_step(direction, target_face)
            print(f"  ↻ 回中巡邏（抓不到玩家點，保守極小交替）{note} → 接回攻擊")
            time.sleep(random.uniform(0.05, 0.12))
            return

        if ref > c + 1:
            direction = "left"           # 偏右 → 往左回中心
        elif ref < c - 1:
            direction = "right"          # 偏左 → 往右回中心
        else:
            direction = "right" if self._patrol_dir > 0 else "left"
            self._patrol_dir *= -1       # 已在中心：極小交替一步
        self._tap(direction, hold=self.move_time + random.uniform(-0.01, 0.01))
        note = self._set_face_after_step(direction, target_face)
        arrow = "→右" if direction == "right" else "←左"
        src = f"x={x}" if x is not None else f"x≈{ref}(最近已知)"
        print(f"  ↻ 回中巡邏 {arrow}（{src} 中心={c}）{note} → 接回攻擊")
        time.sleep(random.uniform(0.05, 0.12))

    def run(self, window_keyword):
        hwnd = _find_window(window_keyword)
        if hwnd is None:
            print(f"[錯誤] 找不到視窗標題含「{window_keyword}」的遊戲。請先開好遊戲。")
            return 1
        self._hwnd = hwnd
        self._window_keyword = window_keyword
        # 實體按鍵鉤子：區分「你的手」和「我們注入的鍵」，你的操作永遠優先
        if not self.dry_run:
            self._phys = _PhysicalKeys(tuple(_VK[k] for k in self.INTERVENE_KEYS))
            time.sleep(0.3)
            if self._phys.ok:
                print("  ✓ 實體按鍵鉤子啟用：手動操作優先，注入卡鍵自動解除")
            else:
                print("  （實體按鍵鉤子裝不起來 → 介入判斷退回舊模式）")
        if not self.dry_run:
            fg_ok = False
            for _ in range(5):     # UAC 彈窗剛關/焦點鎖時第一次常失敗，多試幾次
                if _foreground(hwnd):
                    fg_ok = True
                    break
                time.sleep(0.8)
            if not fg_ok:
                # 不再直接退出：主迴圈本來就會等遊戲回到前景才送鍵
                print("[警告] 暫時無法把遊戲切到前景 → 照常啟動；"
                      "請自行點一下遊戲視窗，偵測到前景後會自動開始。")

        # edge-guard：建立擷取/定位器，記下起始 x 當中心、設安全左右界。
        # 啟動時抓不到玩家點（小地圖沒開/在切圖）不放棄——之後每次挪步前重試。
        if self.edge_guard and not self.dry_run:
            self._try_enable_edge_guard(retries=12, verbose=True)

        # dispel-buff：右上角出現「要點掉的 buff」（如速度激發）→ 右鍵移除
        if self.dispel_buff and not self.dry_run:
            try:
                self._init_dispel()
            except Exception as e:
                print(f"[警告] dispel-buff 初始化失敗（缺 opencv/mss？{e}）→ 停用")
                self.dispel_buff = False

        swap_desc = ("挪步後換邊打" if self.alternate_face and self.swap_every == 1 else
                     f"每 {self.swap_every} 次挪步換邊打" if self.alternate_face else
                     "固定單邊打")
        if self.smart_face:
            swap_desc = f"哪邊怪多打哪邊（動靜偵測，每 8~14 秒看一眼；判斷不了才{swap_desc}）"
        if self.edge_guard:
            move_desc = (f"每 {self.interval_min:.0f}~{self.interval_max:.0f} 秒巡邏，"
                         f"用小地圖真實 x 到邊界折返（不會走出平台），{swap_desc}")
        else:
            move_desc = "不移動" if not self.enable_move else \
                f"每 {self.interval_min:.0f}~{self.interval_max:.0f} 秒巡邏挪一步（範圍 ±{self.patrol_steps} 步，{swap_desc}）"
        atk_desc = (f"按住「{self.attack_key}」攻擊（每 2~4 秒補壓防斷）" if self.hold_attack
                    else f"連點「{self.attack_key}」攻擊（每 {self.attack_interval:.2f}s 一下）")
        print("=" * 56)
        print(f" {atk_desc}；{move_desc}")
        print(f" 接手換邊=方向鍵暫停→轉向→Ctrl恢復 | 結束=F12")
        if self.dry_run:
            print(" [dry-run] 只印節奏、不送實體按鍵")
        print("=" * 56)

        state = "RUNNING"
        cycle_start = time.time()
        wait = self._next_interval()
        self._next_attack = 0.0
        # 開場不強制轉向——沿用你啟動時角色本來面對的方向。attack_facing 只是假設值，
        # 你手動換邊（暫停→轉向→Ctrl 恢復）後會自動更新成你最後面對的方向。
        _f = '左' if self.attack_facing == 'left' else '右'
        if self.enable_move:
            print(f"  開始攻擊（假設朝{_f}，手動換邊會跟著更新）；下次移動：{wait:.0f} 秒後")
        else:
            print(f"  開始攻擊（假設朝{_f}，手動換邊會跟著更新，不移動）")
        try:
            while True:
                if _pressed(_VK[self.QUIT_KEY]):
                    print("\n[F12] 結束。")
                    break

                if state == "RUNNING":
                    self._maybe_reload_tuning()   # 調參 UI 改了值就即時套用
                    if self._user_intervened():
                        state = "PAUSED"
                        self._attack_release()   # 你接手時絕不卡著攻擊鍵
                        self._pending_face = self._user_direction() or self._pending_face
                        print("  ⏸ 你接手了 → 暫停攻擊。轉到你要打的方向後按 Ctrl 恢復"
                              "（會朝你最後面對的那邊打）…")
                        while self._user_intervened():   # 等你放開方向鍵，順便記住最後方向
                            d = self._user_direction()
                            if d:
                                self._pending_face = d
                            time.sleep(self.POLL)
                        continue

                    # 送鍵前先確認楓之谷在前景。不在的話：
                    #   refocus=True  → 切回楓之谷再打（別的視窗彈出時不中斷）
                    #   refocus=False → 暫停不送鍵（你切走打字時它讓開、絕不打進你的視窗）
                    if not self.dry_run and not self._game_is_foreground():
                        if self.refocus:
                            if self._was_fg:
                                print("  ⚠ 焦點被別的視窗搶走，切回楓之谷…")
                            ok = self._ensure_foreground()
                            self._was_fg = ok
                            if not ok:
                                self._attack_release()   # 切不回去就先放開攻擊鍵
                                time.sleep(0.15)
                                continue
                            print("  ✓ 已切回楓之谷，繼續攻擊")
                        else:
                            # 不搶焦點：完全不送鍵、讓開。焦點離開『超過 focus_grace 秒』
                            # 才當成你真的切走並印訊息——短暫閃一下的視窗(bun/通知)直接忽略，
                            # 因為分不出是誰搶的焦點，就用『有沒有離開夠久』來判斷。
                            self._attack_release()   # 你切走打字時絕不按著 Ctrl（避免組合鍵）
                            now = time.time()
                            if self._fg_lost_at is None:
                                self._fg_lost_at = now
                            if (not self._pause_announced
                                    and now - self._fg_lost_at >= self.focus_grace):
                                print(f"  ⏸ 你切到別的視窗（打字？）超過 {self.focus_grace:.0f} 秒 "
                                      "→ 暫停攻擊；切回楓之谷自動恢復")
                                self._pause_announced = True
                            self._was_fg = False
                            time.sleep(0.12)
                            continue
                    else:
                        if not self._was_fg:   # 剛從別的視窗切回楓之谷
                            if self._pause_announced:
                                print("  ▶ 回到楓之谷 → 自動恢復攻擊")
                            # 短暫閃一下就回來的：靜默恢復，不印任何東西
                        self._was_fg = True
                        self._fg_lost_at = None
                        self._pause_announced = False

                    # buff 檢查（速度激發等會讓步伐走過頭 → 掉出平台，要馬上點掉）
                    if self.dispel_buff and time.time() - self._dispel_last >= self.dispel_interval:
                        self._dispel_check()
                        self._dispel_last = time.time()

                    # 常駐位置警衛：怪物擊退不按鍵也會推動角色，出界立刻推回
                    if self.edge_guard and time.time() >= self._guard_next:
                        self._edge_guard_tick()
                        self._guard_next = time.time() + self.guard_interval

                    # 擬人化：每隔 8~14 秒看一眼哪邊動靜多（= 怪在哪邊），怪明顯
                    # 移到另一邊就跟著轉過去打——像人在顧螢幕，不是機械輪流。
                    if self.smart_face and time.time() >= self._smart_next:
                        face, sides = self._pick_face_by_motion(self.attack_facing)
                        if sides is not None and face != self.attack_facing:
                            self._reface(face)
                            self.attack_facing = face
                            l, r = sides
                            print(f"  ⇄ {'左' if face == 'left' else '右'}邊動靜較多"
                                  f"（左{l}/右{r}）→ 換邊打")
                        self._smart_next = time.time() + random.uniform(8.0, 14.0)

                    now = time.time()
                    if self.enable_move and now - cycle_start >= wait:
                        self._move()
                        self._release_moves()   # 保險絲：防 keyup 掉包卡鍵
                        cycle_start = time.time()
                        wait = self._next_interval()
                        self._next_attack = 0.0  # 移動後立刻接回攻擊（下一圈馬上點）
                        print(f"  （下次移動：{wait:.0f} 秒後）")
                    elif self.hold_attack:
                        self._attack_hold_tick(now)
                    elif now >= self._next_attack:
                        self._attack_once()
                        # 攻擊間隔帶抖動、偶爾停頓一下——人不會用碼表點鍵
                        gap = self.attack_interval * random.uniform(0.85, 1.30)
                        if random.random() < 0.02:
                            gap += random.uniform(0.6, 1.6)
                        self._next_attack = time.time() + gap

                elif state == "PAUSED":
                    d = self._user_direction()      # 暫停期間持續記住你面對的方向
                    if d:
                        self._pending_face = d
                    if _pressed(_VK[self.RESUME_KEY]):
                        while _pressed(_VK[self.RESUME_KEY]):
                            time.sleep(self.POLL)
                        if self._pending_face:       # 採用你手動換到的方向
                            self.attack_facing = self._pending_face
                            self._pending_face = None
                        # 你手動接手可能把角色走到新位置 → edge-guard 以新位置重設安全界；
                        # 之前沒啟用成功的，也趁這時再試一次
                        if self._edge_guard_wanted and not self.edge_guard and not self.dry_run:
                            self._try_enable_edge_guard(retries=8)
                        elif self.edge_guard and not self.dry_run:
                            nx = self._player_x(retries=8, gap=0.15, fresh=True)
                            if nx is not None:
                                self._last_x = nx
                                self._edge_center = nx
                                self._edge_lo = nx - self.edge_margin
                                self._edge_hi = nx + self.edge_margin
                                self._edge_center_y = self._last_y
                                self._y_off_count = 0
                                print(f"    edge-guard 重新定位：中心 x={nx}（往這裡回歸）")
                        state = "RUNNING"
                        cycle_start = time.time()
                        wait = self._next_interval()
                        self._next_attack = 0.0
                        _f = '左' if self.attack_facing == 'left' else '右'
                        print(f"  ▶ 已恢復攻擊（朝{_f}）。" +
                              (f"下次移動：{wait:.0f} 秒後" if self.enable_move else ""))

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
            if self._cap is not None:
                try:
                    self._cap.close()
                except Exception:
                    pass
        return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="按住鍵 + 定時左右動一下的簡易掛機節奏器")
    p.add_argument("--key", default="ctrl", choices=sorted(_SCAN),
                   help="攻擊鍵（預設 ctrl）；會被連續點擊")
    p.add_argument("--attack-interval", type=float, default=0.22,
                   help="兩次攻擊點擊的間隔秒數（預設 0.22，越小打越快）")
    p.add_argument("--hold-attack", action="store_true",
                   help="攻擊鍵改『按住不放』（遊戲按住連打的最大輸出），每 2~4 秒快速"
                        "鬆壓補一下防止按住狀態被遊戲吃掉；暫停/切視窗/移動前都會放開")
    p.add_argument("--interval-min", type=float, default=35.0,
                   help="兩次巡邏移動之間最短秒數（預設 35）；定點約 60 秒攻擊才會失效，故不用太頻繁")
    p.add_argument("--interval-max", type=float, default=50.0,
                   help="兩次巡邏移動之間最長秒數（預設 50，留在 60 秒失效前的餘裕）；每次在 min~max 隨機")
    p.add_argument("--move-time", type=float, default=0.18,
                   help="每一步按住方向鍵秒數（預設 0.18）；太短(<0.1)角色只會轉身不走路")
    p.add_argument("--patrol-steps", type=int, default=2,
                   help="從出發點往單邊最多走幾步就折返（預設 2）；平台越小設越小")
    p.add_argument("--face", choices=("left", "right"), default="left",
                   help="起始攻擊方向的『假設值』（預設 left）；開場不會強制轉向、沿用你角色"
                        "當下面向，之後手動換邊（暫停→轉向→Ctrl）會自動更新")
    p.add_argument("--edge-guard", action="store_true",
                   help="用小地圖真實 x 巡邏、到安全界折返（不會走出平台）；需 opencv/mss")
    p.add_argument("--edge-margin", type=int, default=6,
                   help="edge-guard 安全界：起始 x ± 這麼多小地圖 px（預設 6，平台窄設更小）")
    p.add_argument("--dispel-buff", action="store_true",
                   help="偵測右上角「要點掉的 buff」（如速度激發：移速變快步伐會走過頭而"
                        "掉出平台）並自動右鍵移除；圖示截圖放 assets/templates/buffs/，需 opencv/mss")
    p.add_argument("--dispel-interval", type=float, default=5.0,
                   help="dispel-buff 檢查間隔秒數（預設 5）")
    p.add_argument("--fixed-face", action="store_true",
                   help="固定朝同一邊打、不換邊（預設是每次挪步後換邊打——"
                        "定點防失效光位移不夠，要真的換邊；此旗標恢復舊行為）")
    p.add_argument("--swap-every", type=int, default=1,
                   help="每幾次挪步換一次邊（預設 1 = 每次都換；配合 interval 約 45~55 秒換一邊）")
    p.add_argument("--no-smart-face", action="store_true",
                   help="關閉「哪邊動靜多打哪邊」偵測（預設開啟：每 8~14 秒比較角色左右"
                        "兩側畫面動靜量，怪多的那邊就轉過去打；需 mss/numpy，失敗自動退回輪流換邊）")
    p.add_argument("--tuning", default=os.path.join("config", "tuning.yaml"),
                   help="調參檔路徑（預設 config/tuning.yaml）；配合 tools/tuner.py 的 UI "
                        "邊跑邊調，每 2 秒自動套用。檔案不存在則忽略")
    p.add_argument("--jump-in-place", action="store_true",
                   help="改用『原地跳』重定位（直上直下不位移，小平台不會掉下去；"
                        "注意：攻擊失效判定看水平位移，原地跳解不了失效）")
    p.add_argument("--shuffle", action="store_true",
                   help="改用『小碎步』重定位：往一邊走一小步立刻走回來——有真實水平位移"
                        "（解攻擊失效），淨位移≈0（細樹枝平台也安全）；去程優先往中心")
    p.add_argument("--jump-key", default="alt", choices=sorted(_SCAN),
                   help="跳躍鍵（預設 alt）")
    p.add_argument("--no-move", action="store_true",
                   help="完全不移動，只連點攻擊（只想定點刷攻擊時用）")
    p.add_argument("--no-refocus", action="store_true",
                   help="焦點被搶走時不自動切回楓之谷（只暫停送鍵，等它自己回前景）")
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
                    attack_interval=args.attack_interval, hold_attack=args.hold_attack,
                    patrol_steps=args.patrol_steps,
                    attack_facing=args.face, enable_move=not args.no_move,
                    refocus=not args.no_refocus, jump_in_place=args.jump_in_place,
                    shuffle=args.shuffle,
                    jump_key=args.jump_key, edge_guard=args.edge_guard,
                    edge_margin=args.edge_margin, dispel_buff=args.dispel_buff,
                    dispel_interval=args.dispel_interval,
                    alternate_face=not args.fixed_face, swap_every=args.swap_every,
                    smart_face=not args.no_smart_face,
                    tuning_path=(args.tuning if os.path.isabs(args.tuning)
                                 else os.path.join(ROOT, args.tuning)),
                    dry_run=args.dry_run)
    return hw.run(args.window)


if __name__ == "__main__":
    sys.exit(main())
