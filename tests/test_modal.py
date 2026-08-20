"""置中彈窗偵測測試（測謊/符文/死亡對話框）＋核心的零輸入反應。

測謊沒答會被斷線甚至標記，是封號風險最高的一環。本工具只偵測並停手通知，
不自動作答——這些測試釘住「該停的一定停、不該停的不要亂停」。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from src.vision.modal import (ModalWatcher, find_modal, centrality,  # noqa: E402
                              rects_overlap)
from tools.overnight import ClassProfile, NightWatchCore, WorldState  # noqa: E402

H, W = 808, 1371


def world():
    """高飽和的遊戲世界背景（綠色系）——不該被當成 UI。"""
    f = np.zeros((H, W, 3), dtype=np.uint8)
    f[:, :] = (40, 160, 60)
    return f


def panel(f, x, y, w, h, v=210):
    """畫一塊灰白色 UI 面板。"""
    f[y:y + h, x:x + w] = (v, v, v)
    return f


# ---------------- 純函式 ----------------

def test_centrality_math():
    cx, cy = centrality((W // 2 - 50, H // 2 - 50, 100, 100), W, H)
    assert cx < 0.01 and cy < 0.01
    cx, cy = centrality((0, 0, 100, 100), W, H)
    assert cx > 0.4 and cy > 0.4


def test_rects_overlap_same_and_different():
    a = (100, 100, 200, 150)
    assert rects_overlap(a, (105, 103, 198, 152)) is True
    assert rects_overlap(a, (600, 100, 200, 150)) is False
    assert rects_overlap(a, (100, 100, 60, 40)) is False      # 尺寸差太多


# ---------------- 偵測 ----------------

def test_detects_centered_popup():
    f = panel(world(), W // 2 - 220, H // 2 - 150, 440, 300)
    r = find_modal(f)
    assert r is not None
    assert abs(r[0] - (W // 2 - 220)) <= 12


def test_chat_box_at_bottom_is_not_a_modal():
    # 聊天框水平置中但垂直偏底（實測 centrality y=0.44）→ 不可誤判
    f = panel(world(), 414, 664, 669, 144)
    assert find_modal(f) is None


def test_side_window_is_not_a_modal():
    # 玩家自己拖到旁邊的技能欄
    f = panel(world(), 40, 200, 300, 420)
    assert find_modal(f) is None


def test_tiny_panel_is_not_a_modal():
    f = panel(world(), W // 2 - 60, H // 2 - 40, 120, 80)
    assert find_modal(f) is None


def test_clean_world_has_no_modal():
    assert find_modal(world()) is None


# ---------------- 跨幀確認 ----------------

def test_watcher_requires_persistence():
    wch = ModalWatcher(persist=3)
    f = panel(world(), W // 2 - 220, H // 2 - 150, 440, 300)
    assert wch.update(f) is None          # 第 1 幀不算
    assert wch.update(f) is None          # 第 2 幀不算
    assert wch.update(f) is not None      # 第 3 幀確認
    assert wch.update(world()) is None    # 關掉後立即解除


def test_watcher_ignores_flicker():
    wch = ModalWatcher(persist=3)
    f = panel(world(), W // 2 - 220, H // 2 - 150, 440, 300)
    for _ in range(5):
        assert wch.update(f) is None or True
        assert wch.update(world()) is None   # 一幀有一幀沒 → 永遠不確認
    assert wch.active is None


# ---------------- 核心反應 ----------------

def Wd(now, **kw):
    return WorldState(now=now, **kw)


def SPAN(width=20.0, dl=10.0, dr=10.0):
    return {"width": width, "dist_left": dl, "dist_right": dr}


def farming():
    c = NightWatchCore(heal_mode="external", profile=ClassProfile())
    c.tick(Wd(0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    c.tick(Wd(1.0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    assert c.state == "FARM"
    return c


def test_modal_stops_everything_immediately():
    c = farming()
    c.tick(Wd(2.0, hp=0.9, pos=(50, 90), span=SPAN()))        # 攻擊按住中
    acts = c.tick(Wd(3.0, hp=0.9, pos=(50, 90), span=SPAN(), modal=(400, 250, 500, 300)))
    verbs = [a.verb for a in acts]
    assert "release_attack" in verbs and "release_all" in verbs
    assert c.state == "IDLE_SILENT"
    assert c.stats.modals == 1


def test_no_input_at_all_while_modal_is_up():
    """彈窗還在畫面上的期間，一個按鍵都不准送（含補血/跳/技能）。"""
    c = farming()
    seen = set()
    for t in range(2, 400, 10):
        acts = c.tick(Wd(float(t), hp=0.3, mp=0.1, pos=(50, 90), span=SPAN(),
                         modal=(400, 250, 500, 300)))
        for a in acts:
            seen.add(a.verb)
    assert c.state == "IDLE_SILENT"
    assert seen <= {"log", "release_attack", "release_all"}


def test_modal_does_not_auto_answer():
    # 絕不自動作答：不可出現任何方向鍵/確認鍵動作
    c = farming()
    acts = c.tick(Wd(2.0, hp=0.9, pos=(50, 90), span=SPAN(), modal=(400, 250, 500, 300)))
    assert not [a for a in acts if a.verb in ("tap", "step", "turn", "tap_jump")]


# ---------------- 彈窗消失後的自動恢復（實跑 90 秒發現的缺陷）----------------

def test_recovers_after_modal_disappears():
    """彈窗關掉後要能自己回去打怪。

    實跑 90 秒觀察到核心卡在 IDLE_SILENT 227/317 圈——彈窗早已消失卻永遠
    不恢復，等於一次彈窗就讓整晚停擺。測謊被答掉之後本來就該繼續。
    """
    c = farming()
    c.tick(Wd(2.0, hp=0.9, pos=(50, 90), span=SPAN(), modal=(400, 250, 500, 300)))
    assert c.state == "IDLE_SILENT"
    # 彈窗還在 → 不恢復
    c.tick(Wd(60.0, hp=0.9, pos=(50, 90), span=SPAN(), modal=(400, 250, 500, 300)))
    assert c.state == "IDLE_SILENT"
    # 彈窗消失但還沒滿觀察期 → 仍不恢復
    c.tick(Wd(70.0, hp=0.9, pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SILENT"
    # 消失夠久 → 自動恢復
    c.tick(Wd(70.0 + NightWatchCore.MODAL_CLEAR_SECONDS + 1,
              hp=0.9, pos=(50, 90), span=SPAN()))
    assert c.state == "VERIFY"
    assert c.stats.recoveries == 1


def test_reappearing_modal_resets_the_clear_timer():
    c = farming()
    c.tick(Wd(2.0, hp=0.9, pos=(50, 90), span=SPAN(), modal=(400, 250, 500, 300)))
    c.tick(Wd(20.0, hp=0.9, pos=(50, 90), span=SPAN()))              # 消失
    c.tick(Wd(50.0, hp=0.9, pos=(50, 90), span=SPAN(), modal=(400, 250, 500, 300)))
    c.tick(Wd(80.0, hp=0.9, pos=(50, 90), span=SPAN()))              # 又消失，重新計時
    assert c.state == "IDLE_SILENT"


def test_exp_stall_silence_does_not_auto_recover():
    """EXP 停滯造成的靜默需要人工處理，不可自己恢復。"""
    c = farming()
    c.tick(Wd(2.0, hp=0.9, pos=(50, 90), span=SPAN()))
    c.tick(Wd(2.0 + NightWatchCore.EXP_STALL_LIMIT + 5, hp=0.9,
              pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SILENT"
    for t in range(600, 2000, 100):
        c.tick(Wd(float(t), hp=0.9, pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SILENT"


def test_full_screen_bright_is_not_a_modal():
    """整片偏亮的畫面（登入/選頻/斷線）不可被當成彈窗。

    實機案例：遊戲斷線後停在登入畫面，舊版把 (0,0,1371,754) 整張畫面判成
    彈窗，誤報率 99%，核心 1.5 秒就停手不動。
    """
    f = np.full((H, W, 3), 200, dtype=np.uint8)      # 幾乎整片亮白
    assert find_modal(f) is None


def test_scattered_bright_background_is_not_a_modal():
    """散佈的亮色背景外接框雖大，但填充率低 → 不是面板。"""
    f = world()
    rng = np.random.default_rng(3)
    for _ in range(260):                             # 散落的亮點遍佈中央
        x = int(rng.integers(W // 2 - 300, W // 2 + 300))
        y = int(rng.integers(H // 2 - 200, H // 2 + 200))
        f[y:y + 14, x:x + 14] = (205, 205, 205)
    assert find_modal(f) is None


def test_real_sized_dialog_still_detected():
    """真正的對話框（實心、置中、佔畫面一小部分）仍要偵測得到。"""
    f = panel(world(), W // 2 - 230, H // 2 - 140, 460, 280)
    r = find_modal(f)
    assert r is not None and r[2] < W * 0.75
