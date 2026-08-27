"""掃蕩模式測試（併自另一工作階段的 --sweep，加上邊界折返保險）。

行為：左右來回走、邊走邊打，攻擊自然朝走的方向。原版是盲走計步；這裡加上
edge-guard 邊界檢查——盲走在被擊退或斜坡上會累積誤差，實測是掉平台主因。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

if sys.platform != "win32":
    pytest.skip("Windows only", allow_module_level=True)

from tools.hold_and_wiggle import HoldWiggle  # noqa: E402


def sweeper(**kw):
    kw.setdefault("dry_run", True)
    kw.setdefault("sweep", True)
    kw.setdefault("sweep_steps", 3)
    return HoldWiggle(**kw)


def test_sweep_turns_around_after_configured_steps():
    hw = sweeper(sweep_steps=2)
    seen = []
    for _ in range(8):
        hw._sweep_step()
        seen.append(hw.attack_facing)
    # 走 2 步右 → 折返走左 → 再折返：方向要來回而不是一路往同一邊
    assert "right" in seen and "left" in seen
    assert abs(hw._sweep_pos) <= hw.sweep_steps


def test_sweep_facing_follows_walking_direction():
    hw = sweeper()
    hw._sweep_dir = 1
    hw._sweep_step()
    assert hw.attack_facing == "right"      # 面向＝走的方向，才打得到前方的怪
    hw._sweep_dir = -1
    hw._sweep_step()
    assert hw.attack_facing == "left"


def test_sweep_position_stays_bounded_over_long_run():
    hw = sweeper(sweep_steps=3)
    for _ in range(200):                    # 長時間來回不可漂走
        hw._sweep_step()
        assert -hw.sweep_steps <= hw._sweep_pos <= hw.sweep_steps


def test_sweep_defaults_off():
    hw = HoldWiggle(dry_run=True)
    assert hw.sweep is False
    assert hw.start_paused is False


def test_start_paused_flag_is_stored():
    hw = sweeper(start_paused=True)
    assert hw.start_paused is True


def test_sweep_shrinks_range_when_minimap_unreadable():
    """要 edge-guard 但讀不到座標時，盲走計步不可信 → 範圍收斂，不可照原範圍走。"""
    hw = sweeper(sweep_steps=8, edge_guard=True)
    hw.dry_run = False                      # 走盲走分支
    hw._edge_guard_wanted = True
    hw._edge_center = None
    hw._edge_guard_broken = True            # 讓重試立即放棄，不去抓畫面
    hw.edge_guard = False
    calls = []
    hw._tap = lambda k, hold=None: calls.append(k)
    for _ in range(40):
        hw._sweep_step()
        assert -4 <= hw._sweep_pos <= 4     # 收斂為 8 // 2
    assert calls                            # 仍在走，只是範圍變小


def test_sweep_turns_back_at_safe_bound():
    """真實座標已到安全界 → 這一步必須反向，不能再往外踏。"""
    hw = sweeper(sweep_steps=8, edge_guard=True)
    hw.dry_run = False
    hw._edge_guard_wanted = True
    hw.edge_guard = True
    hw._edge_center, hw._edge_lo, hw._edge_hi = 100, 60, 140
    hw._player_x = lambda **kw: 141         # 已越過右界
    hw._sweep_dir = 1                       # 本來要往右
    moved = []
    hw._tap = lambda k, hold=None: moved.append(k)
    hw._sweep_step()
    assert moved == ["left"]                # 折返，而不是繼續往右走出平台
    assert hw.attack_facing == "left"


# ---- 適應性掃蕩：眼前有怪就留下打，沒怪就快走，怪在身後就提前折返 ----

def adaptive(sides, **kw):
    """做一台開了適應性掃蕩、動靜量被寫死成 `sides` 的掃蕩器。"""
    hw = sweeper(**kw)
    hw.adaptive_sweep = True
    hw._init_smart = lambda: True
    hw._motion_sides = lambda: sides
    hw._dwell_next = 0.0
    return hw


def test_dwell_slows_down_when_monsters_are_ahead():
    hw = adaptive((100, 3000))        # 動靜集中在右邊
    hw._sweep_dir = 1                 # 正往右走 → 怪在眼前
    hw._sweep_sense()
    assert hw._dwell == hw.DWELL_SLOW  # 留下來打，不要走掉
    assert hw._sweep_dir == 1          # 方向不變


def test_dwell_speeds_up_when_nothing_is_moving():
    hw = adaptive((10, 10))           # 兩邊都靜悄悄＝怪清光了
    hw._sweep_sense()
    assert hw._dwell == hw.DWELL_FAST  # 快點走去找下一隻


def test_turns_back_early_when_monsters_are_behind():
    hw = adaptive((3000, 100))        # 動靜在左
    hw._sweep_dir = 1                 # 卻正往右走 → 整趟是空揮
    hw._since_turn = 5
    hw._sweep_sense()
    assert hw._sweep_dir == -1         # 提前折返回去打
    assert hw._since_turn == 0


def test_does_not_flip_before_minimum_steps():
    """剛折返就又想折返 → 會原地抖。要求走滿幾步才准再翻。"""
    hw = adaptive((3000, 100))
    hw._sweep_dir = 1
    hw._since_turn = 0                # 才剛轉過來
    hw._sweep_sense()
    assert hw._sweep_dir == 1          # 不准馬上再翻


def test_motion_check_is_throttled():
    """量動靜要抓兩張畫面（~0.15s）。每步都量會吃掉三分之一的時間。"""
    calls = []
    hw = adaptive((10, 10))
    hw._motion_sides = lambda: (calls.append(1), (10, 10))[1]
    hw._sweep_sense()
    hw._sweep_sense()                  # 立刻再問一次 → 應該吃快取
    assert len(calls) == 1


def test_adaptive_off_by_default():
    hw = sweeper()
    assert hw.adaptive_sweep is False
    assert hw._dwell == 1.0            # 不開就是原本的固定節奏


# ---- 戰績計：用數字比較設定，而不是靠感覺 ----

def test_meter_off_by_default_and_in_dry_run():
    assert sweeper().meter is False
    assert sweeper(meter=True).meter is False      # dry-run 不量測


def test_meter_records_a_hit_when_exp_region_changes():
    import numpy as np
    hw = sweeper(meter=True)
    hw.meter = True
    hw._np = np
    hw._init_smart = lambda: True
    hw._meter_roi = (0, 0, 4, 4)
    frames = [np.zeros((8, 8, 3), np.uint8), np.full((8, 8, 3), 200, np.uint8)]
    hw._cap = type("C", (), {"grab": lambda self: frames.pop(0)})()
    hw._meter_t0, hw._meter_report = 0.0, 1e9
    hw._meter_tick(10.0)                            # 建立基準
    hw._meter_tick(20.0)                            # 數字變了
    assert len(hw._meter_events) == 1


def test_meter_tick_is_throttled():
    hw = sweeper(meter=True)
    hw.meter = True
    hw._meter_off = False
    calls = []
    hw._init_smart = lambda: calls.append(1) or True
    hw._meter_roi = None
    hw._meter_locate = lambda: False
    hw._meter_tick(10.0)
    hw._meter_tick(10.1)                            # 還在節流窗內
    assert len(calls) == 1


def test_meter_failure_never_stops_farming():
    """量測是附加價值；它壞掉不可以害你停止打怪。"""
    hw = sweeper(meter=True)
    hw.meter = True
    hw._init_smart = lambda: (_ for _ in ()).throw(RuntimeError("no mss"))
    try:
        hw._meter_tick(10.0)
    except Exception:
        raise AssertionError("戰績計的例外不該傳出去打斷主迴圈")


def test_early_turnaround_does_not_move_the_sweep_origin():
    """提前折返只能翻方向，不能把「離出發點幾步」的計數歸零。

    歸零等於把原點移到現在的位置，± 範圍就跟著漂走。小地圖讀不到而改用
    盲走時，收斂範圍那道保險會因此完全失效——一路漂出平台。
    """
    hw = adaptive((3000, 100), sweep_steps=4)
    hw._sweep_dir = 1
    hw._sweep_pos = 3                  # 已離出發點 3 步
    hw._since_turn = 5
    hw._sweep_sense()
    assert hw._sweep_dir == -1         # 有折返
    assert hw._sweep_pos == 3, "折返時把步數計數歸零了 → 邊界原點會漂走"


def test_blind_sweep_stays_bounded_even_with_early_turnarounds():
    """盲走 + 反覆提前折返的組合下，位置仍必須被關在收斂範圍內。"""
    hw = adaptive((3000, 100), sweep_steps=8, edge_guard=True)
    hw.dry_run = False
    hw._edge_guard_wanted = True
    hw._edge_center = None
    hw._edge_guard_broken = True
    hw.edge_guard = False
    hw._tap = lambda k, hold=None: None
    for i in range(120):
        hw._dwell_next = 0.0           # 每步都重新判斷（最容易漂的情況）
        hw._sweep_sense()
        hw._sweep_step()
        assert -4 <= hw._sweep_pos <= 4, f"第 {i} 步漂出收斂範圍：{hw._sweep_pos}"


# ---- 打得到才持續輸出（使用者實跑回報：「會朝空氣攻擊」）----
#
# 實機量測否決了「先偵測怪、再決定打不打」：模板比對 8 隻只找到 2 隻、608ms；
# 名牌定位 score 0.39 失效，角色實際位置比畫面中央低 236px，同層過濾整個算錯。
# 改用傷害數字——打中了就跳數字，是打到東西的直接證據，繞開角色定位這個死結。

def dmg_sweeper(**kw):
    hw = sweeper(**kw)
    hw.target_check = True
    hw._dmg_ready = True
    hw._dmg_broken = False
    hw.attacks = []
    hw._attack_once = lambda: hw.attacks.append("atk")
    hw.steps = []
    hw._tap = lambda k, hold=None: hw.steps.append(k)
    hw._next_attack = 0.0
    return hw


def test_keeps_attacking_while_damage_is_landing():
    hw = dmg_sweeper()
    hw._gate_open = True                  # 判斷閘說打得到
    hw._sweep_by_damage(100.5)
    assert hw.attacks == ["atk"]
    assert hw.steps == [], "打得到卻走掉了"


def test_stops_attacking_once_damage_stops():
    """傷害停了就是打不到——這正是空揮的來源。"""
    hw = dmg_sweeper()
    hw._gate_open = False                     # 判斷閘說打不到
    hw._last_step = 1e9                       # 還不到走下一步的時間
    hw._last_probe = 1e9                      # 也還不到試打的時間
    hw._sweep_by_damage(100.0 + hw.HIT_MEMORY + 0.1)
    assert hw.attacks == [], "沒打到東西還在開火"
    assert hw._air_skips == 1


def test_probes_are_time_throttled_not_continuous():
    """完全不打會有另一個問題：永遠不知道新位置打不打得到。
    但試打必須有節流——每一刻都試等於一路開火，跟原本的空揮沒兩樣。"""
    hw = dmg_sweeper()
    hw._gate_open = False
    hw._last_step = 1e9
    hw._last_probe = 0.0
    t = 100.0
    for i in range(30):                       # 3 秒內（節流是 2 秒）
        hw._next_attack = 0.0
        hw._sweep_by_damage(t)
        t += 0.1
    assert len(hw.attacks) <= hw.PROBE_SHOTS * 2, (
        f"3 秒內試打了 {len(hw.attacks)} 下 → 節流沒生效")
    assert hw.attacks, "完全不試打 → 永遠不知道打不打得到"


def test_hold_mode_releases_the_key_when_damage_stops():
    """按住模式下不再呼叫 hold_tick 不會讓鍵彈起來，會一路壓著打空氣。"""
    hw = dmg_sweeper(hold_attack=True)
    ups = []
    hw._up = lambda k: ups.append(k)
    hw._atk_held = True
    hw._gate_open = False
    hw._last_step = 1e9
    hw._last_probe = 1e9
    hw._sweep_by_damage(100.0)
    assert hw.attack_key in ups
    assert hw._atk_held is False


def test_standing_still_too_long_triggers_an_anti_stale_shuffle():
    """定點輸出約 60 秒後攻擊會失效，失效判定看的是水平位移（原地跳無效）。"""
    hw = dmg_sweeper()
    hw._gate_open = True
    hw._sweep_by_damage(100.1)
    assert hw._stand_since == 100.1
    hw.steps.clear()
    hw._sweep_by_damage(100.1 + hw.STAND_SHUFFLE_AFTER + 0.1)
    assert set(hw.steps) == {"left", "right"}, "碎步要有來有回，淨位移接近 0"


def test_detection_failure_falls_back_to_plain_attacking():
    """傷害偵測壞掉時必須退回照打，不可以變成永遠不出手。"""
    hw = dmg_sweeper()
    hw._init_damage = lambda: (_ for _ in ()).throw(RuntimeError("no cv2"))
    hw._dmg_ready = False
    hw._dmg_next = 0.0
    hw._damage_tick(100.0)
    assert hw._dmg_broken is True


def test_losing_the_player_for_too_long_falls_back_to_plain_attacking():
    """定位不到就無法歸屬傷害。換過帽子時「不確定＝不打」會變成幾乎不出手，
    所以連續定位失敗夠多次要退回照打，並講清楚怎麼修。"""
    hw = dmg_sweeper()
    hw._init_damage = lambda: True
    hw._cv2 = __import__("cv2")
    hw._canon = (8, 8)
    import numpy as np
    blank = np.zeros((8, 8, 3), np.uint8)
    hw._cap = type("C", (), {"grab": lambda self: blank})()
    hw._loc = type("L", (), {"find": lambda self, f: None})()
    hw._dmgw = type("W", (), {"update": lambda *a: []})()
    for i in range(hw.LOC_MISS_LIMIT):
        hw._dmg_next = 0.0
        hw._damage_tick(100.0 + i)
    assert hw._dmg_broken is True


def test_turns_back_toward_the_side_it_last_hit():
    hw = dmg_sweeper()
    hw._gate_open = False
    hw._last_step = 1e9
    hw._last_probe = 1e9
    hw._hit_side = "left"
    hw._sweep_dir = 1                      # 正往右走，但剛才打到的在左邊
    hw._since_turn = 5
    hw._sweep_by_damage(100.0)
    assert hw._sweep_dir == -1


# ---- 統一判斷閘：所有攻擊路徑都必須問過 ----
#
# 使用者實跑的 run_hold_wiggle_admin.bat 用 --hold-attack --no-move，走的是
# 定點攻擊分支——那條分支以前完全沒有問過有沒有目標，會一路壓著 Ctrl 打空氣。
# 這是「還是會空射」最直接的原因，而測試完全沒涵蓋到，因為測試只驗掃蕩。

def gated(**kw):
    hw = sweeper(**kw)
    hw.target_check = True
    hw.dry_run = False
    hw._dmg_ready, hw._dmg_broken = True, False
    hw._damage_tick = lambda now: None
    return hw


def test_every_attack_path_consults_the_gate():
    """靜態檢查：主迴圈裡不可以有繞過 _target_gate 的攻擊呼叫。"""
    import inspect
    from tools.hold_and_wiggle import HoldWiggle
    src = inspect.getsource(HoldWiggle.run)
    body = src[src.index('state == "RUNNING"'):src.index('elif state == "PAUSED"')]
    assert body.count("_target_gate") >= 2, "有攻擊路徑沒問過目標判斷"


def test_gate_opens_during_warmup():
    """一開跑就關著閘，會在證據建立前先誤判成打不到。"""
    hw = gated()
    hw._last_dmg = None
    allow, why = hw._target_gate(1000.0)
    assert allow and why == "暖機中"


def test_gate_closes_when_nothing_is_being_hit():
    hw = gated()
    hw._gate_t0 = 0.0
    hw._last_dmg = 100.0
    allow, _ = hw._target_gate(100.0 + hw.HIT_MEMORY + 0.1)
    assert not allow


def test_gate_reopens_after_long_blindness():
    """換裝備／換地圖會讓歸屬失準。關著不放等於整晚不出手，比空揮更糟。"""
    hw = gated()
    hw._gate_t0 = 0.0
    hw._last_dmg = None
    assert hw._target_gate(100.0)[0] is False          # 先關起來
    allow, why = hw._target_gate(100.0 + hw.BLIND_LIMIT + 1)
    assert allow and why == "長時間無證據"


def test_gate_fails_open_when_detection_is_broken():
    hw = gated()
    hw._dmg_broken = True
    assert hw._target_gate(1000.0)[0] is True


def test_gate_is_transparent_in_dry_run():
    hw = sweeper()
    assert hw._target_gate(1000.0) == (True, "未啟用")


def test_repeated_grab_failures_disable_the_gate():
    """抓不到畫面就永遠關著閘＝整晚不出手。"""
    hw = gated()
    hw._damage_tick = None
    from tools.hold_and_wiggle import HoldWiggle
    hw._damage_tick = HoldWiggle._damage_tick.__get__(hw)
    hw._init_damage = lambda: True
    hw._cv2 = __import__("cv2")
    hw._canon = (8, 8)
    hw._cap = type("C", (), {"grab": lambda self: None})()
    for i in range(hw.GRAB_FAIL_LIMIT):
        hw._dmg_next = 0.0
        hw._damage_tick(100.0 + i)
    assert hw._dmg_broken is True


def test_exp_progress_also_opens_the_gate():
    """傷害數字只在畫面停留約 0.4 秒，取樣一定會漏。EXP 進帳是擊殺的直接證據，
    實測把它一起當證據後，漏掉的真實擊殺從 58% 降到 0%。"""
    import numpy as np
    hw = gated()
    hw._gate_t0 = 0.0
    hw._last_dmg = None
    assert hw._target_gate(100.0)[0] is False       # 沒有任何證據 → 關
    hw._exp_roi = (0, 0, 4, 4)
    hw._exp_recalib = 99
    hw._exp_prev = np.zeros((4, 4, 3), np.int16)
    hw._exp_tick(np.full((8, 8, 3), 200, np.uint8))  # EXP 區變了
    assert hw._exp_hits == 1
    assert hw._last_dmg is not None
