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


# ============================================================
#  兩平台輪流清怪（純函式，方便測試；小地圖座標系）
#
#  想法：箭是水平飛的，所以「打得到」= 怪與角色在同一高度帶
#  （filter_attackable，以畫面 y 差過濾）。目前平台連續 N 圈沒有
#  可打的怪，就走/跳到另一個平台（plan_two_platforms）。
# ============================================================

def filter_attackable(player_screen_y, monsters, y_band):
    """只留「與角色同高度帶」的怪：y_band=[lo, hi]，怪中心y − 角色y 需落在其中。

    箭是水平飛的——別的平台的怪看得到但射不到，濾掉才不會對空放箭。
    y_band 為 None 時不過濾。
    """
    if not y_band:
        return list(monsters)
    lo, hi = y_band
    return [m for m in monsters if lo <= m.center[1] - player_screen_y <= hi]


@dataclass(frozen=True)
class PlatformState:
    """兩平台巡邏的跨圈狀態（不可變；每圈由 plan_two_platforms 產生新狀態）。"""
    target: int = 0        # 想待的平台 index
    empty_loops: int = 0   # 在目標平台上連續沒怪的圈數
    move_loops: int = 0    # 換平台已花的圈數（防卡死）


def on_platform(player_mm, plat, y_tol=1, x_tol=2):
    """玩家（小地圖座標）是否站在該平台上：x 在範圍內且 y 在平台高度附近。"""
    if player_mm is None:
        return False
    px, py = player_mm
    left, right = plat["x_range"]
    return (left - x_tol) <= px <= (right + x_tol) and abs(py - plat["minimap_y"]) <= y_tol


def plan_two_platforms(player_mm, n_attackable, state, platforms, switch_cfg=None):
    """兩平台決策（純函式）。回傳 (新狀態, plan)。

    plan（dict）四種：
      {"kind": "patrol", "left": …, "right": …, "name": …}   # 在目標平台上巡邏找怪
      {"kind": "approach", "dir": …, "name": …}              # 前往另一平台：走
      {"kind": "jump_move", "dir": …, "name": …}             # 前往另一平台：到平台下方，邊走邊跳上去
      {"kind": "hold", "name": …}                            # 小地圖讀不到玩家 → 原地不動
    """
    sw = switch_cfg or {}
    y_tol = int(sw.get("y_tolerance", 1))
    x_tol = int(sw.get("x_tolerance", 2))
    empty_limit = int(sw.get("empty_loops", 8))
    move_limit = int(sw.get("max_move_loops", 40))

    target = state.target % len(platforms)
    plat = platforms[target]

    if player_mm is None:
        # 讀不到自己的位置就不要亂走（可能在跳躍/被畫面遮到），原地等下一圈。
        return state, {"kind": "hold", "name": plat.get("name", f"平台{target}")}

    if on_platform(player_mm, plat, y_tol, x_tol):
        # 站在目標平台上：有怪 → 歸零計數交給打怪；沒怪 → 累積計數，滿了換平台。
        empty = 0 if n_attackable > 0 else state.empty_loops + 1
        if empty >= empty_limit:
            new_target = (target + 1) % len(platforms)
            new_plat = platforms[new_target]
            return (PlatformState(target=new_target, empty_loops=0, move_loops=1),
                    _move_plan(player_mm, new_plat, x_tol,
                               new_plat.get("name", f"平台{new_target}")))
        left, right = plat["x_range"]
        return (PlatformState(target=target, empty_loops=empty, move_loops=0),
                {"kind": "patrol", "left": left, "right": right,
                 "name": plat.get("name", f"平台{target}")})

    # 不在目標平台上 → 前往。卡太久（跳不上去等）就放棄、改守另一平台。
    if state.move_loops + 1 >= move_limit:
        new_target = (target + 1) % len(platforms)
        return (PlatformState(target=new_target, empty_loops=0, move_loops=0),
                {"kind": "patrol",
                 "left": platforms[new_target]["x_range"][0],
                 "right": platforms[new_target]["x_range"][1],
                 "name": platforms[new_target].get("name", f"平台{new_target}")})

    name = plat.get("name", f"平台{target}")
    return (PlatformState(target=target, empty_loops=0, move_loops=state.move_loops + 1),
            _move_plan(player_mm, plat, x_tol, name))


def _move_plan(player_mm, plat, x_tol, name):
    """產生「前往平台」的單步 plan：先走到定點，再邊走邊跳上去。"""
    px = player_mm[0]
    left, right = plat["x_range"]
    center = (left + right) / 2
    jump_x = plat.get("jump_x")
    if jump_x is not None:
        # 有指定起跳點：先走到 jump_x，再朝平台中心邊走邊跳。
        if abs(px - jump_x) > x_tol:
            return {"kind": "approach", "dir": "right" if px < jump_x else "left", "name": name}
        return {"kind": "jump_move", "dir": "right" if px < center else "left", "name": name}
    if (left - x_tol) <= px <= (right + x_tol):
        # 已在平台正下方（x 進範圍但高度不對）→ 邊走邊跳上去。
        return {"kind": "jump_move", "dir": "right" if px < center else "left", "name": name}
    return {"kind": "approach", "dir": "right" if px < left else "left", "name": name}
