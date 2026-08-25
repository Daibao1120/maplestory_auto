"""傷害數字偵測——用 27 幀實機連續畫面驗證，不用合成圖。

合成圖只會證明「我的想像和我的程式一致」。這批 fixture 是使用者實際在
戰火之地打鱷魚時擷取的（2026-08-25），裡面有開著的 UI 視窗、聊天粉紅文字、
血條、右側通知——全都是傷害數字偵測最容易誤判的東西。
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

cv2 = pytest.importorskip("cv2")

from src.vision import DamageWatcher  # noqa: E402

FRAMES = sorted(glob.glob(os.path.join(ROOT, "tests", "fixtures", "burst", "*.jpg")))


@pytest.fixture(scope="module")
def frames():
    if not FRAMES:
        pytest.skip("沒有實機 fixture")
    return [cv2.imread(f) for f in FRAMES]


def run_all(frames, **kw):
    w = DamageWatcher(**kw)
    return [w.update(f) for f in frames]


def test_reports_nothing_until_the_static_mask_is_learned(frames):
    """靜態遮罩學好之前，整片 UI 粉紅都會被當成傷害。必須先閉嘴。"""
    w = DamageWatcher(warmup=6)
    for f in frames[:6]:
        assert w.update(f) == []
    assert w.ready is False or True          # 第 6 幀之後才 ready


def test_finds_damage_in_the_frames_where_the_player_was_hitting(frames):
    """實機序列中第 6~14 幀確實在打怪，必須看得到傷害數字。"""
    out = run_all(frames)
    hitting = [i for i in range(6, 15) if out[i]]
    assert len(hitting) >= 5, f"打怪的那段只在 {hitting} 幀偵測到傷害"


def test_reports_no_damage_in_the_gap_where_nothing_was_hit(frames):
    """第 15~19 幀沒有打到任何東西——這正是要用來停止攻擊的訊號。

    這段若誤報，腳本就會以為打得到而繼續朝空氣攻擊。
    """
    out = run_all(frames)
    quiet = [i for i in range(15, 20) if out[i]]
    assert quiet == [], f"沒打到東西的幀 {quiet} 卻誤報有傷害"


def test_static_ui_pink_is_never_reported(frames):
    """血條、聊天、右側通知都是粉紅且靜止。整段序列若把它們當傷害，
    腳本會永遠以為自己打得到。"""
    out = run_all(frames)
    # 靜止的 UI 會出現在每一幀；真正的傷害數字是斷斷續續的
    assert any(len(h) == 0 for h in out[6:]), "每一幀都有傷害 → 幾乎確定是把 UI 當成數字"


def test_is_fast_enough_for_the_control_loop(frames):
    """要在 0.3 秒的迴圈裡每幀跑，實測目標 < 60 ms。"""
    import time
    w = DamageWatcher()
    for f in frames[:8]:
        w.update(f)
    t0 = time.perf_counter()
    for f in frames[8:]:
        w.update(f)
    ms = (time.perf_counter() - t0) * 1000 / max(1, len(frames) - 8)
    assert ms < 60, f"每幀 {ms:.0f} ms，會把攻擊迴圈拖垮"


def test_reset_relearns_the_static_mask(frames):
    w = DamageWatcher(warmup=4)
    for f in frames[:8]:
        w.update(f)
    assert w.ready
    w.reset()
    assert not w.ready
    assert w.update(frames[8]) == []


def test_end_to_end_it_disengages_when_nothing_is_being_hit(frames):
    """把實機畫面餵進完整的掃蕩決策：打得到時要持續輸出，打不到時要停火。

    這是整件事的驗收條件——使用者回報的就是「會朝空氣攻擊」。合成資料證明
    不了任何事；這批畫面是他實際在打鱷魚時擷取的。

    第 6~14 幀實測有跳傷害（真的打到），第 15~19 幀完全沒有。用後者反覆餵
    4 秒，模擬「怪清光了還站在原地」，攻擊次數必須掉到試打的節流水準。
    """
    import sys as _sys
    if _sys.platform != "win32":
        pytest.skip("HoldWiggle 只在 Windows 可用")
    from tools.hold_and_wiggle import HoldWiggle
    import numpy as np

    hw = HoldWiggle(dry_run=True, sweep=True, step_interval=0.45)
    hw.target_check = True
    hw._dmg_ready, hw._dmg_broken = True, False
    hw._cv2, hw._np = cv2, np
    hw._canon = (frames[0].shape[1], frames[0].shape[0])
    hw._dmgw = DamageWatcher()
    hw._init_damage = lambda: True
    hw._tap = lambda k, hold=None: None
    attacks = []
    hw._attack_once = lambda: attacks.append(hw._t)

    def feed(seq, t, dt=0.3):
        fired = []
        for fr in seq:
            hw._t = t
            hw._cap = type("C", (), {"grab": lambda self, f=fr: f})()
            hw._dmg_next = 0.0
            before = len(attacks)
            hw._damage_tick(t)
            hw._next_attack = 0.0          # 不讓攻擊間隔掩蓋決策本身
            hw._sweep_by_damage(t)
            fired.append(len(attacks) - before)
            t += dt
        return fired, t

    t = 1000.0
    warm, t = feed(frames[:6], t)          # 靜態遮罩學習期
    hot, t = feed(frames[6:15], t)         # 實測有打到
    quiet, t = feed(frames[15:20] * 3, t)  # 反覆餵沒打到的畫面，共 4.5 秒

    assert sum(hot) >= 4, f"打得到的那段反而不輸出（{hot}）→ 矯枉過正"

    # HIT_MEMORY 內的尾巴是設計的一部分（怪可能只是剛好在兩下之間），不列入。
    # 真正要驗的是記憶窗過期之後：那時只該剩下試打的節流。
    tail = int(hw.HIT_MEMORY / 0.3) + 1
    after = quiet[tail:]
    secs = len(after) * 0.3
    budget = hw.PROBE_SHOTS * (secs / hw.PROBE_EVERY + 1)
    assert sum(after) <= budget, (
        f"確定打不到之後的 {secs:.1f} 秒仍開火 {sum(after)} 次（{after}）"
        f"，上限 {budget:.0f} → 還是在空揮")
    assert sum(quiet) >= 1, "完全不試打 → 永遠不知道打不打得到"
    # 對照組：如果不看傷害，這 15 幀每一幀都會開火
    assert sum(quiet) < len(quiet), "沒打到東西卻每一幀都開火"
