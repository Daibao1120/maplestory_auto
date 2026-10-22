"""遊戲視窗：尋找、截圖、滑鼠鍵盤操作（Windows 專用）。

所有座標都以「遊戲視窗內容區左上角」為原點，所以視窗被拖到別的位置也沒關係。
"""
from __future__ import annotations

import ctypes
import random
import sys
import time
from ctypes import wintypes

import numpy as np

if sys.platform != "win32":  # 讓其他平台也能 import（跑單元測試用）
    raise ImportError("window.py 只能在 Windows 上使用")

# 一定要在 import pyautogui 之前設定 DPI 感知，否則 4K/縮放 150% 的螢幕座標會跑掉
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import mss  # noqa: E402
import pyautogui  # noqa: E402

pyautogui.FAILSAFE = True   # 滑鼠甩到螢幕左上角 = 緊急停止
pyautogui.PAUSE = 0.0

user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def _title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def list_windows() -> list[tuple[int, str]]:
    out = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            t = _title(hwnd)
            if t:
                out.append((hwnd, t))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


_GDI = None


def _gdi_api():
    """背景截圖用的 Win32 函式（另外載入一份並設定好參數型別：64 位元的 handle 不能被截成 32 位元）。"""
    global _GDI
    if _GDI is None:
        u = ctypes.WinDLL("user32")
        g = ctypes.WinDLL("gdi32")
        vp, ci, cu = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint
        u.GetDC.restype, u.GetDC.argtypes = vp, [vp]
        u.ReleaseDC.restype, u.ReleaseDC.argtypes = ci, [vp, vp]
        u.PrintWindow.restype, u.PrintWindow.argtypes = ctypes.c_bool, [vp, vp, cu]
        g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = vp, [vp]
        g.CreateCompatibleBitmap.restype, g.CreateCompatibleBitmap.argtypes = vp, [vp, ci, ci]
        g.SelectObject.restype, g.SelectObject.argtypes = vp, [vp, vp]
        g.DeleteObject.restype, g.DeleteObject.argtypes = ctypes.c_bool, [vp]
        g.DeleteDC.restype, g.DeleteDC.argtypes = ctypes.c_bool, [vp]
        g.GetDIBits.restype, g.GetDIBits.argtypes = ci, [vp, vp, cu, cu, vp, vp, cu]
        _GDI = (u, g)
    return _GDI


class GameWindow:
    def __init__(self, title_keyword: str, exclude_keywords: list[str] | None = None):
        self.keyword = title_keyword
        self.exclude = exclude_keywords or []
        self.hwnd = None
        self._sct = mss.mss()

    # ---------- 視窗 ----------
    def locate(self) -> bool:
        cands = [(h, t) for h, t in list_windows()
                 if self.keyword in t and not any(e in t for e in self.exclude)]
        if not cands:
            self.hwnd = None
            return False
        # 有多個時，選內容區最大的那個（避免抓到啟動器小視窗）
        cands.sort(key=lambda ht: self._client_size(ht[0])[0] * self._client_size(ht[0])[1], reverse=True)
        self.hwnd = cands[0][0]
        return True

    @staticmethod
    def _client_size(hwnd) -> tuple[int, int]:
        r = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(r))
        return r.right - r.left, r.bottom - r.top

    def client_rect(self) -> tuple[int, int, int, int]:
        """回傳 (螢幕x, 螢幕y, 寬, 高)"""
        if not self.hwnd or not user32.IsWindow(self.hwnd):
            if not self.locate():
                raise RuntimeError(f"找不到標題包含「{self.keyword}」的遊戲視窗")
        w, h = self._client_size(self.hwnd)
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(self.hwnd, ctypes.byref(pt))
        return pt.x, pt.y, w, h

    def is_minimized(self) -> bool:
        return bool(self.hwnd and user32.IsIconic(self.hwnd))

    def foreground(self) -> int:
        return user32.GetForegroundWindow()

    def activate(self) -> None:
        """把遊戲視窗叫到最前面。"""
        self.client_rect()
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(self.hwnd)
        time.sleep(0.25)
        if user32.GetForegroundWindow() != self.hwnd:
            # Windows 有時會擋程式搶焦點 → 改成點一下遊戲視窗的標題列（不會影響遊戲操作）
            r = wintypes.RECT()
            user32.GetWindowRect(self.hwnd, ctypes.byref(r))
            cx, cy, _, _ = self.client_rect()
            ty = r.top + max(4, (cy - r.top) // 2)
            if cy - r.top >= 8:
                pyautogui.click((r.left + r.right) // 2, ty)
            else:  # 無邊框視窗：點內容區最上緣
                pyautogui.click((r.left + r.right) // 2, cy + 3)
            time.sleep(0.3)

    def set_topmost(self, on: bool = True) -> None:
        """讓遊戲視窗一直在最上層（被別的視窗擋住就截不到 EXP 條）。"""
        self.client_rect()
        flags = 0x0001 | 0x0002 | 0x0010          # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
        after = wintypes.HWND(-1 if on else -2)  # HWND_TOPMOST / HWND_NOTOPMOST
        user32.SetWindowPos(wintypes.HWND(self.hwnd), after, 0, 0, 0, 0, flags)

    def resize_client(self, w: int, h: int, tries: int = 8) -> tuple[int, int]:
        """把遊戲畫面（內容區，不含標題列和邊框）調成剛好 w x h。回傳調完後的大小。

        遊戲在調整大小時會自己微調（例如維持 16:9），所以量一次、差多少補多少，重複幾次。
        視窗最大化時先還原，不然改不了大小。調完如果超出螢幕，就移回螢幕裡。"""
        self.client_rect()
        if user32.IsIconic(self.hwnd) or user32.IsZoomed(self.hwnd):
            user32.ShowWindow(self.hwnd, 9)                     # SW_RESTORE
            time.sleep(0.5)
        SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0002, 0x0004, 0x0010
        for _ in range(tries):
            cw, ch = self._client_size(self.hwnd)
            if (cw, ch) == (w, h):
                break
            r = wintypes.RECT()
            user32.GetWindowRect(self.hwnd, ctypes.byref(r))
            ow = (r.right - r.left) + (w - cw)
            oh = (r.bottom - r.top) + (h - ch)
            user32.SetWindowPos(self.hwnd, 0, 0, 0, ow, oh, SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
            time.sleep(0.4)
        # 超出螢幕工作區 → 移回來（截圖截不到螢幕外面）
        work = wintypes.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)   # SPI_GETWORKAREA
        r = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        x = min(max(r.left, work.left), max(work.left, work.right - (r.right - r.left)))
        y = min(max(r.top, work.top), max(work.top, work.bottom - (r.bottom - r.top)))
        if (x, y) != (r.left, r.top):
            user32.SetWindowPos(self.hwnd, 0, x, y, 0, 0, 0x0001 | SWP_NOZORDER | SWP_NOACTIVATE)
            time.sleep(0.3)
        return self._client_size(self.hwnd)

    def restore_foreground(self, hwnd: int) -> None:
        if hwnd and hwnd != self.hwnd and user32.IsWindow(hwnd):
            user32.SetForegroundWindow(hwnd)

    def arrange_side_by_side(self) -> None:
        """遊戲靠螢幕右邊、這個命令視窗放左邊，兩個不互相擋住。
        遊戲視窗只移動位置、不改大小（大小改了校正就不準）。"""
        self.client_rect()
        work = wintypes.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)   # SPI_GETWORKAREA
        r = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        gw, gh = r.right - r.left, r.bottom - r.top
        gx = max(work.left, work.right - gw)
        gy = max(work.top, work.top + (work.bottom - work.top - gh) // 2)
        SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
        user32.SetWindowPos(self.hwnd, 0, gx, gy, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        con = ctypes.windll.kernel32.GetConsoleWindow()
        if con:
            cw = gx - work.left
            if cw >= 300:
                user32.MoveWindow(con, work.left, work.top, cw, work.bottom - work.top, True)
            else:  # 螢幕不夠寬：命令視窗放左下角小一點
                user32.MoveWindow(con, work.left, work.bottom - 400, 900, 400, True)
        time.sleep(0.3)

    # ---------- 你有沒有在用電腦 ----------
    @staticmethod
    def idle_seconds() -> float:
        """距離你上一次動滑鼠「或按鍵盤」幾秒（Windows 的 GetLastInputInfo）。"""
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
        li = LASTINPUTINFO()
        li.cbSize = ctypes.sizeof(li)
        if not user32.GetLastInputInfo(ctypes.byref(li)):
            return 1e9
        return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0

    # ---------- 截圖 ----------
    def capture_background(self) -> np.ndarray | None:
        """遊戲被別的視窗蓋住時也截得到（PrintWindow + PW_RENDERFULLCONTENT，Windows 8.1 以後）。
        視窗縮小到工作列、或遊戲不支援時回傳 None。不會把遊戲叫到前面。"""
        try:
            self.client_rect()
            if user32.IsIconic(self.hwnd):
                return None
            w, h = self._client_size(self.hwnd)
            if w <= 0 or h <= 0:
                return None
            u, gdi32 = _gdi_api()
            hdc = u.GetDC(self.hwnd)
            mem = gdi32.CreateCompatibleDC(hdc)
            bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
            old = gdi32.SelectObject(mem, bmp)
            try:
                ok = u.PrintWindow(self.hwnd, mem, 0x1 | 0x2)     # PW_CLIENTONLY | PW_RENDERFULLCONTENT
                gdi32.SelectObject(mem, old)                      # GetDIBits 時 bitmap 不能還選在 DC 裡
                old = None

                class BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
                bi = BITMAPINFOHEADER()
                bi.biSize = ctypes.sizeof(bi)
                bi.biWidth, bi.biHeight = w, -h          # 負的 = 由上往下
                bi.biPlanes, bi.biBitCount = 1, 32
                buf = ctypes.create_string_buffer(w * h * 4)
                got = gdi32.GetDIBits(mem, bmp, 0, h, ctypes.addressof(buf), ctypes.addressof(bi), 0)  # DIB_RGB_COLORS
            finally:
                if old is not None:
                    gdi32.SelectObject(mem, old)
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(mem)
                u.ReleaseDC(self.hwnd, hdc)
            if not ok or got != h:
                return None
            img = np.frombuffer(buf.raw, np.uint8).reshape(h, w, 4)[:, :, :3]
            img = np.ascontiguousarray(img)
            if float(img.std()) < 4:                     # 有的遊戲背景截圖會是全黑
                return None
            return img
        except Exception:
            return None

    def capture(self) -> np.ndarray:
        x, y, w, h = self.client_rect()
        shot = self._sct.grab({"left": x, "top": y, "width": w, "height": h})
        img = np.asarray(shot)[:, :, :3]  # BGRA -> BGR
        return np.ascontiguousarray(img)

    # ---------- 滑鼠鍵盤（座標都是視窗內座標） ----------
    def _to_screen(self, x: float, y: float) -> tuple[int, int]:
        sx, sy, _, _ = self.client_rect()
        return int(sx + x), int(sy + y)

    @staticmethod
    def _jitter(v: int, j: int = 2) -> int:
        return v + random.randint(-j, j)

    def click(self, x: float, y: float, jitter: int = 2) -> None:
        sx, sy = self._to_screen(x, y)
        pyautogui.moveTo(self._jitter(sx, jitter), self._jitter(sy, jitter),
                         duration=random.uniform(0.08, 0.2))
        time.sleep(random.uniform(0.04, 0.09))
        pyautogui.mouseDown()
        time.sleep(random.uniform(0.04, 0.08))
        pyautogui.mouseUp()

    def double_click(self, x: float, y: float) -> None:
        sx, sy = self._to_screen(x, y)
        pyautogui.moveTo(self._jitter(sx), self._jitter(sy), duration=random.uniform(0.08, 0.2))
        time.sleep(0.08)
        for _ in range(2):
            pyautogui.mouseDown()
            time.sleep(0.04)
            pyautogui.mouseUp()
            time.sleep(0.07)

    def tap(self, x: float, y: float) -> None:
        """快速點一下（不加人性化的移動時間），連續點捲動鈕用。"""
        sx, sy = self._to_screen(x, y)
        pyautogui.moveTo(sx, sy)
        time.sleep(0.02)
        pyautogui.mouseDown()
        time.sleep(0.03)
        pyautogui.mouseUp()
        time.sleep(0.03)

    def drag(self, x1: float, y1: float, x2: float, y2: float) -> None:
        """按住滑鼠從 (x1,y1) 拖到 (x2,y2)（拖捲軸滑塊用）。"""
        sx, sy = self._to_screen(x1, y1)
        ex, ey = self._to_screen(x2, y2)
        pyautogui.moveTo(sx, sy, duration=0.05)
        time.sleep(0.05)
        pyautogui.mouseDown()
        time.sleep(0.05)
        pyautogui.moveTo(ex, ey, duration=0.15)
        time.sleep(0.05)
        pyautogui.mouseUp()

    def move_away(self, x: float, y: float) -> None:
        sx, sy = self._to_screen(x, y)
        pyautogui.moveTo(sx, sy, duration=0.1)

    def wheel(self, x: float, y: float, clicks: int) -> None:
        """在 (x, y) 滾動滑鼠滾輪；正數往上、負數往下。"""
        sx, sy = self._to_screen(x, y)
        pyautogui.moveTo(sx, sy, duration=0.05)
        pyautogui.scroll(int(clicks))

    def press(self, key: str, times: int = 1) -> None:
        for _ in range(times):
            pyautogui.press(key)
            time.sleep(random.uniform(0.03, 0.07))

    def type_text(self, text: str) -> None:
        """輸入數字。

        用「數字鍵盤」的按鍵（Num0~Num9）來打：注音輸入法只會攔截上排的數字鍵
        （1=ㄅ、2=ㄉ…），數字鍵盤的數字不會被轉成注音，所以不用切換輸入法。
        如果 NumLock 沒開，會暫時打開，打完再關回去。"""
        VK_NUMLOCK = 0x90
        numlock_was_on = bool(user32.GetKeyState(VK_NUMLOCK) & 1)
        if not numlock_was_on:
            pyautogui.press("numlock")
            time.sleep(0.05)
        try:
            for ch in text:
                if ch.isdigit():
                    pyautogui.press("num" + ch)
                else:
                    pyautogui.press(ch)
                time.sleep(random.uniform(0.05, 0.1))
        finally:
            if not numlock_was_on:
                pyautogui.press("numlock")

    @staticmethod
    def mouse_pos() -> tuple[int, int]:
        p = pyautogui.position()
        return int(p[0]), int(p[1])
