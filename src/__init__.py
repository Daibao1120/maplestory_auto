"""楓之谷經典版自動化腳本（純電腦視覺）。

三層解耦架構：
    routine（路線） / commands（動作指令 command book） / engine（執行引擎）

各層對外部套件（mss / opencv / pydirectinput）採「延遲載入」，
因此缺套件時本套件仍可 import，方便先檢視架構與跑測試。
詳見 README.md 與各子模組 docstring。
"""

__version__ = "0.1.0"
