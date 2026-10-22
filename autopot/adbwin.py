"""雷電模擬器（Android）的「遊戲視窗」：用 ADB 截圖和點擊。

跟 autopot.window.GameWindow 介面一樣，所以商店、補貨的程式都可以直接用。
不同的地方：
  * 不動真的滑鼠、不需要模擬器在最上層（被擋住、在背景都可以）
    → 跟電腦版的補水程式同時跑也不會搶滑鼠
  * 截圖是模擬器的原始解析度（例如 1920x1080），座標也是
  * 「雙擊」= 點一下、等一下、再點一下（手機版：第一下選取，第二下才會打開，例如妙妙）
  * 沒有滑鼠游標 → 不會跳說明框，move_away 什麼都不做
"""
from __future__ import annotations

import logging
import shutil
import struct
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("autopot.adb")

KEYCODES = {"enter": 66, "esc": 111, "backspace": 67, "delete": 112, "tab": 61}
DEFAULT_ADB = [r"F:\ldplayer\ldplayer9\adb.exe", r"C:\LDPlayer\LDPlayer9\adb.exe", r"D:\LDPlayer\LDPlayer9\adb.exe"]


class AdbError(RuntimeError):
    pass


def find_adb(path: str | None = None) -> str:
    cands = ([path] if path else []) + DEFAULT_ADB
    for c in cands:
        if c and Path(c).exists():
            return c
    w = shutil.which("adb")
    if w:
        return w
    raise AdbError("找不到 adb.exe：請在 config_ld.yaml 的 adb_path 填雷電模擬器資料夾裡的 adb.exe")


def parse_raw_screencap(data: bytes) -> np.ndarray:
    """`adb exec-out screencap`（不加 -p）的原始格式：開頭是寬、高、格式（有的版本多 4 bytes 色彩空間），後面是 RGBA。"""
    if len(data) < 16:
        raise AdbError(f"截圖資料太短（{len(data)} bytes）")
    w, h, fmt = struct.unpack("<III", data[:12])
    n = w * h * 4
    if w <= 0 or h <= 0 or n > len(data):
        raise AdbError(f"截圖格式不對（{w}x{h}，{len(data)} bytes）")
    head = len(data) - n
    if head not in (12, 16):
        raise AdbError(f"截圖格式不對（開頭 {head} bytes）")
    rgba = np.frombuffer(data, np.uint8, n, head).reshape(h, w, 4)
    return np.ascontiguousarray(rgba[..., 2::-1])      # RGBA → BGR


class AdbWindow:
    """模擬器畫面。runner 是「執行 adb 指令」的函式（測試時可以換成假的）。"""

    def __init__(self, serial: str = "emulator-5554", adb_path: str | None = None, runner=None,
                 tap_gap: float = 0.45):
        self.serial = serial
        self.adb = None if runner else find_adb(adb_path)
        self._run = runner or self._real_run
        self.tap_gap = tap_gap
        self.hwnd = None
        self._size = None
        self.stats = {"capture": [0, 0.0], "input": [0, 0.0]}    # [次數, 總秒數]：看 ADB 慢在哪裡

    # ---------- adb ----------
    def _real_run(self, args: list[str], timeout: float = 10.0) -> bytes:
        cmd = [self.adb, "-s", self.serial] + args
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise AdbError(f"adb 沒有回應：{' '.join(args[:3])}")
        if p.returncode != 0:
            raise AdbError((p.stderr or p.stdout).decode("utf-8", "replace").strip() or f"adb 失敗 {p.returncode}")
        return p.stdout

    def shell(self, *args: str) -> bytes:
        t = time.time()
        try:
            return self._run(["shell"] + [str(a) for a in args])
        finally:
            st = self.stats["input"]
            st[0] += 1
            st[1] += time.time() - t

    def connect(self) -> None:
        """確認連得上模擬器（雷電模擬器要打開「ADB 偵錯：開啟本地連接」）。"""
        try:
            out = self._run(["get-state"], timeout=5).decode("utf-8", "replace").strip()
        except AdbError as e:
            raise AdbError(f"連不上模擬器（{self.serial}）：{e}。請確認雷電模擬器有開，"
                           "而且「設定 → 其他 → ADB 偵錯」是「開啟本地連接」") from e
        if out != "device":
            raise AdbError(f"模擬器狀態是「{out}」，不是 device")

    # ---------- 跟 GameWindow 一樣的介面 ----------
    def locate(self) -> bool:
        try:
            self.connect()
            return True
        except AdbError:
            return False

    def capture(self) -> np.ndarray:
        t = time.time()
        data = self._run(["exec-out", "screencap"], timeout=10)
        st = self.stats["capture"]
        st[0] += 1
        st[1] += time.time() - t
        img = parse_raw_screencap(data)
        self._size = (img.shape[1], img.shape[0])
        return img

    def client_rect(self) -> tuple[int, int, int, int]:
        if self._size is None:
            self.capture()
        return 0, 0, self._size[0], self._size[1]

    def is_minimized(self) -> bool:
        return False

    def foreground(self) -> int:
        return 0

    def activate(self) -> None:
        pass

    def restore_foreground(self, hwnd: int) -> None:
        pass

    def set_topmost(self, on: bool = True) -> None:
        pass

    def arrange_side_by_side(self) -> None:
        pass

    @staticmethod
    def mouse_pos() -> tuple[int, int]:
        return (0, 0)          # 不用真的滑鼠 → 不會誤判「你正在用電腦」

    def click(self, x: float, y: float, jitter: int = 2) -> None:
        if jitter:
            x += np.random.randint(-jitter, jitter + 1)
            y += np.random.randint(-jitter, jitter + 1)
        self.shell("input", "tap", int(round(x)), int(round(y)))

    def tap(self, x: float, y: float) -> None:
        self.shell("input", "tap", int(round(x)), int(round(y)))

    def double_click(self, x: float, y: float) -> None:
        """手機版：點一下選取，等一下再點一下才會打開（實測：連點太快沒反應）。"""
        self.tap(x, y)
        time.sleep(self.tap_gap)
        self.tap(x, y)

    def drag(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.shell("input", "swipe", int(x1), int(y1), int(x2), int(y2), 400)

    def move_away(self, x: float, y: float) -> None:
        pass                   # 沒有滑鼠游標，不會跳說明框

    def wheel(self, x: float, y: float, clicks: int) -> None:
        pass                   # 手機沒有滾輪（商店清單用 ▲▼ 或拖滑塊）

    def press(self, key: str, times: int = 1) -> None:
        code = KEYCODES.get(key)
        if code is None:
            raise AdbError(f"不支援的按鍵：{key}")
        self.shell("input", "keyevent", *([code] * max(1, times)))

    def type_text(self, text: str) -> None:
        if not text.isdigit():
            raise AdbError("只支援輸入數字")
        self.shell("input", "text", text)

    def stats_text(self, reset: bool = True) -> str:
        c, i = self.stats["capture"], self.stats["input"]
        out = (f"截圖 {c[0]} 次（平均 {c[1] / max(1, c[0]):.2f} 秒）、"
               f"點擊/拖曳 {i[0]} 次（平均 {i[1] / max(1, i[0]):.2f} 秒）")
        if reset:
            self.stats = {"capture": [0, 0.0], "input": [0, 0.0]}
        return out
