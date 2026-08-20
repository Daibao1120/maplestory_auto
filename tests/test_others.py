"""遇人偵測測試：同層附近出現其他玩家（搶怪／被檢舉風險）。

實機確認的顏色分工：自己＝黃點（H≈27-30）、其他玩家＝純紅十字（H≈0、
S=255、V=255）、傳點/NPC＝青綠（H≈97）。只有「和我同一層且在附近」的
才要緊，別層或遠處的玩家不影響。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from src.vision.minimap import MinimapLocator  # noqa: E402
from tools.overnight import ClassProfile, NightWatchCore, WorldState  # noqa: E402


def minimap(dots):
    """dots: [(x, y, 'red'|'yellow'|'cyan')] → 合成小地圖影像（BGR）。"""
    f = np.full((120, 300, 3), 30, dtype=np.uint8)      # 深色地形底
    colors = {"red": (0, 0, 255), "yellow": (0, 230, 245), "cyan": (200, 220, 0)}
    for x, y, kind in dots:
        f[y - 2:y + 3, x - 2:x + 3] = colors[kind]
    return f


def loc():
    return MinimapLocator({"roi": None, "reference_size": None})


def test_only_red_dots_count_as_other_players():
    f = minimap([(50, 40, "red"), (120, 40, "yellow"), (200, 40, "cyan"),
                 (250, 90, "red")])
    dots = loc().other_player_dots(f)
    xs = sorted(d[0] for d in dots)
    assert len(dots) == 2                       # 黃點（我）與青綠（傳點）不算
    assert abs(xs[0] - 50) <= 2 and abs(xs[1] - 250) <= 2


def test_no_others_on_empty_map():
    assert loc().other_player_dots(minimap([(60, 30, "yellow")])) == []


def test_others_near_only_counts_same_layer_and_close():
    f = minimap([(105, 50, "red"),      # 同層、很近 → 算
                 (260, 50, "red"),      # 同層但很遠 → 不算
                 (108, 90, "red")])     # 很近但不同層 → 不算
    n = loc().others_near(f, (100, 50), dy_tol=3, dx_range=40)
    assert n == 1


def test_others_near_zero_without_player_pos():
    f = minimap([(105, 50, "red")])
    assert loc().others_near(f, None) == 0


# ---------------- 核心反應 ----------------

def W(now, **kw):
    return WorldState(now=now, **kw)


def SPAN(width=20.0, dl=10.0, dr=10.0):
    return {"width": width, "dist_left": dl, "dist_right": dr}


def farming(on_others="log"):
    c = NightWatchCore(heal_mode="external", profile=ClassProfile())
    c.on_others = on_others
    c.tick(W(0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    c.tick(W(1.0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    assert c.state == "FARM"
    return c


def test_logs_once_when_others_appear_and_keeps_farming():
    c = farming("log")
    acts = c.tick(W(2.0, hp=0.9, pos=(50, 90), span=SPAN(), others_near=2))
    logs = [a.arg for a in acts if a.verb == "log"]
    assert any("其他玩家" in str(m) or "位玩家" in str(m) for m in logs)
    assert c.state == "FARM"                    # 預設不停手
    # 不重複洗版
    acts = c.tick(W(3.0, hp=0.9, pos=(50, 90), span=SPAN(), others_near=2))
    assert not [a for a in acts if a.verb == "log"]


def test_idle_mode_yields_the_spot_after_sustained_presence():
    c = farming("idle")
    c.tick(W(2.0, hp=0.9, pos=(50, 90), span=SPAN(), others_near=1))
    assert c.state == "FARM"                    # 剛出現先觀察
    c.tick(W(2.0 + NightWatchCore.OTHERS_HOLD + 1, hp=0.9,
             pos=(50, 90), span=SPAN(), others_near=1))
    assert c.state == "IDLE_SAFE"               # 持續存在 → 讓出位置


def test_others_leaving_resets_the_timer():
    c = farming("idle")
    c.tick(W(2.0, hp=0.9, pos=(50, 90), span=SPAN(), others_near=1))
    c.tick(W(10.0, hp=0.9, pos=(50, 90), span=SPAN(), others_near=0))   # 走了
    c.tick(W(15.0, hp=0.9, pos=(50, 90), span=SPAN(), others_near=1))   # 又來
    c.tick(W(15.0 + NightWatchCore.OTHERS_HOLD - 2, hp=0.9,
             pos=(50, 90), span=SPAN(), others_near=1))
    assert c.state == "FARM"                    # 重新計時，還沒到門檻
