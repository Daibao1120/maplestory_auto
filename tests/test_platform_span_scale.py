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


def test_platform_span_scales_its_tolerances():
    """platform_span 必須把容差一起換算到實際影像尺度。"""
    import inspect
    from src.vision.minimap import MinimapLocator
    src = inspect.getsource(MinimapLocator.platform_span)
    assert "gap_tol=gap_tol," not in src, (
        "gap_tol 原封不動傳下去——放大後平台會被繪圖縫切碎")


def test_selftest_reports_the_measured_platform_width():
    """設定檔叫使用者「用 selftest 看腳下平台寬度至少 12 以上再開巡邏」，
    但 selftest 從來沒印過那個數字。整夜沒開打時等於無從判斷。"""
    import inspect
    import tools.selftest as st
    src = inspect.getsource(st)
    assert "WIDE_ENOUGH" in src, "沒有把實測寬度和開打門檻放在一起比較"
    assert "平台寬度" in src
