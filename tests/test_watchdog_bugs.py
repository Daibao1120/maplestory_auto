"""三個實跑會出事、但單元測試沒涵蓋到的缺陷（先證明，再修）。

這些不是理論問題：使用者連續三晚回報「掛了整夜、經驗值 0、log 一片空白」，
下面每一項都能單獨造成那個結果。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.overnight import NightWatchCore, WorldState  # noqa: E402


def W(now, **kw):
    return WorldState(now=now, **kw)


def SPAN(width=20.0, dl=10.0, dr=10.0):
    return {"width": width, "dist_left": dl, "dist_right": dr}


def farming():
    c = NightWatchCore(potion_key="delete")
    c.tick(W(0.0, hp=0.90, pos=(50, 76), span=SPAN(20)))
    c.tick(W(1.0, hp=0.95, pos=(50, 76), span=SPAN(20)))
    c.tick(W(1.5, hp=0.95, pos=(50, 90), span=SPAN(20)))
    assert c.state == "FARM"
    return c


def test_exp_stall_watchdog_actually_fires_when_nothing_is_killed():
    """一整夜都沒有經驗值進帳 → 必須進入靜默，而不是繼續按 ctrl 到天亮。

    缺陷：沒偵測到怪時的「盲翻方向」分支每 20 秒會把 _last_exp_ts 重設成現在，
    而 480 秒的停滯 watchdog 看的是同一個時鐘 → 那個 watchdog 永遠不會觸發。
    """
    c = farming()
    t = 2.0
    while t < 900.0:                       # 15 分鐘完全沒有進帳
        c.tick(W(t, hp=0.95, pos=(50, 90), span=SPAN(20), exp_changed=False))
        if c.state == "IDLE_SILENT":
            break
        t += 1.0
    assert c.state == "IDLE_SILENT", (
        f"跑了 {t:.0f} 秒零進帳仍停在 {c.state}——停滯 watchdog 形同虛設")
    assert c._silent_reason == "exp_stall"


def test_blind_flip_still_happens_while_stall_clock_keeps_ageing():
    """修正不可以把盲翻改壞：翻向仍要每 20 秒發生，但不能重置停滯時鐘。"""
    c = farming()
    flips = 0
    for i in range(1, 200):
        acts = c.tick(W(1.5 + i, hp=0.95, pos=(50, 90), span=SPAN(20)))
        flips += sum(1 for a in acts if a.verb == "turn")
        if c.state != "FARM":
            break
    assert flips >= 3, f"盲翻沒在運作（只翻了 {flips} 次）"


def test_stall_retries_are_bounded_then_it_stops_for_good():
    """慢但有效的掛機不該被一次誤判停整夜；但一直沒進帳也不能無限重試。

    前提是彈窗偵測確實在跑（modal_ok=True）且畫面上沒有彈窗——沒有這個證據
    就不准恢復，見 test_stall_never_recovers_without_working_modal_detection。
    """
    c = farming()
    t, seen_verify = 2.0, 0
    while t < 6000.0:
        c.tick(W(t, hp=0.95, pos=(50, 90), span=SPAN(20), exp_changed=False,
                 modal_ok=True))
        if c.state == "VERIFY":
            seen_verify += 1
        t += 1.0
    assert seen_verify >= 1, "完全沒重試——一次誤判就報銷整夜"
    assert c.state == "IDLE_SILENT", "一直沒進帳卻還在動——重試沒有上限"
    assert c._stall_retries == c.EXP_STALL_RETRY_MAX


def test_real_exp_resets_the_retry_budget():
    """真的有進帳＝真的在打怪 → 之前用掉的重試額度要還回來。"""
    c = farming()
    c._stall_retries = 2
    c.tick(W(3.0, hp=0.95, pos=(50, 90), span=SPAN(20), exp_changed=True))
    assert c._stall_retries == 0


def test_stall_never_recovers_without_working_modal_detection():
    """彈窗偵測沒接上時，停滯靜默必須永遠靜默。

    停滯靜默是「看不見的測謊視窗」的代理偵測。偵測沒在跑就自動恢復，等於
    整夜對著測謊視窗按 ctrl——寧可白掛一夜，不冒被標記的險。
    """
    c = farming()
    t = 2.0
    while t < 6000.0:                       # modal_ok 預設 False＝偵測沒接上
        acts = c.tick(W(t, hp=0.95, pos=(50, 90), span=SPAN(20)))
        if c.state == "IDLE_SILENT":
            assert {a.verb for a in acts} <= {"log", "release_all", "release_attack"},                 "靜默期間送出了輸入（放開按鍵可以，按下去不行）"
        t += 1.0
    assert c.state == "IDLE_SILENT"
    assert c._stall_retries == 0            # 一次都沒恢復


def test_stall_does_not_recover_while_a_popup_is_actually_visible():
    """偵測在跑、而且真的看到彈窗 → 更不可以恢復。"""
    c = farming()
    t = 2.0
    while t < 3000.0:
        c.tick(W(t, hp=0.95, pos=(50, 90), span=SPAN(20),
                 modal_ok=True, modal=(10, 10, 200, 200)))
        t += 1.0
    assert c.state == "IDLE_SILENT"
