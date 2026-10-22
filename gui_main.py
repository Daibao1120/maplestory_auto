"""圖形介面版的進入點（PyInstaller 打包成 MapleAutoPot.exe 用這個檔）。"""
import sys

from autopot.gui import main

if __name__ == "__main__":
    sys.exit(main())
