"""攻擊決策的純函式測試（不需 OpenCV，任何環境都能跑）。"""
import os
import sys
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.commands.combat import (  # noqa: E402
    select_nearest, dominant_side, plan_attack, plan_ranged, decision_to_actions,
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
