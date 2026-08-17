"""守夜核心 v2 全面測試：28 項審查發現逐一釘死＋邊界值＋整晚模擬。"""
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


def make_farming_core():
    c = NightWatchCore(potion_key="delete")
    c.tick(W(0.0, hp=0.90, pos=(50, 76), span=SPAN(20)))
    c.tick(W(1.0, hp=0.95, pos=(50, 76), span=SPAN(20)))    # 驗證成功 → DESCEND
    assert c.state == "DESCEND"
    c.tick(W(1.5, hp=0.95, pos=(50, 90), span=SPAN(20)))    # 寬平台 → FARM
    assert c.state == "FARM"
    return c


def start_attacking(c, t):
    acts = c.tick(W(t, hp=0.95, pos=(50, 90), span=SPAN()))
    assert "hold_attack" in verbs(acts)
    return acts


# ============================================================
#  補血鍵驗證（可重試、遲到的成功、絕望補血）
# ============================================================

def test_verify_success_then_descend():
    c = NightWatchCore()
    acts = c.tick(W(0, hp=0.90, pos=(50, 90), span=SPAN(3)))
    assert "tap" in verbs(acts)
    c.tick(W(1.0, hp=0.95, pos=(50, 90), span=SPAN(3)))
    assert c.potion_works is True and c.state == "DESCEND"


def test_verify_first_failure_is_inconclusive_then_retries():
    c = NightWatchCore(potion_candidates=("delete",))   # 單一候選：隔離判死路徑
    c.tick(W(0, hp=0.90, pos=(50, 90), span=SPAN()))
    # 窗過、沒回升 → 第 1 次失敗（不確定），同圈立即重試第 2 次
    acts = c.tick(W(5.0, hp=0.90, pos=(50, 90), span=SPAN()))
    assert c.potion_works is None and "tap" in verbs(acts)
    c.tick(W(10.0, hp=0.90, pos=(50, 90), span=SPAN()))      # 第 2 次也失敗 → 判無效
    assert c.potion_works is False
    assert c.state == "IDLE_SAFE"


def test_verify_late_tick_success_not_condemned():
    # 驗證窗被吞（讓手/切窗/慢幀）→ 遲到的那一筆 HP 有回升就要算成功
    c = NightWatchCore()
    c.tick(W(0, hp=0.90, pos=(50, 90), span=SPAN()))
    c.tick(W(30.0, hp=0.97, pos=(50, 90), span=SPAN()))      # 遠超窗但 HP 升了
    assert c.potion_works is True


def test_condemned_potion_gets_retry_after_cooldown():
    c = NightWatchCore(potion_candidates=("delete",))
    c.tick(W(0, hp=0.90, pos=(50, 90), span=SPAN()))
    c.tick(W(5.0, hp=0.90, pos=(50, 90), span=SPAN()))
    c.tick(W(6.0, hp=0.90, pos=(50, 90), span=SPAN()))
    c.tick(W(11.0, hp=0.90, pos=(50, 90), span=SPAN()))
    assert c.potion_works is False
    t = 11.0 + NightWatchCore.POTION_RETRY_AFTER + 1
    c.tick(W(t, hp=0.90, pos=(50, 90), span=SPAN()))
    assert c.potion_works is None                            # 重新給機會


def test_external_heal_mode_never_touches_potion_and_farms_directly():
    # 寵物負責補血：核心不按任何藥水鍵、不驗證、不因低血停手
    c = NightWatchCore(heal_mode="external")
    acts = c.tick(W(0, hp=0.62, pos=(50, 76), span=SPAN(20)))
    assert "tap" not in verbs(acts)                 # 不碰藥水鍵
    assert c.state == "DESCEND"                     # 直接開工
    c.tick(W(1.0, hp=0.62, pos=(50, 90), span=SPAN(20)))
    assert c.state == "FARM"
    for dt in range(0, 60, 3):                      # 血一直低也照打
        acts = c.tick(W(2.0 + dt, hp=0.35, pos=(50, 90), span=SPAN(),
                        exp_changed=True))
        assert "tap" not in verbs(acts)
    assert c.state == "FARM"


def test_external_heal_mode_last_resort_still_protects():
    c = NightWatchCore(heal_mode="external", last_resort_hp=0.20)
    c.tick(W(0, hp=0.90, pos=(50, 90), span=SPAN(20)))
    c.tick(W(1.0, hp=0.90, pos=(50, 90), span=SPAN(20)))
    assert c.state == "FARM"
    for dt in range(0, 30, 2):                      # 掉到 15% 且持續
        c.tick(W(2.0 + dt, hp=0.15, pos=(50, 90), span=SPAN(), exp_changed=True))
    assert c.state != "FARM"                        # 最後保險仍會停手


def test_farm_faces_the_side_with_more_monsters():
    c = NightWatchCore(heal_mode="external")
    c.tick(W(0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    c.tick(W(1.0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    assert c.state == "FARM"
    acts = c.tick(W(2.0, hp=0.9, pos=(50, 90), span=SPAN(), mon_left=3, mon_right=1))
    turns = [a.arg for a in acts if a.verb == "turn"]
    assert turns == ["left"] and c.facing == "left"
    # 怪跑到右邊 → 轉過去
    acts = c.tick(W(5.0, hp=0.9, pos=(50, 90), span=SPAN(), mon_left=0, mon_right=4))
    assert [a.arg for a in acts if a.verb == "turn"] == ["right"]
    # 同一側不重複轉身
    acts = c.tick(W(8.0, hp=0.9, pos=(50, 90), span=SPAN(), mon_left=0, mon_right=4))
    assert not any(a.verb == "turn" for a in acts)
    # 兩邊一樣多／沒怪 → 維持面向
    acts = c.tick(W(11.0, hp=0.9, pos=(50, 90), span=SPAN(), mon_left=2, mon_right=2))
    assert not any(a.verb == "turn" for a in acts)
    assert c.facing == "right"


def test_exp_feedback_flips_facing_when_no_gain():
    # 沒有怪物偵測資訊時，靠經驗值回饋換邊（不依賴角色螢幕座標）
    c = NightWatchCore(heal_mode="external")
    c.tick(W(0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    c.tick(W(1.0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    assert c.state == "FARM"
    c.tick(W(2.0, hp=0.9, pos=(50, 90), span=SPAN(), exp_changed=True))
    before = c.facing
    t = 2.0 + NightWatchCore.EXP_FLIP_AFTER + 1
    acts = c.tick(W(t, hp=0.9, pos=(50, 90), span=SPAN()))
    turns = [a.arg for a in acts if a.verb == "turn"]
    assert turns and c.facing != before               # 沒進帳 → 換邊
    # 換邊後給完整觀察窗，不會每圈亂翻
    acts = c.tick(W(t + 2, hp=0.9, pos=(50, 90), span=SPAN()))
    assert not any(a.verb == "turn" for a in acts)


def test_exp_feedback_keeps_facing_while_gaining():
    c = NightWatchCore(heal_mode="external")
    c.tick(W(0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    c.tick(W(1.0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    t = 2.0
    for _ in range(20):                               # 一直有經驗值 → 不換邊
        acts = c.tick(W(t, hp=0.9, pos=(50, 90), span=SPAN(), exp_changed=True))
        assert not any(a.verb == "turn" for a in acts)
        t += 10.0
    assert c.state == "FARM"


def test_monster_detection_overrides_exp_feedback():
    # 有怪物偵測資訊時以它為準（更即時）
    c = NightWatchCore(heal_mode="external")
    c.tick(W(0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    c.tick(W(1.0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    t = 2.0 + NightWatchCore.EXP_FLIP_AFTER + 1
    acts = c.tick(W(t, hp=0.9, pos=(50, 90), span=SPAN(), mon_left=5, mon_right=0))
    assert [a.arg for a in acts if a.verb == "turn"] == ["left"]


def test_turn_is_rate_limited():
    c = NightWatchCore(heal_mode="external")
    c.tick(W(0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    c.tick(W(1.0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    c.tick(W(2.0, hp=0.9, pos=(50, 90), span=SPAN(), mon_left=3))
    acts = c.tick(W(2.5, hp=0.9, pos=(50, 90), span=SPAN(), mon_right=3))
    assert not any(a.verb == "turn" for a in acts)   # 1 秒內不連續轉身


def test_potion_key_auto_discovery_cycles_until_one_heals():
    # 不知道藥水放哪一格 → 自己換鍵試，直到 HP 真的回升為止（不必問使用者）
    c = NightWatchCore(potion_key="delete",
                       potion_candidates=("delete", "insert", "home"))
    taps = []
    t = 0.0
    healed_key = "home"
    hp = 0.90
    for _ in range(40):
        acts = c.tick(W(t, hp=hp, pos=(50, 90), span=SPAN(20)))
        for a in acts:
            if a.verb == "tap":
                taps.append(a.arg)
                if a.arg == healed_key:      # 只有這個鍵真的有藥
                    hp = 0.98
        t += 3.0
        if c.potion_works:
            break
    assert c.potion_works is True
    assert c.potion_key == healed_key
    assert "delete" in taps and "insert" in taps   # 前兩格都試過才換到對的


def test_potion_all_keys_fail_goes_idle_and_retries_later():
    c = NightWatchCore(potion_key="delete", potion_candidates=("delete", "insert"))
    t = 0.0
    for _ in range(30):
        c.tick(W(t, hp=0.90, pos=(50, 90), span=SPAN(20)))
        t += 3.0
        if c.potion_works is False:
            break
    assert c.potion_works is False
    assert c.state == "IDLE_SAFE"
    c.tick(W(t + NightWatchCore.POTION_RETRY_AFTER + 1, hp=0.90,
             pos=(50, 90), span=SPAN(20)))
    assert c.potion_works is None                  # 隔一段時間整輪重掃
    assert c.potion_key == "delete"


def test_desperate_potion_taps_even_when_condemned():
    c = make_farming_core()
    c.potion_works = False
    acts = c.tick(W(5.0, hp=0.40, pos=(50, 90), span=SPAN()))
    assert "tap" in verbs(acts)                              # 絕望補血照按


def test_first_farm_tick_can_tap_potion_at_time_zero():
    # _last_potion 用 None 哨兵：模擬時間從 0 開始也不會被冷卻擋住
    c = NightWatchCore()
    c.tick(W(0, hp=0.99, pos=(50, 90), span=SPAN(20)))       # 延後驗證 → DESCEND
    c.tick(W(0.5, hp=0.99, pos=(50, 90), span=SPAN(20)))     # → FARM
    assert c.state == "FARM"
    acts = c.tick(W(1.0, hp=0.60, pos=(50, 90), span=SPAN(20)))
    assert "tap" in verbs(acts)


def test_farm_verification_releases_attack_and_pauses_attack():
    c = NightWatchCore()
    c.tick(W(0, hp=0.99, pos=(50, 90), span=SPAN(20)))
    c.tick(W(0.5, hp=0.99, pos=(50, 90), span=SPAN(20)))
    start_attacking(c, 1.0)
    acts = c.tick(W(2.0, hp=0.60, pos=(50, 90), span=SPAN(20)))
    assert "tap" in verbs(acts) and "release_attack" in verbs(acts)
    acts = c.tick(W(3.0, hp=0.60, pos=(50, 90), span=SPAN(20)))
    assert "hold_attack" not in verbs(acts)                  # 驗證期間不進攻


# ============================================================
#  HP 感知盲區（審查主要致死點）
# ============================================================

def test_farm_hp_none_watchdog_aborts():
    c = make_farming_core()
    start_attacking(c, 2.0)
    t = 3.0
    for dt in range(0, 40, 2):
        c.tick(W(t + dt, hp=None, pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SAFE"                            # 30s 看門狗


def test_hp_none_freezes_low_hp_fuse_instead_of_reset():
    # 0.40/None 交錯：低血計時凍結而非歸零 → 仍會在 12s 後撤退
    #（撤退後因 HP 危險會再升級成 IDLE_SILENT，兩者都算保護生效）
    c = make_farming_core()
    start_attacking(c, 2.0)
    t = 3.0
    for i in range(16):
        hp = 0.40 if i % 2 == 0 else None
        c.tick(W(t + i, hp=hp, pos=(50, 90), span=SPAN()))
    assert c.state in ("IDLE_SAFE", "IDLE_SILENT")
    assert c.state != "FARM"


# ============================================================
#  平台/位置感知盲區
# ============================================================

def test_farm_span_none_releases_attack_immediately():
    c = make_farming_core()
    start_attacking(c, 2.0)
    acts = c.tick(W(3.0, hp=0.95, pos=(50, 90), span=None))
    assert "release_attack" in verbs(acts)                   # 立刻放開，不等 60s


def test_farm_span_none_watchdog_aborts():
    c = make_farming_core()
    t = 2.0
    for dt in range(0, 60, 3):
        c.tick(W(t + dt, hp=0.95, pos=(50, 90), span=None))
    assert c.state == "IDLE_SAFE"


def test_farm_pos_none_releases_attack_immediately():
    c = make_farming_core()
    start_attacking(c, 2.0)
    acts = c.tick(W(3.0, hp=0.95, pos=None, span=None))
    assert "release_attack" in verbs(acts)


def test_pos_lost_boundary_exactly_60s_not_yet():
    # span 給有效值以隔離「pos 看門狗」的邊界（span 看門狗 45s 會先響）
    c = make_farming_core()
    c.tick(W(2.0, hp=0.95, pos=None, span=SPAN()))           # since=2.0
    c.tick(W(62.0, hp=0.95, pos=None, span=SPAN()))          # 60.0s：還不到（嚴格 >）
    assert c.state == "FARM"
    c.tick(W(62.2, hp=0.95, pos=None, span=SPAN()))
    assert c.state == "IDLE_SAFE"


# ============================================================
#  EXP 停滯 → 危險靜默；IDLE_SILENT 零輸入
# ============================================================

def test_exp_stall_goes_silent_not_safe():
    c = make_farming_core()
    start_attacking(c, 2.0)
    acts = c.tick(W(2.0 + NightWatchCore.EXP_STALL_LIMIT + 5,
                    hp=0.95, pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SILENT"
    assert "release_attack" in verbs(acts)


def test_low_hp_debounce_window_is_honored():
    # 低血 11.5 秒（未滿 12）不得撤退——釘住去抖常數本身
    c = make_farming_core()
    start_attacking(c, 2.0)
    t = 3.0
    for dt in (0, 2, 4, 6, 8, 10, 11.5):
        c.tick(W(t + dt, hp=0.44, pos=(50, 90), span=SPAN()))
        assert c.state == "FARM", dt
    c.tick(W(t + 12.5, hp=0.44, pos=(50, 90), span=SPAN()))
    assert c.state != "FARM"                                 # 超過 12s 才撤


def test_idle_silent_stays_silent_even_with_stimuli():
    # 給滿各種刺激（低血、buff、到期的挪步/跳計時）仍必須零動作
    c = make_farming_core()
    start_attacking(c, 2.0)
    c.tick(W(600.0, hp=0.95, pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SILENT"
    seen = set()
    for t in range(700, 4000, 30):
        acts = c.tick(W(float(t), hp=0.20, pos=(50, 90), span=SPAN(9, 1, 1),
                        buff_hit=(2500, 60), exp_changed=(t % 300 == 0)))
        seen.update(verbs(acts))
    assert seen <= {"log"}, seen


def test_idle_silent_emits_nothing():
    c = make_farming_core()
    start_attacking(c, 2.0)
    c.tick(W(2.0 + 500, hp=0.95, pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SILENT"
    seen = set()
    for t in range(600, 3000, 50):
        for a in c.tick(W(float(t), hp=0.30, pos=(50, 90), span=SPAN())):
            seen.add(a.verb)
    assert seen <= {"log"}                                   # 連補血/跳都不准


def test_idle_silent_exits_only_via_cmd_farm():
    c = make_farming_core()
    start_attacking(c, 2.0)                                  # 先讓停滯計時起算
    c.tick(W(2.0 + 500, hp=0.95, pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SILENT"
    for t in range(600, 1500, 100):                          # 條件再好也不自動回
        c.tick(W(float(t), hp=0.95, pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SILENT"
    c.tick(W(1600.0, hp=0.95, pos=(50, 90), span=SPAN(), cmd="farm"))
    assert c.state in ("VERIFY", "DESCEND")   # 藥效已證實 → 同圈直接續往 DESCEND


# ============================================================
#  IDLE_SAFE：危險升級與自動恢復
# ============================================================

def _idle_core(reason_hp=0.95):
    c = make_farming_core()
    t = 2.0
    for dt in range(0, 70, 5):                               # pos 消失 → IDLE_SAFE
        c.tick(W(t + dt, hp=reason_hp, pos=None, span=None))
    assert c.state == "IDLE_SAFE"
    return c, t + 70


def test_idle_safe_escalates_to_silent_when_bleeding():
    c, t = _idle_core()
    c.tick(W(t, hp=0.90, pos=(50, 90), span=SPAN()))
    c.tick(W(t + 5, hp=0.70, pos=(50, 90), span=SPAN()))     # 掉 >0.12
    assert c.state == "IDLE_SILENT"


def test_idle_safe_escalates_on_critical_hp():
    c, t = _idle_core()
    c.tick(W(t, hp=0.40, pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SILENT"


def test_idle_safe_auto_recovers_when_clean():
    c, t = _idle_core()
    t2 = t + NightWatchCore.IDLE_RECOVER_AFTER
    for dt in range(0, 40, 5):                               # 乾淨 30s+
        c.tick(W(t2 + dt, hp=0.95, pos=(50, 90), span=SPAN()))
    assert c.state in ("VERIFY", "DESCEND")                  # 自動恢復
    assert c.stats.recoveries == 1


def test_idle_safe_recovery_is_bounded():
    c = make_farming_core()
    for round_i in range(NightWatchCore.IDLE_RECOVER_MAX + 2):
        base = 1000.0 * (round_i + 1)
        # 強制進 IDLE_SAFE
        if c.state == "FARM":
            for dt in range(0, 70, 5):
                c.tick(W(base + dt, hp=0.95, pos=None, span=None))
        if c.state != "IDLE_SAFE":
            break
        t2 = base + 70 + NightWatchCore.IDLE_RECOVER_AFTER
        for dt in range(0, 40, 5):
            c.tick(W(t2 + dt, hp=0.95, pos=(50, 90), span=SPAN()))
        if c.state in ("VERIFY", "DESCEND"):
            c.tick(W(t2 + 45, hp=0.99, pos=(50, 90), span=SPAN(20)))
            c.tick(W(t2 + 46, hp=0.99, pos=(50, 90), span=SPAN(20)))
    assert c.stats.recoveries <= NightWatchCore.IDLE_RECOVER_MAX


# ============================================================
#  重入歸零（審查：停滯時戳/下降預算跨回合污染）
# ============================================================

def test_farm_reentry_after_long_idle_does_not_false_stall():
    c = make_farming_core()
    start_attacking(c, 2.0)
    c.tick(W(3.0, hp=0.95, pos=(50, 90), span=SPAN(), cmd="idle"))
    assert c.state == "IDLE_SAFE"
    t = 3.0 + 1200                                           # 閒 20 分鐘
    c.tick(W(t, hp=0.99, pos=(50, 90), span=SPAN(20), cmd="farm"))
    c.tick(W(t + 1, hp=0.99, pos=(50, 90), span=SPAN(20)))
    assert c.state == "FARM"
    acts = c.tick(W(t + 2, hp=0.99, pos=(50, 90), span=SPAN(20)))
    assert c.state == "FARM"                                 # 不會秒退（時戳已重置）
    assert "hold_attack" in verbs(acts)


def test_descend_budget_resets_per_episode():
    c = NightWatchCore()
    c.tick(W(0, hp=0.99, pos=(50, 76), span=SPAN(3, 1, 2)))
    t = 1.0
    for _ in range(5):                                       # 第一回合用 5 步
        c.tick(W(t, hp=0.99, pos=(50, 78), span=SPAN(3, 1, 2)))
        t += 1
    c.tick(W(t, hp=0.99, pos=(50, 94), span=SPAN(20)))       # 到寬平台
    assert c.state == "FARM"
    c.tick(W(t + 1, hp=0.99, pos=(50, 94), span=SPAN(20), cmd="idle"))
    c.tick(W(t + 2, hp=0.99, pos=(50, 94), span=SPAN(3, 1, 2), cmd="farm"))
    assert c.state == "DESCEND"
    moved = 0
    for i in range(NightWatchCore.MAX_DESCENTS + 3):         # 第二回合有完整預算
        acts = c.tick(W(t + 3 + i, hp=0.99, pos=(50, 94), span=SPAN(3, 1, 2)))
        moved += sum(1 for a in acts if a.verb == "step")
    assert moved == NightWatchCore.MAX_DESCENTS


# ============================================================
#  讓手（邊緣觸發）與環境安全（非空洞斷言）
# ============================================================

def test_user_touch_edge_triggered_once():
    c = make_farming_core()
    start_attacking(c, 2.0)
    acts1 = c.tick(W(3.0, hp=0.95, pos=(50, 90), span=SPAN(), user_touch=True))
    assert "release_all" in verbs(acts1)
    n_yields = c.stats.yields
    for t in (3.1, 3.2, 3.3, 3.4):                           # 持續按住不重複觸發
        acts = c.tick(W(t, hp=0.95, pos=(50, 90), span=SPAN(), user_touch=True))
        assert "release_all" not in verbs(acts)
    assert c.stats.yields == n_yields


def test_env_gates_release_held_attack_each_case():
    for kw in ({"fg": False}, {"frame_ok": False}, {"window": False}):
        c = make_farming_core()
        start_attacking(c, 2.0)                              # 每個案例都先按住
        acts = c.tick(W(3.0, hp=0.95, pos=(50, 90), span=SPAN(), **kw))
        assert "release_attack" in verbs(acts), kw           # 必須真的放開
        assert all(a.verb in ("release_attack", "log") for a in acts)


def test_abort_transitions_release_attack_on_the_tick():
    # 每條撤退路徑的「轉換那一圈」都要放開攻擊鍵
    # 1) EXP 停滯
    c = make_farming_core()
    start_attacking(c, 2.0)
    acts = c.tick(W(600.0, hp=0.95, pos=(50, 90), span=SPAN()))
    assert c.state == "IDLE_SILENT" and "release_attack" in verbs(acts)
    # 2) 掉到窄平台
    c = make_farming_core()
    start_attacking(c, 2.0)
    acts = c.tick(W(3.0, hp=0.95, pos=(50, 94), span=SPAN(4, 2, 2)))
    assert c.state == "IDLE_SAFE" and "release_attack" in verbs(acts)
    # 3) 低血持續 → 撤退；因 HP 危險同圈升級為 IDLE_SILENT（保護更強）
    c = make_farming_core()
    start_attacking(c, 2.0)
    c.tick(W(3.0, hp=0.40, pos=(50, 90), span=SPAN()))
    for dt in range(1, 15):
        c.tick(W(3.0 + dt, hp=0.40, pos=(50, 90), span=SPAN()))
        if c.state != "FARM":
            break
    assert c.state == "IDLE_SILENT"
    assert not c._atk_held


# ============================================================
#  FARM 行為與邊界值
# ============================================================

def test_farm_guard_and_narrow_margin_branch():
    c = make_farming_core()
    start_attacking(c, 2.0)
    # 寬 9（<10 → margin 2）：dist 2.0 不推、1.9 推
    acts = c.tick(W(6.0, hp=0.95, pos=(50, 90), span=SPAN(9, 2.0, 7.0)))
    assert not any(a.verb == "step" for a in acts)
    acts = c.tick(W(9.0, hp=0.95, pos=(50, 90), span=SPAN(9, 1.9, 7.1)))
    steps = [a for a in acts if a.verb == "step"]
    assert steps and steps[0].arg[0] == "right"


def test_boundary_hp_values():
    c = make_farming_core()
    start_attacking(c, 2.0)
    acts = c.tick(W(3.0, hp=0.65, pos=(50, 90), span=SPAN()))
    assert "tap" not in verbs(acts)                          # 0.65 不補（嚴格 <）
    c2 = make_farming_core()
    start_attacking(c2, 2.0)
    for dt in range(0, 20):
        c2.tick(W(3.0 + dt, hp=0.45, pos=(50, 90), span=SPAN()))
    assert c2.state == "FARM"                                # 0.45 不觸發保險（嚴格 <）


def test_boundary_verify_defer_at_093():
    c = NightWatchCore()
    c.tick(W(0, hp=0.93, pos=(50, 90), span=SPAN(3)))
    assert c.state == "DESCEND"                              # 0.93 延後驗證


def test_boundary_heal_exactly_min_not_valid():
    c = NightWatchCore()
    c.tick(W(0, hp=0.90, pos=(50, 90), span=SPAN()))
    c.tick(W(1.0, hp=0.92, pos=(50, 90), span=SPAN()))       # 恰 +0.02：不算（嚴格 >）
    assert c.potion_works is None
    c.tick(W(5.0, hp=0.92, pos=(50, 90), span=SPAN()))
    assert c._potion_fails == 1


def test_boundary_tier_drop_dy_exactly_3_rehomes():
    c = make_farming_core()
    start_attacking(c, 2.0)
    acts = c.tick(W(3.0, hp=0.95, pos=(50, 93), span=SPAN(18)))
    assert c.state == "FARM"
    assert any(a.verb == "log" and "掉層" in str(a.arg) for a in acts)


def test_boundary_width_exactly_8_keeps_farming():
    c = make_farming_core()
    start_attacking(c, 2.0)
    c.tick(W(3.0, hp=0.95, pos=(50, 94), span=SPAN(8.0, 4, 4)))
    assert c.state == "FARM"                                 # 8.0 不算窄（嚴格 <）


def test_boundary_width_exactly_15_reaches_farm():
    c = NightWatchCore()
    c.tick(W(0, hp=0.99, pos=(50, 76), span=SPAN(15.0)))
    c.tick(W(1, hp=0.99, pos=(50, 76), span=SPAN(15.0)))
    assert c.state == "FARM"


def test_farm_repositions_and_dispels():
    c = make_farming_core()
    start_attacking(c, 2.0)
    t = 2.0 + 70
    acts = c.tick(W(t, hp=0.95, pos=(50, 90), span=SPAN(), exp_changed=True))
    assert sum(1 for a in acts if a.verb == "step") == 2     # 小碎步一去一回
    acts = c.tick(W(t + 1, hp=0.95, pos=(50, 90), span=SPAN(),
                    exp_changed=True, buff_hit=(2500, 60)))
    assert any(a.verb == "right_click" for a in acts)


# ============================================================
#  收尾與整晚模擬
# ============================================================

def test_cmd_stop_and_time_limit():
    c = make_farming_core()
    start_attacking(c, 2.0)
    acts = c.tick(W(3.0, cmd="stop"))
    assert c.state == "STOPPED" and "release_all" in verbs(acts)
    c2 = NightWatchCore(max_seconds=100)
    c2.tick(W(0, hp=0.99, pos=(50, 90), span=SPAN(3)))
    c2.tick(W(101, hp=0.99))
    assert c2.state == "STOPPED"


def test_full_night_scenario_invariants():
    """驗藥→下平台→農 3 小時（EXP 流動、擊退、感知閃失、HP 波動、補血）→
    使用者介入→恢復→時限收工。不變量：pos/span/fg/畫面異常的那一圈，
    絕不 hold_attack/step/tap；IDLE_SILENT 期間除 log 外零動作。"""
    c = NightWatchCore(max_seconds=4 * 3600)
    violations = []

    def checked(w):
        acts = c.tick(w)
        bad = w.pos is None or w.span is None or not w.fg or not w.frame_ok
        if bad and any(a.verb in ("hold_attack", "step", "tap", "repress_attack")
                       for a in acts):
            violations.append((w.now, verbs(acts)))
        return acts

    checked(W(0.0, hp=0.90, pos=(50, 76), span=SPAN(4, 2, 2)))
    checked(W(1.0, hp=0.96, pos=(50, 76), span=SPAN(4, 2, 2)))
    t = 2.0
    for _ in range(5):
        checked(W(t, hp=0.96, pos=(50, 80), span=SPAN(6, 2, 3)))
        t += 1.5
    checked(W(t, hp=0.96, pos=(50, 94), span=SPAN(20)))
    assert c.state == "FARM"
    for i in range(360):                                     # 3 小時、30s 一圈
        t += 30
        if i % 50 == 17:
            checked(W(t, hp=0.9, pos=None, span=None))       # 感知閃失一圈
        elif i % 70 == 33:
            checked(W(t, hp=0.9, pos=(50, 94), span=SPAN(), fg=False))
        else:
            hp = 0.60 if i % 90 == 45 else 0.88
            checked(W(t, hp=hp, pos=(48 + (i % 5), 94),
                      span=SPAN(20, 3 + (i % 5), 17 - (i % 5)),
                      exp_changed=(i % 2 == 0)))
        assert c.state == "FARM", (i, c.state)
    checked(W(t + 1, hp=0.9, pos=(50, 94), span=SPAN(), user_touch=True))
    c.tick(W(4 * 3600 + 10, hp=0.9, pos=(50, 94), span=SPAN()))
    assert c.state == "STOPPED"
    assert not violations, violations
