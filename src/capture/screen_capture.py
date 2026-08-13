"""螢幕擷取封裝。

支援兩種後端：
- ``mss``：跨平台、速度快（預設）。
- ``windows-capture``：Windows Graphics Capture API（選用，較適合擷取單一視窗）。

外部套件採「延遲載入」：未安裝時本模組仍可 import，
只有在建立 ScreenCapture（實際擷取）時才會提示安裝。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

# 延遲載入外部相依，確保缺套件時仍可 import
try:
    import mss  # type: ignore
    _MSS_AVAILABLE = True
except ImportError:
    mss = None  # type: ignore
    _MSS_AVAILABLE = False

try:
    import numpy as np  # type: ignore
except ImportError:
    np = None  # type: ignore


class CaptureError(RuntimeError):
    """螢幕擷取相關錯誤。"""


@dataclass
class Region:
    """擷取區域（螢幕像素座標）。"""
    left: int
    top: int
    width: int
    height: int

    def as_dict(self):
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}


class ScreenCapture:
    """遊戲畫面擷取器。

    參數：
        backend: "mss" 或 "windows-capture"。
        window_title: 目標視窗標題關鍵字；提供時嘗試自動定位視窗區域。
        region: 手動指定的擷取區域 [left, top, width, height]；優先於 window_title。

    典型用法：
        >>> cap = ScreenCapture(window_title="MapleStory")
        >>> frame = cap.grab()      # 回傳 BGR 的 numpy.ndarray（H, W, 3）
        >>> cap.close()
    """

    def __init__(self, backend="mss", window_title=None, region=None,
                 dry_run=False, synthetic_size=(865, 1370)):
        self.backend = backend
        self.window_title = window_title
        self._region: Optional[Region] = Region(*region) if region else None
        self._sct = None  # mss 實例（延遲建立）
        # dry_run：不接觸真實螢幕 / mss，改回傳合成畫面，讓主迴圈可在無遊戲、
        # 無顯示環境（例如 CI / 無頭沙箱）下空轉測試。
        self.dry_run = dry_run
        self.synthetic_size = synthetic_size  # (height, width)
        # dry_run 時可注入自訂畫面產生器（回傳一張 BGR frame）：
        # 例如真實截圖或合成場景。None 則回傳全黑畫面。
        self.scene_provider = None

    # ---- 生命週期 ----
    def _ensure_backend(self):
        """確認後端可用，否則丟出友善錯誤。"""
        if self.backend == "mss":
            if not _MSS_AVAILABLE:
                raise CaptureError("尚未安裝 mss。請執行： pip install mss")
            if self._sct is None:
                self._sct = mss.mss()
        elif self.backend == "windows-capture":
            # TODO: 整合 windows-capture（Windows Graphics Capture API），可避免被遮擋。
            raise CaptureError("windows-capture 後端尚未實作，請先使用 backend='mss'。")
        else:
            raise CaptureError(f"未知的擷取後端：{self.backend!r}")

    def locate_window(self):
        """依 window_title 定位遊戲視窗，回傳 Region 或 None。

        以 ctypes EnumWindows + GetWindowTextW 做「標題子字串」比對（設定檔只填
        關鍵字如「新楓之谷」，實際標題是「新楓之谷：經典版」），矩形優先取
        DWM ExtendedFrameBounds（= 眼睛看到的視窗，不含 Win10 隱形邊框），
        退回 GetWindowRect。每次呼叫都重新定位，視窗被移動也追得到。
        非 Windows 或找不到視窗時，退回既有 region（可能為 None）。
        """
        if not self.window_title or sys.platform != "win32":
            return self._region
        import ctypes
        import ctypes.wintypes as wt

        # 讓座標以實體像素為準（螢幕縮放 ≠100% 時 mss 抓的是實體像素）；失敗無妨
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

        user32 = ctypes.windll.user32
        found = []
        keyword = self.window_title

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def _on_window(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if keyword in buf.value:
                found.append(hwnd)
                return False  # 找到就停
            return True

        user32.EnumWindows(_on_window, 0)
        if not found:
            return self._region

        hwnd = found[0]
        rect = wt.RECT()
        got = False
        try:  # DWMWA_EXTENDED_FRAME_BOUNDS = 9
            got = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0
        except Exception:
            got = False
        if not got and not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return self._region
        return Region(rect.left, rect.top,
                      rect.right - rect.left, rect.bottom - rect.top)

    def grab(self):
        """擷取一張畫面，回傳 BGR 的 numpy.ndarray（H, W, 3）。

        dry_run 模式回傳全黑合成畫面（不需 mss / 螢幕），供無遊戲環境測試主迴圈。
        """
        if self.dry_run:
            if self.scene_provider is not None:
                return self.scene_provider()
            if np is None:
                return None  # 連 numpy 都沒有時回傳 None；下游辨識皆為 stub，可容忍
            h, w = self.synthetic_size
            return np.zeros((h, w, 3), dtype=np.uint8)
        self._ensure_backend()
        if np is None:
            raise CaptureError("尚未安裝 numpy。請執行： pip install numpy")

        region = self._region or self.locate_window()
        if region is not None:
            raw = self._sct.grab(region.as_dict())
        else:
            raw = self._sct.grab(self._sct.monitors[1])  # 主螢幕

        # mss 回傳 BGRA；取前三通道即為 BGR，供 OpenCV 使用
        return np.asarray(raw)[:, :, :3]

    def close(self):
        """釋放資源。"""
        if self._sct is not None:
            try:
                self._sct.close()
            finally:
                self._sct = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
