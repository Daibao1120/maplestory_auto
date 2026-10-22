"""圖形介面用的自動校正（autopot/autocal.py）：畫面比例要自己找得到（朋友的螢幕、視窗大小都不一樣）。"""
import json
import sys
from pathlib import Path

import cv2
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autopot import autocal  # noqa: E402

R = ROOT / "tests" / "samples" / "real"


@pytest.mark.parametrize("zoom", [0.6, 0.8, 1.0, 1.25])
def test_find_scale_on_resized_real_screen(zoom):
    """真實截圖（2764 寬）縮放成不同大小（= 朋友不同的螢幕/視窗），都要找到道具欄按鈕、比例要對。"""
    img = cv2.imread(str(R / "hud_bottom.png"))           # 你電腦上的真實截圖（下方工具列那一段）
    img = cv2.resize(img, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_AREA)
    meta = autocal._meta()
    ref = cv2.imread(str(autocal.REFS / "inv_button.png"))
    base = meta["ref_screen_width"] / meta["native_screen_width"]
    score, f, (x, y) = autocal.find_scale(img, ref, base)
    assert score > 0.8
    _, f1, _ = autocal.find_scale(cv2.imread(str(R / "hud_bottom.png")), ref, base)
    assert f == pytest.approx(f1 / zoom, rel=0.05)


def test_quickslot_cells_from_reference_pair():
    cal = json.loads((R / "calibration.json").read_text())["regions"]
    cells = autocal.quickslot_cells(cal["qs_hp"], cal["qs_mp"])
    assert cells["qs_home"] == cal["qs_hp"] and cells["qs_pgup"] == cal["qs_mp"]
    pitch = cal["qs_mp"][0] - cal["qs_hp"][0]
    assert cells["qs_shift"][0] == cal["qs_hp"][0] - 2 * pitch
    assert cells["qs_pgdn"][1] == cal["qs_hp"][1] + pitch
    assert len(cells) == 8
