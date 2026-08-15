"""守夜核心（NightWatchCore）全面測試：每一條安全鐵則都有對應案例。

純狀態機、時間由 WorldState.now 控制，整晚流程可在毫秒內模擬。
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


def verbs(acts):
    return [a.verb for a in acts]


def make_farming_core(t0=0.0):
    """建一個已進入 FARM 的核心（藥效已驗證）。"""
    c = NightWatchCore(potion_key="delete")
    c.tick(W(t0, hp=0.90, pos=(50, 90), span=SPAN()))      # VERIFY: 按藥驗證
    c.tick(W(t0 + 1.0, hp=0.95, pos=(50, 90), span=SPAN()))  # HP 上升 → 有效
    c.tick(W(t0 + 1.2, hp=0.95, pos=(50, 90), span=SPAN()))  # → DESCEND → FARM
    assert c.state == "FARM", c.state
    return c


# ============================================================
#  VERIFY：補血鍵驗證
# ============================================================

def test_verify_potion_works_then_descend():
    c = NightWatchCore()
    acts = c.tick(W(0, hp=0.90, pos=(50, 90), span=SPAN(3)))
    assert "tap" in verbs(acts)                 # 按了補血鍵
    c.tick(W(1.0, hp=0.95, pos=(50, 90), span=SPAN(3)))
    assert c.potion_works is True
    c.tick(W(1.2, hp=0.95, pos=(50, 90), span=SPAN(3)))
    assert c.state == "DESCEND"


def test_verify_potion_fails_then_idle_safe_never_farm():
    c = NightWatchCore()
    c.tick(W(0, hp=0.90, pos=(50, 90), span=SPAN()))
    for t in (1.0, 2.0, 3.0, 4.0):
        c.tick(W(t, hp=0.90, pos=(50, 90), span=SPAN()))   # HP 不動 → 無效
    assert c.potion_works is False
    assert c.state == "IDLE_SAFE"
    # 之後怎麼餵都不准進 FARM
    for t in range(5, 60):
        c.tick(W(float(t), hp=0.90, pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SAFE"


def test_verify_near_full_hp_defers_verification():
    c = NightWatchCore()
    c.tick(W(0, hp=0.99, pos=(50, 90), span=SPAN(3)))
    assert c.state == "DESCEND"
    assert c.potion_works is None               # 尚未驗證


def test_verify_no_hp_reading_goes_idle():
    c = NightWatchCore()
    c.tick(W(0, hp=None, pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SAFE"


# ============================================================
#  FARM 中的首次用藥＝順便驗證；無效即撤退
# ============================================================

def test_deferred_verification_failure_aborts_farm():
    c = NightWatchCore()
    c.tick(W(0, hp=0.99, pos=(50, 90), span=SPAN(3)))      # 延後驗證 → DESCEND
    c.tick(W(1, hp=0.99, pos=(50, 90), span=SPAN(20)))     # 寬平台 → FARM
    assert c.state == "FARM"
    c.tick(W(2, hp=0.60, pos=(50, 90), span=SPAN(20)))     # 掉血 → 首次用藥+驗證
    for t in (3.0, 4.0, 5.0, 6.0):
        c.tick(W(t, hp=0.60, pos=(50, 90), span=SPAN(20)))  # 沒回升 → 無效
    assert c.potion_works is False
    assert c.state == "IDLE_SAFE"


def test_potion_never_pressed_again_after_proven_broken():
    c = NightWatchCore()
    c.tick(W(0, hp=0.90, pos=(50, 90), span=SPAN()))
    for t in (1.0, 2.0, 3.0, 4.0):
        c.tick(W(t, hp=0.90, pos=(50, 90), span=SPAN()))
    assert c.potion_works is False
    acts = c.tick(W(10, hp=0.30, pos=(50, 90), span=SPAN()))
    assert "tap" not in verbs(acts)             # 明知無效不再狂按


# ============================================================
#  DESCEND
# ============================================================

def test_descend_steps_toward_nearer_edge_until_wide():
    c = NightWatchCore()
    c.tick(W(0, hp=0.95, pos=(50, 76), span=SPAN(4, 1.0, 3.0)))
    c.tick(W(1, hp=0.98, pos=(50, 76), span=SPAN(4, 1.0, 3.0)))
    c.tick(W(1.2, hp=0.98, pos=(50, 76), span=SPAN(4, 1.0, 3.0)))
    assert c.state == "DESCEND"
    acts = c.tick(W(2, hp=0.98, pos=(50, 76), span=SPAN(4, 1.0, 3.0)))
    steps = [a for a in acts if a.verb == "step"]
    assert steps and steps[0].arg[0] == "left"  # 往較近的邊走出去
    acts = c.tick(W(4, hp=0.98, pos=(50, 90), span=SPAN(20)))
    assert c.state == "FARM"


def test_descend_gives_up_after_max_attempts():
    c = NightWatchCore()
    c.tick(W(0, hp=0.99, pos=(50, 76), span=SPAN(3)))
    t = 1.0
    for _ in range(NightWatchCore.MAX_DESCENTS + 2):
        c.tick(W(t, hp=0.99, pos=(50, 76), span=SPAN(3)))
        t += 1
    assert c.state == "IDLE_SAFE"


# ============================================================
#  FARM：攻擊、警衛、掉層、防失效
# ============================================================

def test_farm_holds_attack_and_represses():
    c = make_farming_core()
    acts = c.tick(W(2.0, hp=0.95, pos=(50, 90), span=SPAN()))
    assert "hold_attack" in verbs(acts)
    acts = c.tick(W(30.0, hp=0.95, pos=(50, 90), span=SPAN()))
    assert "repress_attack" in verbs(acts)


def test_farm_guard_pushes_back_near_edge():
    c = make_farming_core()
    c.tick(W(2.0, hp=0.95, pos=(50, 90), span=SPAN()))
    acts = c.tick(W(6.0, hp=0.95, pos=(58, 90), span=SPAN(20, 1.0, 19.0)))
    steps = [a for a in acts if a.verb == "step"]
    assert steps and steps[0].arg[0] == "right"     # 離左緣太近 → 往右推回
    assert "release_attack" in verbs(acts)          # 推回前先放開攻擊鍵
    assert "release_moves" in verbs(acts)           # 推完保險放開方向鍵


def test_farm_tier_drop_rehomes_on_wide_platform():
    c = make_farming_core()
    c.tick(W(2.0, hp=0.95, pos=(50, 90), span=SPAN()))
    c.tick(W(3.0, hp=0.95, pos=(50, 94), span=SPAN(18)))    # 掉層但夠寬
    assert c.state == "FARM"


def test_farm_tier_drop_to_narrow_platform_goes_idle():
    c = make_farming_core()
    c.tick(W(2.0, hp=0.95, pos=(50, 90), span=SPAN()))
    c.tick(W(3.0, hp=0.95, pos=(50, 94), span=SPAN(4, 2, 2)))  # 掉到小島
    assert c.state == "IDLE_SAFE"


def test_farm_low_hp_sustained_aborts():
    c = make_farming_core()
    c.tick(W(2.0, hp=0.95, pos=(50, 90), span=SPAN()))
    t = 3.0
    c.tick(W(t, hp=0.40, pos=(50, 90), span=SPAN()))
    for dt in range(1, 15):
        c.tick(W(t + dt, hp=0.40, pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SAFE"


def test_farm_momentary_low_hp_does_not_abort():
    c = make_farming_core()
    c.tick(W(2.0, hp=0.95, pos=(50, 90), span=SPAN()))
    c.tick(W(3.0, hp=0.40, pos=(50, 90), span=SPAN()))       # 瞬間低血
    c.tick(W(4.0, hp=0.80, pos=(50, 90), span=SPAN()))       # 馬上回復
    for t in range(5, 20):
        c.tick(W(float(t), hp=0.80, pos=(50, 90), span=SPAN()))
    assert c.state == "FARM"


def test_farm_exp_stall_goes_idle():
    c = make_farming_core()
    t = 2.0
    c.tick(W(t, hp=0.95, pos=(50, 90), span=SPAN()))
    t += NightWatchCore.EXP_STALL_LIMIT + 5
    c.tick(W(t, hp=0.95, pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SAFE"                            # 測謊/異常保險


def test_farm_exp_flow_prevents_stall_abort():
    c = make_farming_core()
    t = 2.0
    for _ in range(20):
        c.tick(W(t, hp=0.95, pos=(50, 90), span=SPAN(), exp_changed=True))
        t += 60.0                                            # 每分鐘都有進帳
    assert c.state == "FARM"


# ============================================================
#  使用者優先與環境安全
# ============================================================

def test_user_touch_releases_everything_and_yields():
    c = make_farming_core()
    c.tick(W(2.0, hp=0.95, pos=(50, 90), span=SPAN()))       # 攻擊鍵按住中
    acts = c.tick(W(3.0, hp=0.95, pos=(50, 90), span=SPAN(), user_touch=True))
    assert "release_attack" in verbs(acts)
    assert "release_all" in verbs(acts)
    acts = c.tick(W(10.0, hp=0.95, pos=(50, 90), span=SPAN()))
    assert acts == []                                        # 讓手期間完全沉默
    acts = c.tick(W(3.0 + 46.0, hp=0.95, pos=(50, 90), span=SPAN()))
    assert "hold_attack" in verbs(acts)                      # 讓手期滿恢復


def test_no_keys_when_not_foreground_or_black_or_no_window():
    c = make_farming_core()
    c.tick(W(2.0, hp=0.95, pos=(50, 90), span=SPAN()))
    for kw in ({"fg": False}, {"frame_ok": False}, {"window": False}):
        acts = c.tick(W(5.0, hp=0.95, pos=(50, 90), span=SPAN(), **kw))
        assert all(a.verb in ("release_attack", "log") for a in acts), acts


def test_position_lost_too_long_goes_idle():
    c = make_farming_core()
    t = 2.0
    c.tick(W(t, hp=0.95, pos=(50, 90), span=SPAN()))
    for dt in range(0, 70, 5):
        c.tick(W(t + dt, hp=0.95, pos=None, span=None))
    assert c.state == "IDLE_SAFE"


# ============================================================
#  指令、時限與收尾
# ============================================================

def test_cmd_stop_releases_all_and_stops():
    c = make_farming_core()
    c.tick(W(2.0, hp=0.95, pos=(50, 90), span=SPAN()))
    acts = c.tick(W(3.0, hp=0.95, pos=(50, 90), span=SPAN(), cmd="stop"))
    assert c.state == "STOPPED"
    assert "release_all" in verbs(acts)
    assert c.tick(W(4.0)) == []                              # 停了就不再動作


def test_cmd_idle_and_farm_roundtrip():
    c = make_farming_core()
    c.tick(W(2.0, hp=0.95, pos=(50, 90), span=SPAN(), cmd="idle"))
    assert c.state == "IDLE_SAFE"
    c.tick(W(3.0, hp=0.95, pos=(50, 90), span=SPAN(), cmd="farm"))
    # 藥效先前已證實 → 不必重驗，直接進 DESCEND
    assert c.state == "DESCEND"


def test_time_limit_stops():
    c = NightWatchCore(max_seconds=100)
    c.tick(W(0, hp=0.99, pos=(50, 90), span=SPAN(3)))
    c.tick(W(101, hp=0.99, pos=(50, 90), span=SPAN(3)))
    assert c.state == "STOPPED"


def test_idle_safe_only_antiidle_jumps():
    c = NightWatchCore()
    c.tick(W(0, hp=None))                                    # → IDLE_SAFE
    assert c.state == "IDLE_SAFE"
    seen = set()
    for t in range(1, 700, 10):
        for a in c.tick(W(float(t), hp=None)):
            seen.add(a.verb)
    assert "tap_jump" in seen                                # 有防閒置跳
    assert "hold_attack" not in seen and "step" not in seen  # 絕不攻擊/走動


# ============================================================
#  整晚模擬（情境劇本）：不變量檢查
# ============================================================

def test_full_night_scenario_invariants():
    """劇本：驗藥→下平台→農 2 小時（間歇 EXP/擊退/掉層）→ 使用者半夜介入
    → 恢復 → 時限收工。不變量：pos=None 或非前景時絕無 step/tap。"""
    c = NightWatchCore(max_seconds=3600 * 3)
    t = 0.0
    violations = []

    def step_check(w, acts):
        if (w.pos is None or not w.fg or not w.frame_ok) and any(
                a.verb in ("step", "tap", "hold_attack") for a in acts):
            violations.append((w.now, verbs(acts)))

    w = W(t, hp=0.90, pos=(50, 76), span=SPAN(4, 2, 2))
    step_check(w, c.tick(w))
    t += 1
    w = W(t, hp=0.96, pos=(50, 76), span=SPAN(4, 2, 2))
    step_check(w, c.tick(w))
    # 下平台
    for _ in range(4):
        t += 1.5
        w = W(t, hp=0.96, pos=(50, 80), span=SPAN(6, 2, 3))
        step_check(w, c.tick(w))
    t += 1.5
    w = W(t, hp=0.96, pos=(50, 94), span=SPAN(20))
    step_check(w, c.tick(w))
    assert c.state == "FARM"
    # 農 2 小時：每 30 秒一圈，週期性 EXP、偶爾擊退到邊緣、偶爾讀不到位置
    for i in range(240):
        t += 30
        pos = (50 + (i % 5), 94)
        span = SPAN(20, 3 + (i % 5), 17 - (i % 5))
        if i % 40 == 17:
            w = W(t, hp=0.9, pos=None, span=None, exp_changed=False)
        else:
            w = W(t, hp=0.85 + 0.1 * ((i % 3) == 0), pos=pos, span=span,
                  exp_changed=(i % 2 == 0))
        step_check(w, c.tick(w))
        assert c.state == "FARM", (i, c.state)
    # 半夜使用者介入
    w = W(t + 1, hp=0.9, pos=(50, 94), span=SPAN(20), user_touch=True)
    step_check(w, c.tick(w))
    # 時限到 → 收工
    w = W(3600 * 3 + 10, hp=0.9, pos=(50, 94), span=SPAN(20))
    c.tick(w)
    assert c.state == "STOPPED"
    assert not violations, violations
