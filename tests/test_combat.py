"""攻擊決策的純函式測試（不需 OpenCV，任何環境都能跑）。"""
import os
import sys
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.commands.combat import (  # noqa: E402
    select_nearest, dominant_side, plan_attack, plan_ranged, decision_to_actions,
    PlatformState, filter_attackable, on_platform, plan_two_platforms,
)


@dataclass
class FakeDet:
    """假的偵測結果，只需要 center / score。"""
    cx: int
    cy: int
    score: float = 1.0

    @property
    def center(self):
        return (self.cx, self.cy)


def test_select_nearest_by_horizontal_distance():
    ms = [FakeDet(200, 100), FakeDet(60, 100)]
    assert select_nearest(100, ms).center == (60, 100)


def test_plan_attack_in_range_and_facing_left():
    ms = [FakeDet(200, 100), FakeDet(60, 100)]      # 角色 x=100，最近 (60) 在左
    d = plan_attack((100, 100), ms, attack_range=50)
    assert d.target.center == (60, 100)
    assert d.facing == "left"
    assert d.in_range is True                        # |60-100|=40 <= 50
    assert decision_to_actions(d, combo=3) == [
        {"action": "attack", "args": {"facing": "left", "repeat": 3}}
    ]


def test_plan_attack_approach_when_far():
    ms = [FakeDet(500, 100)]                          # dx=+400，超出射程
    d = plan_attack((100, 100), ms, attack_range=50)
    assert d.facing == "right"
    assert d.in_range is False
    assert decision_to_actions(d, 3) == [
        {"action": "approach", "args": {"dir": "right"}}
    ]


def test_dominant_side_by_count():
    ms = [FakeDet(200, 1), FakeDet(300, 1), FakeDet(400, 1), FakeDet(50, 1)]
    assert dominant_side(100, ms) == "right"          # 右 3、左 1


def test_plan_ranged_faces_side_with_more_monsters():
    # 左側較多（3）、最近的怪其實在右（150），弓箭手應面向「怪較多」的左側
    ms = [FakeDet(40, 1), FakeDet(30, 1), FakeDet(20, 1), FakeDet(150, 1)]
    d = plan_ranged((100, 100), ms, attack_range=700)
    assert d.facing == "left"
    assert d.count == 4
    assert d.in_range is True


def test_plan_none_when_no_monsters():
    assert plan_attack((0, 0), [], 10) is None
    assert plan_ranged((0, 0), [], 10) is None
    assert decision_to_actions(None) == []


# ============================================================
#  兩平台輪流清怪（數值取自 擷取.PNG 實測：角色畫面 y≈404，
#  同平台鱷魚 y≈430(dy+26)、另一平台 y≈488(dy+84)）
# ============================================================

PLATFORMS = [
    {"name": "左平台", "x_range": [30, 48], "minimap_y": 38, "jump_x": None},
    {"name": "右平台", "x_range": [56, 75], "minimap_y": 40, "jump_x": None},
]
SWITCH = {"empty_loops": 3, "y_tolerance": 1, "x_tolerance": 2, "max_move_loops": 5}


def test_filter_attackable_keeps_same_height_band_only():
    ms = [FakeDet(472, 430), FakeDet(1295, 488), FakeDet(200, 360)]
    kept = filter_attackable(404, ms, [0, 60])           # dy = +26 / +84 / -44
    assert [m.center for m in kept] == [(472, 430)]
    assert len(filter_attackable(404, ms, None)) == 3    # 不設定 = 不過濾


def test_on_platform_checks_x_range_and_height():
    assert on_platform((40, 38), PLATFORMS[0]) is True
    assert on_platform((40, 43), PLATFORMS[0]) is False   # 高度不對（在下層土丘）
    assert on_platform((60, 38), PLATFORMS[0]) is False   # x 超出範圍
    assert on_platform(None, PLATFORMS[0]) is False


def test_two_platforms_patrol_and_reset_when_monsters_present():
    st = PlatformState(target=0, empty_loops=2)
    st2, plan = plan_two_platforms((40, 38), 1, st, PLATFORMS, SWITCH)
    assert plan["kind"] == "patrol" and plan["name"] == "左平台"
    assert (plan["left"], plan["right"]) == (30, 48)
    assert st2.empty_loops == 0                          # 有怪 → 計數歸零


def test_two_platforms_switch_after_empty_loops():
    st = PlatformState(target=0, empty_loops=0)
    for _ in range(2):                                   # 累積到 2，仍在左平台巡邏
        st, plan = plan_two_platforms((40, 38), 0, st, PLATFORMS, SWITCH)
        assert plan["kind"] == "patrol"
    st, plan = plan_two_platforms((40, 38), 0, st, PLATFORMS, SWITCH)
    assert st.target == 1                                # 第 3 圈沒怪 → 換右平台
    assert plan["kind"] == "approach" and plan["dir"] == "right"


def test_two_platforms_move_walks_then_jumps():
    st = PlatformState(target=1)
    # 還在左平台下方 x=50：右平台範圍是 56-75 → 先往右走
    st, plan = plan_two_platforms((50, 43), 0, st, PLATFORMS, SWITCH)
    assert plan["kind"] == "approach" and plan["dir"] == "right"
    # 走到右平台正下方 x=60（高度 43 不對）→ 邊走邊跳上去
    st, plan = plan_two_platforms((60, 43), 0, st, PLATFORMS, SWITCH)
    assert plan["kind"] == "jump_move" and plan["dir"] == "right"
    # 跳上去了（y=40）→ 回到巡邏
    st, plan = plan_two_platforms((60, 40), 0, st, PLATFORMS, SWITCH)
    assert plan["kind"] == "patrol" and plan["name"] == "右平台"
    assert st.move_loops == 0


def test_two_platforms_jump_x_walks_to_spot_first():
    plats = [PLATFORMS[0], {**PLATFORMS[1], "jump_x": 70}]
    st = PlatformState(target=1)
    # 在 x=60（已在 x_range 內）但指定起跳點 70 → 先走過去而不是原地跳
    st, plan = plan_two_platforms((60, 43), 0, st, plats, SWITCH)
    assert plan["kind"] == "approach" and plan["dir"] == "right"
    st, plan = plan_two_platforms((69, 43), 0, st, plats, SWITCH)
    assert plan["kind"] == "jump_move"


def test_two_platforms_gives_up_after_max_move_loops():
    st = PlatformState(target=1)
    for _ in range(4):
        st, plan = plan_two_platforms((60, 43), 0, st, PLATFORMS, SWITCH)
        assert plan["kind"] == "jump_move"
    st, plan = plan_two_platforms((60, 43), 0, st, PLATFORMS, SWITCH)
    assert st.target == 0                                # 卡滿 5 圈 → 放棄，回守左平台
    assert plan["kind"] == "patrol" and plan["name"] == "左平台"


def test_two_platforms_holds_when_player_not_located():
    st = PlatformState(target=0, empty_loops=2, move_loops=0)
    st2, plan = plan_two_platforms(None, 0, st, PLATFORMS, SWITCH)
    assert plan["kind"] == "hold"
    assert st2 == st                                     # 讀不到位置 → 狀態不變
