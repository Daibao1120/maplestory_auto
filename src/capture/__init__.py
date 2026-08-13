"""螢幕擷取層：把遊戲畫面擷取成 BGR 影像（numpy.ndarray）。"""
from src.capture.screen_capture import ScreenCapture, CaptureError, Region

__all__ = ["ScreenCapture", "CaptureError", "Region"]
