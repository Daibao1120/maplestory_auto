"""判斷「哪個視窗才是真的遊戲」——純函式，可測試。

為什麼需要：設定檔只填關鍵字（「新楓之谷」），而瀏覽器分頁標題常常也含這幾個字
（實測抓到「新楓之谷：經典版官方網站 - Google Chrome」）。只比對標題就會把整個
瀏覽器視窗當成遊戲畫面，於是血條、小地圖、角色全部讀在瀏覽器的像素上，
所有偵測同時變紅，而錯誤訊息只會說「找不到玩家點」。

這段邏輯原本只寫在 tools/hold_and_wiggle.py 裡，擷取層沒有——所以擷取層照樣
會抓到瀏覽器。放在這裡讓兩邊共用。
"""

# 這些執行檔的視窗即使標題命中也不是遊戲
NON_GAME_EXES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "iexplore.exe", "explorer.exe", "code.exe", "windowsterminal.exe",
    "powershell.exe", "cmd.exe", "conhost.exe", "notepad.exe", "python.exe",
    "pythonw.exe", "claude.exe", "discord.exe", "linefortab.exe", "line.exe",
}


def is_game_window(title, exe, cls, keyword, width=0, height=0):
    """這個視窗是不是真的遊戲視窗。

    規則：標題要含關鍵字；window class 命中 maplestory 即直接採用（遊戲本體
    通常是 MapleStoryClass）；執行檔不得是瀏覽器/終端機等；視窗尺寸要像遊戲
    畫面（避免抓到小型工具視窗）。
    """
    if keyword and keyword not in (title or ""):
        return False
    if cls and "maplestory" in cls.lower():
        return True
    if (exe or "").lower() in NON_GAME_EXES:
        return False
    if width and height and (width < 640 or height < 480):
        return False
    return True
