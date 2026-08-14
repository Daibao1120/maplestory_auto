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

    def __init__(self, attack_key="ctrl", interval_min=35.0, interval_max=50.0,
                 move_time=0.18, attack_interval=0.22, patrol_steps=2,
                 attack_facing="left", enable_move=True, refocus=True,
                 jump_in_place=False, jump_key="alt",
                 edge_guard=False, edge_margin=6, dry_run=False):
        self.attack_key = attack_key
        self.interval_min = float(interval_min)
        self.interval_max = float(interval_max)
        self.move_time = float(move_time)          # 每步按住方向鍵秒數（越小步伐越小）
        self.attack_interval = float(attack_interval)  # 兩次攻擊點擊的間隔（秒）
        self.patrol_steps = max(1, int(patrol_steps))  # 從中心往單邊最多走幾步就折返
        self.attack_facing = attack_facing          # 固定攻擊方向（left/right）；移動後轉回這邊
        self.enable_move = enable_move              # False = 完全不移動，只連點攻擊
        self.jump_in_place = jump_in_place          # True = 改用原地跳重定位（小平台不會掉下去）
        self.jump_key = jump_key                    # 跳躍鍵
        self.edge_guard = edge_guard                # True = 用小地圖真實 x 巡邏、到邊界折返（不會走出平台）
        self.edge_margin = int(edge_margin)         # 安全界：起始 x ± 這麼多小地圖 px
        self._cap = None                            # ScreenCapture（延遲建立）
        self._mmloc = None                          # MinimapLocator
        self._edge_lo = None
        self._edge_hi = None
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
        # 按住時間帶隨機（人不會每次都按一樣久）
        time.sleep(hold if hold is not None else random.uniform(0.04, 0.10))
        self._up(key)

    def _attack_once(self):
        """點一下攻擊鍵。連續呼叫 = 連續攻擊，比『按住不放』可靠——
        不管遊戲是『按一下打一下』還是『按住連打』都吃得到，移動後也能無縫接回。"""
        self._tap(self.attack_key, hold=random.uniform(0.04, 0.09))

    def _reface(self):
        """把面向轉回固定攻擊方向（極短按，只轉身不走路），這樣攻擊永遠朝同一邊。"""
        self._tap(self.attack_facing, hold=0.03)

    # ---- edge-guard：用小地圖真實 x 判斷邊界 ----
    def _init_edge_guard(self):
        """延遲載入辨識模組、建立擷取器與小地圖定位器。缺套件會丟例外，由呼叫端接。"""
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from src.capture import ScreenCapture   # 需要 mss
        from src.vision import MinimapLocator    # 需要 opencv/numpy
        import yaml
        cfgpath = os.path.join(ROOT, "config", "settings.yaml")
        if not os.path.exists(cfgpath):
            cfgpath = os.path.join(ROOT, "config", "settings.example.yaml")
        with open(cfgpath, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self._cap = ScreenCapture(backend="mss", window_title=self._window_keyword)
        self._mmloc = MinimapLocator(cfg["vision"]["minimap"])

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
                    return cands[0][0]                       # 最大塊
                best = min(cands, key=lambda b: abs(b[0] - self._last_x))
                if abs(best[0] - self._last_x) <= self.max_jump:
                    return best[0]
                # 最接近的都跳太遠 → 這張可能是誤判/雜訊，再抓一張
            if i < retries - 1:
                time.sleep(gap)
        return None

    def _user_intervened(self):
        """使用者是否按著方向鍵（攻擊鍵是我們自己在點，不算介入）。"""
        return any(_pressed(_VK[k]) for k in self.INTERVENE_KEYS)

    def _user_direction(self):
        """使用者當下按的是左還右（用來記住你手動換邊的方向）；都沒按回 None。"""
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
        if self.jump_in_place:
            # 原地跳：直上直下、水平不位移 → 平台再小也掉不出去（前提是跳也算移動）
            self._tap(self.jump_key, hold=random.uniform(0.04, 0.08))
            print("  ↻ 原地跳一下（不位移、掉不出平台）→ 接回攻擊")
            time.sleep(random.uniform(0.1, 0.2))
            return
        if self.edge_guard:
            self._move_edge_guarded()
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
        # 若剛剛走的方向跟固定攻擊方向相反，面向被帶偏了 → 轉回來，攻擊才不會射錯邊
        refaced = ""
        if direction != self.attack_facing:
            self._reface()
            refaced = f"，轉回{'左' if self.attack_facing == 'left' else '右'}攻擊"
        arrow = "→右" if direction == "right" else "←左"
        print(f"  ↻ 巡邏挪一步 {arrow}（位置 {self._patrol_pos:+d}/±{self.patrol_steps}）{refaced} → 接回攻擊")
        time.sleep(random.uniform(0.05, 0.12))

    def _move_edge_guarded(self):
        """用小地圖真實 x 決定方向、到安全界折返。

        抓不到當下 x 時**不再定在原地**（那會造成攻擊失效）：改用「最近一次已知 x」
        判斷方向照樣移動——掛機時你站著不動，上次位置幾乎就是現在位置，安全。
        真的從頭到尾沒讀到過才退成保守左右交替小步。
        """
        x = self._player_x()
        if x is not None:
            self._last_x = x
        ref = x if x is not None else self._last_x

        if ref is None:
            # 完全沒讀到過位置 → 保守：左右交替一小步（不累積漂移），至少讓角色動一下
            direction = "right" if self._patrol_dir > 0 else "left"
            self._patrol_dir *= -1
            self._tap(direction, hold=min(self.move_time, 0.12))
            if direction != self.attack_facing:
                self._reface()
            print(f"  ↻ 邊界巡邏（抓不到玩家點，保守交替小步 {'→右' if direction == 'right' else '←左'}）→ 接回攻擊")
            time.sleep(random.uniform(0.05, 0.12))
            return

        if ref <= self._edge_lo:
            self._patrol_dir = 1        # 太左 → 往右
        elif ref >= self._edge_hi:
            self._patrol_dir = -1       # 太右 → 往左
        direction = "right" if self._patrol_dir > 0 else "left"
        self._tap(direction, hold=self.move_time + random.uniform(-0.01, 0.01))
        if direction != self.attack_facing:
            self._reface()
        arrow = "→右" if direction == "right" else "←左"
        src = f"x={x}" if x is not None else f"x≈{ref}(最近已知)"
        print(f"  ↻ 邊界巡邏 {arrow}（{src} 安全界[{self._edge_lo}~{self._edge_hi}]）→ 接回攻擊")
        time.sleep(random.uniform(0.05, 0.12))

    def run(self, window_keyword):
        hwnd = _find_window(window_keyword)
        if hwnd is None:
            print(f"[錯誤] 找不到視窗標題含「{window_keyword}」的遊戲。請先開好遊戲。")
            return 1
        self._hwnd = hwnd
        self._window_keyword = window_keyword
        if not self.dry_run and not _foreground(hwnd):
            print("[錯誤] 無法把遊戲切到前景，按鍵不會生效。")
            return 1

        # edge-guard：建立擷取/定位器，記下起始 x 當中心、設安全左右界
        if self.edge_guard and not self.dry_run:
            try:
                self._init_edge_guard()
            except Exception as e:
                print(f"[警告] edge-guard 初始化失敗（缺 opencv/mss？{e}）→ 改用一般巡邏")
                self.edge_guard = False
            if self.edge_guard:
                sx = self._player_x(retries=12, gap=0.15, fresh=True)
                if sx is None:
                    print("[警告] edge-guard 抓不到小地圖玩家點 → 改用一般巡邏（請確認遊戲在前景、小地圖有開）")
                    self.edge_guard = False
                else:
                    self._last_x = sx
                    self._edge_lo = sx - self.edge_margin
                    self._edge_hi = sx + self.edge_margin
                    print(f"  ✓ edge-guard 啟用：起始 x={sx}，安全界[{self._edge_lo}~{self._edge_hi}]（±{self.edge_margin}px）")

        if self.edge_guard:
            move_desc = (f"每 {self.interval_min:.0f}~{self.interval_max:.0f} 秒巡邏，"
                         f"用小地圖真實 x 到邊界折返（不會走出平台）")
        else:
            move_desc = "不移動" if not self.enable_move else \
                f"每 {self.interval_min:.0f}~{self.interval_max:.0f} 秒巡邏挪一步（範圍 ±{self.patrol_steps} 步，移動後轉回攻擊方向）"
        print("=" * 56)
        print(f" 連點「{self.attack_key}」攻擊（每 {self.attack_interval:.2f}s 一下）；{move_desc}")
        print(f" 接手換邊=方向鍵暫停→轉向→Ctrl恢復 | 結束=F12")
        if self.dry_run:
            print(" [dry-run] 只印節奏、不送實體按鍵")
        print("=" * 56)

        state = "RUNNING"
        cycle_start = time.time()
        wait = self._next_interval()
        last_attack = 0.0
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
                    if self._user_intervened():
                        state = "PAUSED"
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
                                time.sleep(0.15)
                                continue
                            print("  ✓ 已切回楓之谷，繼續攻擊")
                        else:
                            # 不搶焦點：完全不送鍵、讓開。焦點離開『超過 focus_grace 秒』
                            # 才當成你真的切走並印訊息——短暫閃一下的視窗(bun/通知)直接忽略，
                            # 因為分不出是誰搶的焦點，就用『有沒有離開夠久』來判斷。
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
                    d = self._user_direction()      # 暫停期間持續記住你面對的方向
                    if d:
                        self._pending_face = d
                    if _pressed(_VK[self.RESUME_KEY]):
                        while _pressed(_VK[self.RESUME_KEY]):
                            time.sleep(self.POLL)
                        if self._pending_face:       # 採用你手動換到的方向
                            self.attack_facing = self._pending_face
                            self._pending_face = None
                        # 你手動接手可能把角色走到新位置 → edge-guard 以新位置重設安全界
                        if self.edge_guard and not self.dry_run:
                            nx = self._player_x(retries=8, gap=0.15, fresh=True)
                            if nx is not None:
                                self._last_x = nx
                                self._edge_lo = nx - self.edge_margin
                                self._edge_hi = nx + self.edge_margin
                                print(f"    edge-guard 重新定位：中心 x={nx}，安全界[{self._edge_lo}~{self._edge_hi}]")
                        state = "RUNNING"
                        cycle_start = time.time()
                        wait = self._next_interval()
                        last_attack = 0.0
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
    p.add_argument("--jump-in-place", action="store_true",
                   help="改用『原地跳』重定位（直上直下不位移，小平台不會掉下去）")
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
                    attack_interval=args.attack_interval, patrol_steps=args.patrol_steps,
                    attack_facing=args.face, enable_move=not args.no_move,
                    refocus=not args.no_refocus, jump_in_place=args.jump_in_place,
                    jump_key=args.jump_key, edge_guard=args.edge_guard,
                    edge_margin=args.edge_margin, dry_run=args.dry_run)
    return hw.run(args.window)


if __name__ == "__main__":
    sys.exit(main())
