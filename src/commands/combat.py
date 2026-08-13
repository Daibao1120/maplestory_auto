"""攻擊決策（純函式，方便測試；不依賴 OpenCV）。

角色為弓箭手＝遠程定點輸出：偵測到鱷魚後，面向「怪較多的那一側」放技能連射，
不需貼身；最近的怪若還在射程外，就朝該側走近一步。

- select_nearest / plan_attack：以最近的怪為目標的單體版本（通用、好測）。
- dominant_side / plan_ranged：弓箭手用；面向怪較多的一側，射程內就放技能。
攻擊在橫向卷軸以「水平距離」為準，因此以 |怪物中心x − 角色x| 判斷遠近與方位。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional


@dataclass
class CombatDecision:
    """一次攻擊決策。"""
    target: Any        # Detection（具 .center 屬性）
    facing: str        # "left" | "right"
    in_range: bool     # 最近的怪是否已在攻擊距離內
    distance: int      # 到最近怪的帶正負號水平距離（怪 − 角色）
    count: int = 0     # 偵測到的怪物總數


def select_nearest(player_x, monsters):
    """挑水平距離最近的怪；同距離時取分數較高者。"""
    return min(monsters, key=lambda m: (abs(m.center[0] - player_x), -m.score))


def dominant_side(player_x, monsters):
    """回傳怪較多的一側 'left'/'right'；平手時以最近的怪所在側為準。"""
    left = sum(1 for m in monsters if m.center[0] < player_x)
    right = len(monsters) - left
    if right > left:
        return "right"
    if left > right:
        return "left"
    return "right" if select_nearest(player_x, monsters).center[0] >= player_x else "left"


def plan_attack(player_xy, monsters, attack_range):
    """單體版：鎖定最近的怪，決定面向與是否在射程內。無怪回傳 None。"""
    if not monsters:
        return None
    px, _py = player_xy
    target = select_nearest(px, monsters)
    dx = target.center[0] - px
    facing = "right" if dx >= 0 else "left"
    return CombatDecision(target=target, facing=facing,
                          in_range=abs(dx) <= attack_range, distance=int(dx),
                          count=len(monsters))


def plan_ranged(player_xy, monsters, attack_range):
    """弓箭手版：面向怪較多的一側；最近的怪在射程內就放技能，否則走近。無怪回傳 None。"""
    if not monsters:
        return None
    px, _py = player_xy
    nearest = select_nearest(px, monsters)
    dx = nearest.center[0] - px
    return CombatDecision(target=nearest, facing=dominant_side(px, monsters),
                          in_range=abs(dx) <= attack_range, distance=int(dx),
                          count=len(monsters))


def decision_to_actions(decision, combo=3):
    """把決策轉成 command book 動作序列（dict 清單）。

    - 已在射程內 → 面向並連射：attack(facing, repeat=combo)
    - 還太遠     → 朝目標走近一步：approach(dir=facing)
    """
    if decision is None:
        return []
    if decision.in_range:
        return [{"action": "attack", "args": {"facing": decision.facing, "repeat": int(combo)}}]
    return [{"action": "approach", "args": {"dir": decision.facing}}]
