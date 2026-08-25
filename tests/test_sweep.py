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


# ---- 看到怪才出手（使用者實跑回報：「會朝空氣攻擊」）----

def targeting(tg, **kw):
    """做一台目標偵測被寫死成 tg=(左,右,左最近,右最近) 的掃蕩器。"""
    hw = sweeper(**kw)
    hw.target_check = True
    hw._tgt_ready = True
    hw._atk_range = 700.0
    hw._init_targets = lambda: True
    hw._scan_targets = lambda: tg
    hw._tgt_seen = 0.0
    hw.attacks = []
    hw._attack_once = lambda: hw.attacks.append("atk")
    hw.steps = []
    hw._tap = lambda k, hold=None: hw.steps.append(k)
    return hw


def test_does_not_attack_when_nothing_is_in_front():
    """前面空無一物就不可以出手——這正是使用者回報的空揮。"""
    hw = targeting((0, 0, None, None))
    hw._sweep_dir = 1
    hw._sweep_by_target((0, 0, None, None), 100.0)
    assert hw.attacks == [], "前面沒有怪卻還是攻擊了"
    assert hw._air_skips == 1


def test_attacks_when_a_monster_is_in_range_ahead():
    hw = targeting((0, 1, None, 300.0))
    hw._sweep_dir = 1                    # 往右走，怪在右邊 300px
    hw._next_attack = 0.0
    hw._sweep_by_target((0, 1, None, 300.0), 100.0)
    assert hw.attacks == ["atk"]
    assert hw.steps == [], "射程內有怪還走掉了"


def test_does_not_attack_a_monster_that_is_out_of_range():
    """怪在同一側但遠超射程 → 打過去也是空的，應該先走過去。"""
    hw = targeting((0, 1, None, 1200.0))
    hw._sweep_dir = 1
    hw._last_step = 0.0
    hw._sweep_by_target((0, 1, None, 1200.0), 100.0)
    assert hw.attacks == [], "對射程外的怪開火＝空揮"
    assert hw.steps, "應該走過去接近"


def test_turns_back_when_every_monster_is_behind():
    hw = targeting((3, 0, 200.0, None))
    hw._sweep_dir = 1                    # 往右走，怪全在左邊
    hw._since_turn = 5
    hw._sweep_by_target((3, 0, 200.0, None), 100.0)
    assert hw._sweep_dir == -1
    assert hw.attacks == []


def test_hold_mode_releases_the_attack_key_when_the_target_is_gone():
    """按住模式下不再呼叫 hold_tick 並不會讓鍵彈起來——必須主動放開，
    否則會一路壓著 Ctrl 打空氣。"""
    hw = targeting((0, 0, None, None), hold_attack=True)
    ups = []
    hw._up = lambda k: ups.append(k)
    hw._atk_held = True
    hw._sweep_by_target((0, 0, None, None), 100.0)
    assert hw.attack_key in ups, "沒有目標卻還壓著攻擊鍵"
    assert hw._atk_held is False


def test_falls_back_to_plain_attacking_when_detection_is_blind():
    """這張圖的怪沒有模板時，「看到才打」會變成完全不打。
    寧可打空氣，也不要整晚不出手。"""
    hw = sweeper()
    hw.target_check = True
    hw._tgt_ready = True
    hw._init_targets = lambda: True
    hw._scan_targets = lambda: (0, 0, None, None)
    hw._tgt_seen = 0.0
    assert hw._targets(10.0) is not None            # 還在寬限期內
    assert hw._targets(10.0 + hw.TARGET_BLIND_GRACE + 1) is None  # 判定偵測不可靠
    assert hw._tgt_blind is True


def test_target_check_defaults_on_but_is_disabled_in_dry_run():
    assert sweeper().target_check is False          # dry-run 不做視覺
    hw = HoldWiggle(dry_run=True, target_check=False)
    assert hw.target_check is False


def test_standing_still_too_long_triggers_an_anti_stale_shuffle():
    """定點輸出約 60 秒後攻擊會失效，而且失效看的是水平位移（原地跳沒用）。

    「看到怪就站定打」的代價正是容易踩到這個，所以要主動安排小碎步。
    """
    hw = targeting((0, 1, None, 300.0))
    hw._sweep_dir = 1
    hw._next_attack = 0.0
    hw._sweep_by_target((0, 1, None, 300.0), 100.0)         # 開始站定輸出
    assert hw._stand_since == 100.0
    hw.steps.clear()
    t = 100.0 + hw.STAND_SHUFFLE_AFTER + 1
    hw._sweep_by_target((0, 1, None, 300.0), t)
    assert hw.steps, "站了 40 秒還沒動 → 攻擊會失效"
    assert set(hw.steps) == {"left", "right"}, "碎步要有來有回，淨位移接近 0"


def test_moving_resets_the_anti_stale_timer():
    hw = targeting((0, 0, None, None))
    hw._sweep_dir = 1
    hw._stand_since = 50.0
    hw._sweep_by_target((0, 0, None, None), 100.0)          # 沒有目標 → 走
    assert hw._stand_since is None
