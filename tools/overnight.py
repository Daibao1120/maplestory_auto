# -*- coding: utf-8 -*-
"""守夜練等核心（可測試的純狀態機）＋執行殼。

設計：NightWatchCore 是純邏輯——每圈餵入 WorldState（感知快照），回傳
Action 清單（要送的鍵/點擊），不做任何 I/O，因此所有安全規則都能用單元
測試釘死。執行殼（run_daemon）負責截圖、送鍵、檔案 I/O，邏輯薄到不需測。

狀態機：
    VERIFY    驗證補血鍵有效（HP 近滿則延後到第一次實際用藥時驗證）
    DESCEND   受控走落到夠寬的平台（有怪的樓層）
    FARM      按住攻擊＋平台警衛＋補血＋防失效小碎步＋buff 點掉
    IDLE_SAFE 安全模式：不打不走，只防閒置（原地跳）
    STOPPED   結束（釋放所有按鍵）

安全鐵則（皆有對應測試）：
    - 補血鍵未證實有效 → 絕不 FARM
    - HP 低於 45% 持續 → 停止攻擊回 IDLE_SAFE
    - 讀不到玩家點/畫面全黑/視窗不見/前景遺失 → 不送任何移動鍵
    - 使用者實體按鍵 → 立刻讓手（釋放全部）
    - 每次狀態轉換都先釋放按住中的攻擊鍵
    - 超過總時長上限 → STOPPED
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class WorldState:
    """一圈的感知快照（由執行殼提供；測試直接建構）。"""
    now: float
    frame_ok: bool = True          # 有抓到畫面且不是全黑
    fg: bool = True                # 遊戲在前景
    window: bool = True            # 遊戲視窗存在
    user_touch: bool = False       # 使用者實體按著方向鍵
    pos: Optional[Tuple[int, int]] = None      # 小地圖玩家 (x, y)
    span: Optional[dict] = None    # platform_span 結果（width/dist_left/dist_right）
    hp: Optional[float] = None     # 0~1；None = 讀不到
    exp_changed: bool = False
    buff_hit: Optional[Tuple[int, int]] = None  # 要點掉的 buff 螢幕座標
    cmd: str = ""                  # 外部指令：stop / idle / farm


@dataclass
class Action:
    verb: str                      # hold_attack / release_attack / tap / step /
    #                                release_moves / release_all / right_click / log
    arg: object = None


@dataclass
class Stats:
    exp_count: int = 0
    potions: int = 0
    guard_pushes: int = 0
    descents: int = 0
    yields: int = 0


class NightWatchCore:
    """守夜狀態機（純邏輯、無 I/O、時間全由 WorldState.now 驅動）。"""

    WIDE_ENOUGH = 15.0     # 平台寬達此值（校準px）才算可農
    FARMABLE_MIN = 8.0     # FARM 中掉層後平台至少要這麼寬才續農
    HP_POTION_AT = 0.65
    HP_ABORT = 0.45
    HP_ABORT_HOLD = 12.0   # 低血持續秒數才中止（避免瞬間低血誤判）
    VERIFY_HEAL_MIN = 0.02
    POS_LOST_LIMIT = 60.0
    GUARD_EVERY = 2.5
    DISPEL_EVERY = 5.0
    ATTACK_REPRESS = (2.0, 4.0)
    REPOS_EVERY = (45.0, 65.0)
    YIELD_SECONDS = 45.0
    MAX_DESCENTS = 8
    # FARM 中 EXP 停滯超過此秒數 → 一定出事了（測謊視窗跳出、被傳走、
    # 怪被清空…），停手進安全模式並記錄。這是「自動測謊答題」做出來之前
    # 的保命網：測謊沒答會被斷線/標記，絕不能邊掛測謊邊繼續打。
    EXP_STALL_LIMIT = 480.0

    def __init__(self, max_seconds=7.5 * 3600, potion_key="delete", rng=None):
        self.max_seconds = max_seconds
        self.potion_key = potion_key
        self.rng = rng or random.Random(0)
        self.state = "VERIFY"
        self.stats = Stats()
        self.potion_works: Optional[bool] = None
        self._t0: Optional[float] = None
        self._atk_held = False
        self._atk_refresh = 0.0
        self._last_guard = 0.0
        self._last_dispel = 0.0
        self._next_repos = 0.0
        self._last_potion = 0.0
        self._verify_until = 0.0
        self._verify_hp0: Optional[float] = None
        self._hp_low_since: Optional[float] = None
        self._pos_lost_since: Optional[float] = None
        self._yield_until = 0.0
        self._farm_y: Optional[int] = None
        self._pending_verify = False   # FARM 中第一次用藥要驗證藥效
        self._descend_count = 0
        self._last_exp_ts: Optional[float] = None   # FARM 中最近一次 EXP 進帳

    # ---- 對外主入口 ----
    def tick(self, w: WorldState) -> List[Action]:
        acts: List[Action] = []
        if self._t0 is None:
            self._t0 = w.now
            self._next_repos = w.now + self.rng.uniform(*self.REPOS_EVERY)

        if self.state == "STOPPED":
            return acts

        # 0) 硬性終止
        if w.cmd == "stop" or (w.now - self._t0) >= self.max_seconds:
            return self._stop(acts, "指令/時限")

        # 1) 使用者優先：實體按鍵 → 全放開、讓手
        if w.user_touch:
            self._release_attack(acts)
            acts.append(Action("release_all"))
            self._yield_until = w.now + self.YIELD_SECONDS
            self.stats.yields += 1
            acts.append(Action("log", "使用者操作 → 讓手"))
            return acts
        if w.now < self._yield_until:
            return acts   # 讓手期間完全沉默

        # 2) 環境安全：視窗/前景/畫面 有問題 → 不送任何鍵
        if not w.window or not w.frame_ok or not w.fg:
            self._release_attack(acts)
            return acts

        # 3) 外部指令
        if w.cmd == "idle" and self.state in ("FARM", "DESCEND", "VERIFY"):
            self._release_attack(acts)
            self._to("IDLE_SAFE", acts, "外部指令 idle")
        elif w.cmd == "farm" and self.state == "IDLE_SAFE":
            self._to("VERIFY", acts, "外部指令 farm")

        # 4) 玩家點監控（IDLE_SAFE 不需要）
        if w.pos is None and self.state in ("DESCEND", "FARM"):
            if self._pos_lost_since is None:
                self._pos_lost_since = w.now
            elif w.now - self._pos_lost_since > self.POS_LOST_LIMIT:
                self._release_attack(acts)
                self._to("IDLE_SAFE", acts, "玩家點消失過久")
        elif w.pos is not None:
            self._pos_lost_since = None

        # 5) 補血（讀得到 HP 且已知藥有效，或正在驗證中）
        self._potion_logic(w, acts)

        # 6) 低血保險
        if w.hp is not None and w.hp < self.HP_ABORT:
            if self._hp_low_since is None:
                self._hp_low_since = w.now
            elif (w.now - self._hp_low_since > self.HP_ABORT_HOLD
                  and self.state in ("FARM", "DESCEND")):
                self._release_attack(acts)
                self._to("IDLE_SAFE", acts, f"HP 持續過低（{w.hp:.0%}）")
        else:
            self._hp_low_since = None

        # 7) 分狀態行為
        if self.state == "VERIFY":
            self._tick_verify(w, acts)
        elif self.state == "DESCEND":
            self._tick_descend(w, acts)
        elif self.state == "FARM":
            self._tick_farm(w, acts)
        elif self.state == "IDLE_SAFE":
            self._tick_idle(w, acts)

        if w.exp_changed:
            self.stats.exp_count += 1
        return acts

    # ---- 內部 ----
    def _stop(self, acts, why):
        self._release_attack(acts)
        acts.append(Action("release_all"))
        acts.append(Action("log", f"結束（{why}）"))
        self.state = "STOPPED"
        return acts

    def _to(self, state, acts, why):
        if state != self.state:
            acts.append(Action("log", f"{self.state} → {state}（{why}）"))
            self.state = state

    def _release_attack(self, acts):
        if self._atk_held:
            acts.append(Action("release_attack"))
            self._atk_held = False

    def _potion_logic(self, w, acts):
        if w.hp is None:
            return
        # 驗證中：看 HP 是否上升
        if self._verify_hp0 is not None:
            if w.now <= self._verify_until:
                if w.hp > self._verify_hp0 + self.VERIFY_HEAL_MIN:
                    self.potion_works = True
                    self._verify_hp0 = None
                    acts.append(Action("log", "補血鍵驗證：有效"))
                return
            # 驗證期滿仍沒回升 → 無效
            self.potion_works = False
            self._verify_hp0 = None
            acts.append(Action("log", "補血鍵驗證：無效"))
            if self.state in ("FARM", "DESCEND"):
                self._release_attack(acts)
                self._to("IDLE_SAFE", acts, "補血鍵無效")
            return
        # 一般補血：只在已證實有效（或首次待驗證）時按
        if w.hp < self.HP_POTION_AT and w.now - self._last_potion > 2.0:
            if self.potion_works is False:
                return
            acts.append(Action("tap", self.potion_key))
            self._last_potion = w.now
            self.stats.potions += 1
            if self.potion_works is None:
                # 第一次實戰用藥 = 順便驗證
                self._verify_hp0 = w.hp
                self._verify_until = w.now + 2.5
                acts.append(Action("log", "首次用藥 → 驗證藥效中"))

    def _tick_verify(self, w, acts):
        if w.hp is None:
            self._to("IDLE_SAFE", acts, "讀不到 HP")
            return
        if self._verify_hp0 is not None:
            return   # 等驗證結果（_potion_logic 處理）
        if self.potion_works is True:
            self._to("DESCEND", acts, "補血鍵已證實有效")
            return
        if self.potion_works is False:
            self._to("IDLE_SAFE", acts, "補血鍵無效")
            return
        if w.hp < 0.93:
            acts.append(Action("tap", self.potion_key))
            self.stats.potions += 1
            self._verify_hp0 = w.hp
            self._verify_until = w.now + 2.5
            acts.append(Action("log", "按補血鍵驗證藥效"))
        else:
            self._to("DESCEND", acts, "HP 近滿，藥效留到首次用藥時驗證")

    def _tick_descend(self, w, acts):
        if w.pos is None or w.span is None:
            return
        if w.span["width"] >= self.WIDE_ENOUGH:
            self._farm_y = w.pos[1]
            self._to("FARM", acts, f"到達寬平台（{w.span['width']:.1f}）")
            return
        if self._descend_count >= self.MAX_DESCENTS:
            self._to("IDLE_SAFE", acts, "下降次數達上限仍無寬平台")
            return
        d = "left" if w.span["dist_left"] <= w.span["dist_right"] else "right"
        acts.append(Action("step", (d, 0.4)))
        acts.append(Action("release_moves"))
        self._descend_count += 1
        self.stats.descents += 1

    def _tick_farm(self, w, acts):
        # EXP 停滯保險：太久沒進帳 = 測謊視窗/被傳走/異常 → 停手
        if self._last_exp_ts is None:
            self._last_exp_ts = w.now
        if w.exp_changed:
            self._last_exp_ts = w.now
        elif w.now - self._last_exp_ts > self.EXP_STALL_LIMIT:
            self._release_attack(acts)
            self._to("IDLE_SAFE", acts, "EXP 停滯過久（測謊視窗/異常？）")
            return

        # 位置不明的圈：不發起/補壓任何按鍵（已按住的維持原狀，等讀到再說）
        if w.pos is None or w.span is None:
            return

        # 攻擊保持
        if not self._atk_held:
            acts.append(Action("hold_attack"))
            self._atk_held = True
            self._atk_refresh = w.now + self.rng.uniform(*self.ATTACK_REPRESS)
        elif w.now >= self._atk_refresh:
            acts.append(Action("repress_attack"))
            self._atk_refresh = w.now + self.rng.uniform(*self.ATTACK_REPRESS)

        # 掉層：以新位置為家；太窄就回安全模式
        if self._farm_y is not None and abs(w.pos[1] - self._farm_y) >= 3:
            if w.span["width"] < self.FARMABLE_MIN:
                self._release_attack(acts)
                self._to("IDLE_SAFE", acts, f"掉到窄平台（{w.span['width']:.1f}）")
                return
            self._farm_y = w.pos[1]
            acts.append(Action("log", "掉層 → 以新位置為家"))

        # 平台警衛
        if w.now - self._last_guard >= self.GUARD_EVERY:
            self._last_guard = w.now
            margin = 2 if w.span["width"] < 10 else 3
            if w.span["dist_left"] < margin or w.span["dist_right"] < margin:
                d = "right" if w.span["dist_left"] < w.span["dist_right"] else "left"
                self._release_attack(acts)
                acts.append(Action("step", (d, 0.15)))
                acts.append(Action("release_moves"))
                self.stats.guard_pushes += 1

        # 防失效小碎步
        if w.now >= self._next_repos:
            self._next_repos = w.now + self.rng.uniform(*self.REPOS_EVERY)
            if w.span["dist_left"] > 3 and w.span["dist_right"] > 3:
                d = "right" if w.span["dist_left"] < w.span["dist_right"] else "left"
                self._release_attack(acts)
                acts.append(Action("step", (d, 0.15)))
                acts.append(Action("step", ("left" if d == "right" else "right", 0.13)))
                acts.append(Action("release_moves"))

        # buff 點掉
        if w.buff_hit is not None and w.now - self._last_dispel >= self.DISPEL_EVERY:
            self._last_dispel = w.now
            acts.append(Action("right_click", w.buff_hit))

    def _tick_idle(self, w, acts):
        self._release_attack(acts)
        if w.now >= self._next_repos:
            self._next_repos = w.now + self.rng.uniform(240, 360)
            acts.append(Action("tap_jump"))
