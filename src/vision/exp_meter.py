"""EXP 進帳偵測的共用純函式。

守夜 daemon（tools/overnight.py）與掃蕩腳本（tools/hold_and_wiggle.py）都要
用，抽出來避免兩份實作漂移。這裡沒有任何截圖或按鍵，純資料運算，好測試。
"""


def region_changed(prev, cur, thresh=1.5):
    """比較兩張裁切區是否有變化，回傳 (是否變化, 要保留的新基準)。

    重點：自動重新校準後 ROI 尺寸可能改變（實測 EXP 區寬度 220→224），
    此時直接相減會 ValueError 崩潰。尺寸不同時不比較，只把新的當基準。
    """
    if prev is None or cur is None:
        return False, cur
    if getattr(prev, "shape", None) != getattr(cur, "shape", None):
        return False, cur          # ROI 換尺寸 → 這幀不比較，重新建立基準
    import numpy as _np
    return bool(float(_np.abs(cur - prev).mean()) > thresh), cur


def exp_per_hour(events, now, window=900.0):
    """由 EXP 進帳時間戳估算「每小時進帳次數」（純函式，可測試）。

    events: 遞增的時間戳清單；只看最近 window 秒內的事件。
    不足 60 秒的觀察不外推（回 0.0），避免開場數字亂跳。

    注意這是「進帳次數」而非真實經驗值（要真實數字得 OCR）。它的用途是
    比較設定 A 與 B 哪個有效，絕對值不具意義。
    """
    if not events:
        return 0.0
    recent = [t for t in events if now - t <= window]
    if len(recent) < 2:
        return 0.0
    span = now - recent[0]
    if span < 60.0:
        return 0.0
    return len(recent) * 3600.0 / span
