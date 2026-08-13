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

    def __init__(self, backend="mss", window_title=None, region=None):
        self.backend = backend
        self.window_title = window_title
        self._region: Optional[Region] = Region(*region) if region else None
        self._sct = None  # mss 實例（延遲建立）

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

        Windows 上可用 ctypes 呼叫 user32.FindWindowW / GetWindowRect 取得視窗矩形；
        非 Windows（例如測試沙箱）或找不到視窗時，退回既有 region（可能為 None）。
        """
        if not self.window_title or sys.platform != "win32":
            return self._region
        # TODO: 以 ctypes FindWindowW + GetWindowRect 實作實際定位並更新 self._region。
        return self._region

    def grab(self):
        """擷取一張畫面，回傳 BGR 的 numpy.ndarray（H, W, 3）。"""
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
