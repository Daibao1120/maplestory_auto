"""平台寬度量測的縮放缺陷。

守夜核心要求 span["width"] >= 15 才進 FARM。使用者用調參 UI 實測平台寬 107，
卻連續數晚卡在 DESCEND/IDLE_SAFE 從未開打。原因在 platform_span：它把玩家
座標乘上縮放比 sx，卻把容差參數（gap_tol/min_run/dy_max/seed_r）原封不動
傳進 find_platform_run。小地圖地形線在放大後，1px 的繪圖縫變成 2px，
超過 gap_tol=1 → 延伸提早中斷 → 寬度被截短。

更糟的是 _tick_descend 會往「比較近的那一側」走。寬度被截短時，那一側
其實是平台中央以外的方向——腳本會一步步把自己走下平台。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

np = pytest.importorskip("numpy")

from src.vision.minimap import find_platform_run  # noqa: E402


def terrain_with_drawing_gaps(width, scale, gap_px):
    """一條連續的平台線，但每隔一段有 gap_px 寬的繪圖縫（小地圖實際長相）。"""
    m = np.zeros((40, width), dtype=bool)
    m[20, :] = True
    for x in range(10, width - 10, 12 * scale):
        m[20, x:x + gap_px] = False
    return m


def test_one_pixel_gaps_are_bridged_at_native_scale():
    """基準尺度：1px 縫、gap_tol=1 → 整條接得起來。這是設計意圖。"""
    m = terrain_with_drawing_gaps(120, scale=1, gap_px=1)
    yy, lx, rx = find_platform_run(m, 60, 18, gap_tol=1)
    assert rx - lx + 1 >= 100, f"基準尺度就接不起來（只有 {rx - lx + 1}）"


def test_gap_tolerance_must_scale_with_the_image():
    """放大 2 倍後，同一條縫變成 2px；容差不跟著放大就會把平台切碎。"""
    m = terrain_with_drawing_gaps(240, scale=2, gap_px=2)
    _yy, lx, rx = find_platform_run(m, 120, 18, gap_tol=1)
    truncated = rx - lx + 1
    _yy2, lx2, rx2 = find_platform_run(m, 120, 18, gap_tol=2)   # 容差跟著放大
    scaled = rx2 - lx2 + 1
    assert truncated < 60, "前提不成立：未縮放的容差應該要把平台切碎"
    assert scaled >= 200, f"容差放大後仍接不起來（只有 {scaled}）"


def _span_width(panel_w, seam_px, roi_w):
    """在指定大小的小地圖面板上量一條有繪圖縫的平台，回傳量到的寬度。

    直接跑 MinimapLocator.platform_span（而不是檢查原始碼字串），
    這樣任何「看起來有縮放但其實算錯」的寫法都會被抓到。
    """
    cv2 = pytest.importorskip("cv2")
    from src.vision.minimap import MinimapLocator
    h = 200
    # 地形＝高飽和、不太亮（platform_span 的判定條件）
    img = np.zeros((h, panel_w, 3), np.uint8)
    img[:] = (40, 40, 40)
    row = h // 2
    img[row - 1:row + 2, :] = (150, 30, 30)          # BGR：藍色高飽和
    for x in range(20, panel_w - 20, max(8, roi_w // 20)):
        img[row - 1:row + 2, x:x + seam_px] = (40, 40, 40)
    mm = MinimapLocator({"roi": [0, 0, roi_w, 190]})
    mm.roi = [0, 0, panel_w, h]                      # 實測面板範圍（校準後的樣子）
    mm.reference_size = None                         # → sx = 1，守夜實跑的那條路徑
    span = mm.platform_span(img, (panel_w // 2, row - 3))
    return 0 if span is None else span["width"]


def test_tolerances_follow_the_actual_minimap_size_not_the_scale_factor():
    """校準後 sx 恆為 1，所以容差不能只跟著 sx 走。

    守夜實跑的路徑會把 roi 設成實測面板範圍並清掉 reference_size，
    此時 sx=1；若容差綁在 sx 上，這條路徑等於完全沒有縮放保護——
    而它正是平台被切碎、進而誤判「站在窄崖上」走下平台的地方。
    """
    ref = 235
    small = _span_width(panel_w=ref, seam_px=1, roi_w=ref)
    big = _span_width(panel_w=ref * 2, seam_px=2, roi_w=ref)
    assert small >= ref * 0.8, f"參考尺寸下就接不起來（{small}）"
    assert big >= ref * 2 * 0.8, (
        f"面板放大兩倍、縫也放大兩倍時被切碎（量到 {big}，應約 {ref * 2}）")


def test_selftest_reports_the_measured_platform_width():
    """設定檔叫使用者「用 selftest 看腳下平台寬度至少 12 以上再開巡邏」，
    但 selftest 從來沒印過那個數字。整夜沒開打時等於無從判斷。"""
    import inspect
    import tools.selftest as st
    src = inspect.getsource(st)
    assert "WIDE_ENOUGH" in src, "沒有把實測寬度和開打門檻放在一起比較"
    assert "平台寬度" in src
