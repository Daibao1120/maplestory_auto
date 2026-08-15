"""視窗比對測試：絕不能把瀏覽器分頁當成遊戲視窗（實際發生過的誤操作）。

Chrome 開著遊戲官網登入頁時，分頁標題就是「新楓之谷：經典版」——舊版
只比對標題，於是對瀏覽器狂送按鍵。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

if sys.platform != "win32":
    pytest.skip("Windows only", allow_module_level=True)

from tools.hold_and_wiggle import is_game_window  # noqa: E402

KW = "新楓之谷"


def test_rejects_chrome_tab_with_game_title():
    assert is_game_window("新楓之谷：經典版 - Google Chrome", "chrome.exe",
                          "Chrome_WidgetWin_1", KW, 3840, 2080) is False


def test_rejects_other_browsers_and_terminals():
    for exe in ("msedge.exe", "firefox.exe", "windowsterminal.exe",
                "powershell.exe", "code.exe"):
        assert is_game_window("新楓之谷：經典版", exe, "SomeClass", KW,
                              1920, 1080) is False, exe


def test_accepts_real_game_window():
    assert is_game_window("新楓之谷：經典版", "maplestory.exe", "MapleStoryClass",
                          KW, 2736, 1596) is True


def test_game_class_wins_even_if_exe_unknown():
    # 有些版本執行檔改名；class 命中就採用
    assert is_game_window("新楓之谷", "launcher_x64.exe", "MapleStoryClass",
                          KW, 2736, 1596) is True


def test_rejects_title_mismatch_and_tiny_windows():
    assert is_game_window("其他遊戲", "maplestory.exe", "MapleStoryClass",
                          KW, 2736, 1596) is False
    assert is_game_window("新楓之谷", "unknown.exe", "Foo", KW, 320, 200) is False


def test_accepts_unknown_exe_with_reasonable_size():
    # 執行檔名不在黑名單、尺寸像遊戲 → 放行（避免過度嚴格找不到視窗）
    assert is_game_window("新楓之谷：經典版", "unknown.exe", "Foo", KW,
                          1600, 900) is True
