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
