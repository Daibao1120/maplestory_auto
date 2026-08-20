# -*- coding: utf-8 -*-
"""守夜練等核心（可測試純狀態機）＋執行殼。

NightWatchCore 是純邏輯：每圈餵入 WorldState（感知快照），回傳 Action 清單，
不做任何 I/O；時間全由 WorldState.now 驅動，因此整晚流程可在毫秒內模擬、
所有安全規則都能用單元測試釘死。run_daemon() 是薄執行殼（截圖/送鍵/檔案）。

狀態機：
    VERIFY      驗證補血鍵（HP 近滿則延後到第一次實際用藥時驗證）
    DESCEND     受控走落到夠寬的平台（每次進入重置步數預算）
    FARM        按住攻擊＋平台警衛＋補血＋防失效小碎步＋buff 點掉
    IDLE_SAFE   可恢復閒置：防閒置跳；條件乾淨一段時間後自動回去試（次數有限）
    IDLE_SILENT 危險靜默：疑似測謊視窗/無法補血且掉血——零輸入，讓遊戲
                自然踢線保命；只有使用者/外部指令能喚醒
    STOPPED     結束（釋放所有按鍵）

Action 動詞（執行殼契約，一個都不能漏）：
    hold_attack / repress_attack / release_attack / tap / tap_jump /
    step / turn / release_moves / release_all / right_click / log

安全鐵則（皆有對應測試）：
    - 補血鍵未證實有效 → 不 FARM；驗證失敗可重試（非一票永決），連續
      失敗才判定無效，且之後仍會定時重驗
    - HP<45% 持續 → 停止攻擊；HP 讀不到（FARM/DESCEND）有專屬看門狗，
      且「讀不到」凍結而非重置低血計時
    - 位置或平台讀不到 → 立刻放開攻擊鍵、不送移動鍵；持續過久 → 閒置
    - 絕望補血：HP<50% 時就算藥效未證實也會按補血鍵（按了沒損失）
    - 使用者實體按鍵 → 立刻讓手（邊緣觸發，不重複洗版）
    - EXP 停滯（測謊視窗代理）→ IDLE_SILENT 零輸入
    - IDLE_SAFE 中持續掉血且無法補 → 升級 IDLE_SILENT（別再幫遊戲保持
      連線，讓 AFK 踢線保命）
    - 每個離開 FARM 的轉換都先放開攻擊鍵；STOPPED/讓手釋放全部
    - 超過總時長上限 → STOPPED
"""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class WorldState:
    now: float
    frame_ok: bool = True
    fg: bool = True
    window: bool = True
    user_touch: bool = False
    pos: Optional[Tuple[int, int]] = None
    span: Optional[dict] = None
    hp: Optional[float] = None
    mp: Optional[float] = None
    exp_changed: bool = False
    buff_hit: Optional[Tuple[int, int]] = None
    cmd: str = ""
    # 怪物偵測（模板匹配、已用同高度帶過濾）：角色左右兩側各幾隻
    mon_left: int = 0
    mon_right: int = 0
    # 免模板的活動偵測給的面向提示（"left"/"right"/None）——模板沒抓到怪時的後備
    mon_hint: Optional[str] = None


@dataclass
class Action:
    verb: str
    arg: object = None


@dataclass
class Stats:
    exp_count: int = 0
    potions: int = 0
    guard_pushes: int = 0
    descents: int = 0
    yields: int = 0
    recoveries: int = 0
    buffs_cast: int = 0
    mp_potions: int = 0
    skills_cast: int = 0


@dataclass
class ClassProfile:
    """職業設定：不同職業的攻擊方式、buff、MP 需求都不一樣。

    attack_mode:
        hold   —— 按住攻擊鍵（弓箭手/多數平砍職業最大輸出）
        tap    —— 固定間隔連點（「按一下打一下」的技能）
        rotate —— 技能輪替（法師/騎士等有多顆技能與冷卻）
    rotation: [(鍵, 冷卻秒數), ...]；rotate 模式依冷卻挑下一顆可用技能。
    buffs:    [(鍵, 每幾秒重施), ...]；FARM 期間到期就補。
    """
    name: str = "generic"
    attack_mode: str = "hold"
    attack_key: str = "ctrl"
    attack_interval: float = 0.15
    rotation: tuple = ()
    buffs: tuple = ()
    mp_key: Optional[str] = None
    mp_threshold: float = 0.30
    mp_cooldown: float = 2.5
    pickup_key: Optional[str] = None      # 撿物鍵（通常是 z 或 space）
    pickup_every: float = 4.0             # 幾秒撿一次

    @classmethod
    def from_config(cls, cfg):
        c = (cfg or {})
        atk = c.get("attack") or {}
        mp = c.get("mp") or {}
        rot = tuple((r.get("key"), float(r.get("cooldown", 1.0)))
                    for r in (atk.get("rotation") or []) if r.get("key"))
        buffs = tuple((b.get("key"), float(b.get("every", 180)))
                      for b in (c.get("buffs") or []) if b.get("key"))
        return cls(
            name=c.get("name", "generic"),
            attack_mode=atk.get("mode", "hold"),
            attack_key=atk.get("key", "ctrl"),
            attack_interval=float(atk.get("interval", 0.15)),
            rotation=rot, buffs=buffs,
            mp_key=mp.get("potion_key"),
            mp_threshold=float(mp.get("threshold", 0.30)),
            mp_cooldown=float(mp.get("cooldown", 2.5)),
            pickup_key=(c.get("pickup") or {}).get("key"),
            pickup_every=float((c.get("pickup") or {}).get("every", 4.0)))


class NightWatchCore:
    WIDE_ENOUGH = 15.0
    FARMABLE_MIN = 8.0
    HP_POTION_AT = 0.65
    HP_DESPERATE = 0.50      # 低於此值：藥效未證實也照按（絕望補血）
    HP_ABORT = 0.45
    HP_ABORT_HOLD = 12.0
    HP_LOST_LIMIT = 30.0     # FARM/DESCEND 中 HP 連續讀不到 → 閒置
    SPAN_LOST_LIMIT = 45.0   # FARM/DESCEND 中平台連續讀不到 → 閒置
    POS_LOST_LIMIT = 60.0
    VERIFY_WINDOW = 4.0
    VERIFY_HEAL_MIN = 0.02
    POTION_FAILS_TO_CONDEMN = 2   # 連續驗證失敗達此次數才判「無效」
    POTION_RETRY_AFTER = 600.0    # 判無效後多久允許重驗
    POTION_CONDEMN_MAX = 3        # 一晚最多重驗輪數，之後永久放棄
    GUARD_EVERY = 2.5
    DISPEL_EVERY = 5.0
    ATTACK_REPRESS = (2.0, 4.0)
    REPOS_EVERY = (45.0, 65.0)
    IDLE_JUMP_EVERY = (240.0, 360.0)
    YIELD_SECONDS = 45.0
    MAX_DESCENTS = 8
    EXP_STALL_LIMIT = 480.0
    # 用「經驗值有沒有進帳」當回饋來決定面向：打得到怪就會有 EXP，久久沒進帳
    # 就轉向另一邊試。這比幾何判斷可靠——不需要知道角色在畫面哪個像素，
    # 而角色螢幕位置會隨鏡頭夾邊變動（實測不同地圖偏移 120~200px）。
    EXP_FLIP_AFTER = 20.0
    IDLE_RECOVER_AFTER = 180.0    # IDLE_SAFE 待滿此秒數且條件乾淨 → 自動再試
    IDLE_CLEAN_NEED = 30.0        # 「乾淨」需持續的秒數
    IDLE_RECOVER_MAX = 4          # 一晚自動恢復次數上限
    IDLE_HP_DROP_SILENT = 0.12    # IDLE_SAFE 期間掉血超過此幅度 → 轉入靜默

    # 快捷欄全鍵位：不知道藥水放哪一格就自己一個一個試（看 HP 有沒有回升），
    # 不必問使用者。第一個是設定檔給的鍵，其餘依序輪替。
    POTION_CANDIDATES = ("delete", "insert", "home", "pageup", "end",
                         "pagedown", "shift")

    def __init__(self, max_seconds=7.5 * 3600, potion_key="delete", rng=None,
                 potion_candidates=None, heal_mode="self", last_resort_hp=0.20,
                 profile: Optional["ClassProfile"] = None):
        # 職業設定：攻擊方式/技能輪替/buff/MP 都因職業而異
        self.profile = profile or ClassProfile()
        # heal_mode="external"：補血由寵物/使用者負責——核心完全不碰藥水鍵、
        # 不做藥效驗證、也不因低血停手（只保留 last_resort_hp 最後保險）
        self.heal_mode = heal_mode
        self.last_resort_hp = last_resort_hp
        self.max_seconds = max_seconds
        cands = list(potion_candidates or self.POTION_CANDIDATES)
        if potion_key in cands:
            cands.remove(potion_key)
        self.potion_keys = [potion_key] + cands   # 設定值優先，其餘輪流試
        self._key_idx = 0
        self.potion_key = potion_key
        self.rng = rng or random.Random(0)
        self.state = "VERIFY"
        self.stats = Stats()
        self.potion_works: Optional[bool] = None
        self._potion_fails = 0
        self._potion_condemns = 0
        self._potion_retry_at: Optional[float] = None
        self._t0: Optional[float] = None
        self._atk_held = False
        self._atk_refresh = 0.0
        self._last_guard = 0.0
        self._last_dispel = 0.0
        self._next_repos: Optional[float] = None
        self._last_potion: Optional[float] = None
        self._last_desperate: Optional[float] = None
        self._verify_hp0: Optional[float] = None
        self._verify_until = 0.0
        self._hp_low_since: Optional[float] = None
        self._hp_lost_since: Optional[float] = None
        self._pos_lost_since: Optional[float] = None
        self._span_lost_since: Optional[float] = None
        self._yield_until = 0.0
        self._touch_active = False
        self._farm_y: Optional[int] = None
        self._descend_count = 0
        self._last_exp_ts: Optional[float] = None
        self._idle_entered = 0.0
        self._idle_hp0: Optional[float] = None
        self._clean_since: Optional[float] = None
        self.facing: Optional[str] = None      # 目前面向（依怪物分佈決定）
        self._last_turn = 0.0
        self._buff_due = {}          # 鍵 → 下次施放時間
        self._skill_ready = {}       # 技能鍵 → 下次可用時間
        self._next_tap = 0.0         # tap 模式的下一次攻擊時間
        self._last_mp_potion = 0.0
        self._next_pickup = 0.0

    # ---- 主入口 ----
    def tick(self, w: WorldState) -> List[Action]:
        acts: List[Action] = []
        if self._t0 is None:
            self._t0 = w.now
            self._next_repos = w.now + self.rng.uniform(*self.REPOS_EVERY)

        if self.state == "STOPPED":
            return acts

        if w.cmd == "stop" or (w.now - self._t0) >= self.max_seconds:
            return self._stop(acts, "指令/時限")

        # 使用者優先（邊緣觸發；讓手期間完全沉默）
        if w.user_touch:
            if not self._touch_active:
                self._touch_active = True
                self._release_attack(acts)
                acts.append(Action("release_all"))
                self.stats.yields += 1
                acts.append(Action("log", "使用者操作 → 讓手"))
            self._yield_until = w.now + self.YIELD_SECONDS
            return acts
        self._touch_active = False
        if w.now < self._yield_until:
            return acts

        # 環境安全：任一異常 → 放開攻擊鍵、不送任何鍵
        if not w.window or not w.frame_ok or not w.fg:
            self._release_attack(acts)
            return acts

        # 外部指令
        if w.cmd == "idle" and self.state in ("FARM", "DESCEND", "VERIFY"):
            self._release_attack(acts)
            self._to("IDLE_SAFE", acts, "外部指令 idle", w)
        elif w.cmd == "farm" and self.state in ("IDLE_SAFE", "IDLE_SILENT"):
            self._to("VERIFY", acts, "外部指令 farm", w)

        # IDLE_SILENT：零輸入（環境檢查後直接返回；只有上面的 cmd 能離開）
        if self.state == "IDLE_SILENT":
            self._release_attack(acts)
            if w.exp_changed:
                self.stats.exp_count += 1
            return acts

        # 感知看門狗（FARM/DESCEND）
        self._sensor_watchdogs(w, acts)

        # 補血（含驗證與絕望補血）；external 模式完全交給寵物/使用者
        if self.heal_mode != "external":
            self._potion_logic(w, acts)

        # 低血保險：hp=None 凍結（不重置）計時
        abort_hp = (self.last_resort_hp if self.heal_mode == "external"
                    else self.HP_ABORT)
        if w.hp is not None:
            if w.hp < abort_hp:
                if self._hp_low_since is None:
                    self._hp_low_since = w.now
                elif (w.now - self._hp_low_since > self.HP_ABORT_HOLD
                      and self.state in ("FARM", "DESCEND")):
                    self._release_attack(acts)
                    self._to("IDLE_SAFE", acts, f"HP 持續過低（{w.hp:.0%}）", w)
            else:
                self._hp_low_since = None

        if self.state == "VERIFY":
            self._tick_verify(w, acts)
        elif self.state == "DESCEND":
            self._tick_descend(w, acts)
        elif self.state == "FARM":
            self._tick_farm(w, acts)
        elif self.state == "IDLE_SAFE":
            self._tick_idle_safe(w, acts)

        if w.exp_changed:
            self.stats.exp_count += 1
        return acts

    # ---- 轉換與收尾 ----
    def _stop(self, acts, why):
        self._release_attack(acts)
        acts.append(Action("release_all"))
        acts.append(Action("log", f"結束（{why}）"))
        self.state = "STOPPED"
        return acts

    def _to(self, state, acts, why, w):
        if state == self.state:
            return
        acts.append(Action("log", f"{self.state} → {state}（{why}）"))
        self.state = state
        if state == "FARM":
            self._last_exp_ts = None          # 重入不吃舊時間戳（防假性停滯）
            self._next_repos = w.now + self.rng.uniform(*self.REPOS_EVERY)
        elif state == "DESCEND":
            self._descend_count = 0           # 每回合獨立步數預算
        elif state in ("IDLE_SAFE", "IDLE_SILENT"):
            self._idle_entered = w.now
            self._idle_hp0 = w.hp
            self._clean_since = None
            self._next_repos = w.now + self.rng.uniform(*self.IDLE_JUMP_EVERY)

    def _release_attack(self, acts):
        if self._atk_held:
            acts.append(Action("release_attack"))
            self._atk_held = False

    # ---- 感知看門狗 ----
    def _sensor_watchdogs(self, w, acts):
        if self.state not in ("FARM", "DESCEND"):
            self._pos_lost_since = self._span_lost_since = self._hp_lost_since = None
            return
        # 位置/平台讀不到：立刻放開攻擊鍵（按著攻擊站在未知位置是危險行為）
        if w.pos is None or w.span is None:
            self._release_attack(acts)
        for attr, ok, limit, why in (
                ("_pos_lost_since", w.pos is not None, self.POS_LOST_LIMIT, "玩家點消失過久"),
                ("_span_lost_since", w.span is not None, self.SPAN_LOST_LIMIT, "平台讀不到過久"),
                ("_hp_lost_since", w.hp is not None, self.HP_LOST_LIMIT, "HP 讀不到過久")):
            if ok:
                setattr(self, attr, None)
            else:
                since = getattr(self, attr)
                if since is None:
                    setattr(self, attr, w.now)
                elif w.now - since > limit:
                    self._release_attack(acts)
                    self._to("IDLE_SAFE", acts, why, w)
                    return

    # ---- 補血與驗證 ----
    def _potion_logic(self, w, acts):
        if self.state in ("IDLE_SILENT", "STOPPED"):
            return
        if w.hp is None:
            return
        # 判無效後的定時重驗
        if (self.potion_works is False and self._potion_retry_at is not None
                and w.now >= self._potion_retry_at
                and self._potion_condemns < self.POTION_CONDEMN_MAX):
            self.potion_works = None
            self._potion_fails = 0
            self._potion_retry_at = None
            acts.append(Action("log", "補血鍵重新給機會（定時重驗）"))

        # 驗證進行中
        if self._verify_hp0 is not None:
            if w.hp > self._verify_hp0 + self.VERIFY_HEAL_MIN:
                self.potion_works = True
                self._potion_fails = 0
                self._verify_hp0 = None
                acts.append(Action("log", "補血鍵驗證：有效"))
                return
            if w.now > self._verify_until:
                # 過期收尾也要看最後這一筆（遲到的成功不能當失敗）——上面
                # 已檢查過 w.hp；到這裡代表確實沒回升
                self._verify_hp0 = None
                self._potion_fails += 1
                if self._potion_fails >= self.POTION_FAILS_TO_CONDEMN:
                    # 這個鍵不行 → 自動換下一個快捷鍵再試（藥水可能放別格）
                    if self._key_idx + 1 < len(self.potion_keys):
                        self._key_idx += 1
                        self.potion_key = self.potion_keys[self._key_idx]
                        self._potion_fails = 0
                        self.potion_works = None
                        acts.append(Action("log", f"改試下一個補血鍵：{self.potion_key}"))
                        return
                    self.potion_works = False
                    self._potion_condemns += 1
                    if self._potion_condemns < self.POTION_CONDEMN_MAX:
                        self._potion_retry_at = w.now + self.POTION_RETRY_AFTER
                        self._key_idx = 0                       # 下一輪從頭再掃一次
                        self.potion_key = self.potion_keys[0]
                    acts.append(Action("log",
                                       f"所有快捷鍵都試過仍無效（第 {self._potion_condemns} 輪）"))
                    if self.state in ("FARM", "DESCEND"):
                        self._release_attack(acts)
                        self._to("IDLE_SAFE", acts, "找不到有效補血鍵", w)
                else:
                    acts.append(Action("log", "補血鍵驗證：本次不確定（再試一次）"))
            return

        # 絕望補血：低到危險就算藥效未證實也按（無效頂多沒反應）
        if (w.hp < self.HP_DESPERATE
                and (self._last_desperate is None or w.now - self._last_desperate > 5.0)):
            acts.append(Action("tap", self.potion_key))
            self._last_desperate = w.now
            self.stats.potions += 1
            return

        # 一般補血
        if w.hp < self.HP_POTION_AT and self.potion_works is not False:
            if self._last_potion is not None and w.now - self._last_potion <= 2.0:
                return
            acts.append(Action("tap", self.potion_key))
            self._last_potion = w.now
            self.stats.potions += 1
            if self.potion_works is None:
                self._verify_hp0 = w.hp
                self._verify_until = w.now + self.VERIFY_WINDOW
                self._release_attack(acts)   # 驗證期間別打（減少傷害干擾判讀）
                acts.append(Action("log", "用藥並驗證藥效中"))

    # ---- 各狀態 ----
    def _tick_verify(self, w, acts):
        if self.heal_mode == "external":
            # 補血交給寵物/使用者 → 不驗證、不按藥，直接去找怪
            self._to("DESCEND", acts, "補血由寵物負責，直接開工", w)
            return
        if w.hp is None:
            self._to("IDLE_SAFE", acts, "讀不到 HP", w)
            return
        if self._verify_hp0 is not None:
            return
        if self.potion_works is True:
            self._to("DESCEND", acts, "補血鍵已證實有效", w)
        elif self.potion_works is False:
            self._to("IDLE_SAFE", acts, "補血鍵無效", w)
        elif w.hp < 0.93:
            acts.append(Action("tap", self.potion_key))
            self._last_potion = w.now
            self.stats.potions += 1
            self._verify_hp0 = w.hp
            self._verify_until = w.now + self.VERIFY_WINDOW
            acts.append(Action("log", "按補血鍵驗證藥效"))
        else:
            self._to("DESCEND", acts, "HP 近滿，藥效留到首次用藥時驗證", w)

    def _tick_descend(self, w, acts):
        if w.pos is None or w.span is None:
            return   # 看門狗負責計時
        if w.span["width"] >= self.WIDE_ENOUGH:
            self._farm_y = w.pos[1]
            self._to("FARM", acts, f"到達寬平台（{w.span['width']:.1f}）", w)
            return
        if self._descend_count >= self.MAX_DESCENTS:
            self._to("IDLE_SAFE", acts, "本回合下降步數用盡", w)
            return
        d = "left" if w.span["dist_left"] <= w.span["dist_right"] else "right"
        acts.append(Action("step", (d, 0.4)))
        acts.append(Action("release_moves"))
        self._descend_count += 1
        self.stats.descents += 1

    def _tick_farm(self, w, acts):
        # EXP 停滯（測謊視窗代理）→ 危險靜默
        if self._last_exp_ts is None:
            self._last_exp_ts = w.now
        if w.exp_changed:
            self._last_exp_ts = w.now
        elif w.now - self._last_exp_ts > self.EXP_STALL_LIMIT:
            self._release_attack(acts)
            self._to("IDLE_SILENT", acts, "EXP 停滯過久（測謊視窗/異常？）→ 零輸入", w)
            return

        if w.pos is None or w.span is None:
            return   # 看門狗已放開攻擊鍵並計時

        # 掉層
        if self._farm_y is not None and abs(w.pos[1] - self._farm_y) >= 3:
            if w.span["width"] < self.FARMABLE_MIN:
                self._release_attack(acts)
                self._to("IDLE_SAFE", acts, f"掉到窄平台（{w.span['width']:.1f}）", w)
                return
            self._farm_y = w.pos[1]
            acts.append(Action("log", "掉層 → 以新位置為家"))

        # 驗證期間不進攻（判讀乾淨）
        if self._verify_hp0 is not None:
            return

        # 決定面向：優先用怪物偵測（有明確一側就轉過去）；沒有偵測資訊時
        # 改用經驗值回饋——久久沒進帳就換邊試（不依賴角色螢幕座標）。
        side = None
        if w.mon_left > w.mon_right:
            side = "left"
        elif w.mon_right > w.mon_left:
            side = "right"
        if side and side != self.facing and w.now - self._last_turn > 1.0:
            self.facing = side
            self._last_turn = w.now
            acts.append(Action("turn", side))
        elif (side is None and w.mon_hint
              and w.mon_hint != self.facing
              and w.now - self._last_turn > 3.0):
            # 模板沒抓到怪 → 用「哪邊在動」當後備提示（換地圖也能用）
            self.facing = w.mon_hint
            self._last_turn = w.now
            acts.append(Action("turn", self.facing))
        elif (side is None and not w.mon_hint
              and w.now - self._last_exp_ts > self.EXP_FLIP_AFTER
              and w.now - self._last_turn > self.EXP_FLIP_AFTER):
            self.facing = "left" if self.facing == "right" else "right"
            self._last_turn = w.now
            self._last_exp_ts = w.now      # 給新方向一個完整的觀察窗
            acts.append(Action("turn", self.facing))
            acts.append(Action("log", f"沒有經驗值進帳 → 轉向{self.facing}試"))

        # buff / MP / 撿物（職業設定驅動）
        self._buff_logic(w, acts)
        self._mp_logic(w, acts)
        self._pickup_logic(w, acts)

        # 攻擊：依職業設定分派（按住／連點／技能輪替）
        self._attack_logic(w, acts)

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
        if self._next_repos is not None and w.now >= self._next_repos:
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

    # ---- 職業行為（攻擊/ buff / MP）----
    def _attack_logic(self, w, acts):
        p = self.profile
        if p.attack_mode == "hold":
            if not self._atk_held:
                acts.append(Action("hold_attack"))
                self._atk_held = True
                self._atk_refresh = w.now + self.rng.uniform(*self.ATTACK_REPRESS)
            elif w.now >= self._atk_refresh:
                acts.append(Action("repress_attack"))
                self._atk_refresh = w.now + self.rng.uniform(*self.ATTACK_REPRESS)
            return
        # 非按住模式：先確保攻擊鍵沒被按著
        self._release_attack(acts)
        if p.attack_mode == "rotate" and p.rotation:
            for key, cd in p.rotation:          # 依設定順序挑第一顆冷卻好的
                if w.now >= self._skill_ready.get(key, 0.0):
                    acts.append(Action("tap", key))
                    self._skill_ready[key] = w.now + cd
                    self.stats.skills_cast += 1
                    return
            return                               # 全在冷卻 → 這圈不動作
        # tap 模式：固定間隔連點（帶輕微抖動，不像碼表）
        if w.now >= self._next_tap:
            acts.append(Action("tap", p.attack_key))
            self.stats.skills_cast += 1
            self._next_tap = w.now + p.attack_interval * self.rng.uniform(0.85, 1.25)

    def _buff_logic(self, w, acts):
        """到期就補 buff（每個 buff 各自的重施間隔）。"""
        for key, every in self.profile.buffs:
            due = self._buff_due.get(key)
            if due is None or w.now >= due:
                acts.append(Action("tap", key))
                self._buff_due[key] = w.now + every
                self.stats.buffs_cast += 1
                return                           # 一圈只補一顆，避免連打一串

    def _pickup_logic(self, w, acts):
        """定時撿物（掉落的楓幣/道具）。沒設定撿物鍵就完全不動作。"""
        p = self.profile
        if not p.pickup_key:
            return
        if w.now >= self._next_pickup:
            acts.append(Action("tap", p.pickup_key))
            self._next_pickup = w.now + p.pickup_every * self.rng.uniform(0.9, 1.2)

    def _mp_logic(self, w, acts):
        """MP 不足就補（法師等吃 MP 的職業必要）。"""
        p = self.profile
        if not p.mp_key or w.mp is None:
            return
        if w.mp < p.mp_threshold and w.now - self._last_mp_potion >= p.mp_cooldown:
            acts.append(Action("tap", p.mp_key))
            self._last_mp_potion = w.now
            self.stats.mp_potions += 1

    def _tick_idle_safe(self, w, acts):
        self._release_attack(acts)
        # 危險升級：閒置期間持續掉血且補不回來 → 零輸入讓 AFK 踢線保命
        if w.hp is not None:
            drop = (self._idle_hp0 - w.hp) if self._idle_hp0 is not None else 0.0
            if w.hp < self.HP_ABORT or drop > self.IDLE_HP_DROP_SILENT:
                self._to("IDLE_SILENT", acts,
                         f"閒置中仍掉血（HP {w.hp:.0%}）→ 零輸入", w)
                return
        # 自動恢復：待滿時間且條件連續乾淨、恢復次數未用盡
        clean = (w.pos is not None and w.span is not None
                 and w.hp is not None and w.hp >= 0.60)
        if clean:
            if self._clean_since is None:
                self._clean_since = w.now
        else:
            self._clean_since = None
        if (w.now - self._idle_entered >= self.IDLE_RECOVER_AFTER
                and self._clean_since is not None
                and w.now - self._clean_since >= self.IDLE_CLEAN_NEED
                and self.stats.recoveries < self.IDLE_RECOVER_MAX):
            self.stats.recoveries += 1
            self._to("VERIFY", acts,
                     f"條件乾淨 → 自動恢復（第 {self.stats.recoveries} 次）", w)
            return
        # 防閒置跳
        if self._next_repos is not None and w.now >= self._next_repos:
            self._next_repos = w.now + self.rng.uniform(*self.IDLE_JUMP_EVERY)
            acts.append(Action("tap_jump"))


def exp_per_hour(events, now, window=900.0):
    """由 EXP 進帳時間戳估算「每小時進帳次數」（純函式，可測試）。

    events: 遞增的時間戳清單；只看最近 window 秒內的事件。
    不足 60 秒的觀察不外推（回 0.0），避免開場數字亂跳。
    """
    if not events:
        return 0.0
    recent = [t for t in events if now - t <= window]
    if len(recent) < 2:
        return 0.0
    span = now - recent[0]
    if span < 60.0:
        return 0.0
    return len(recent) * 3600.0 / span


class Perception:
    """感知層（截圖＋辨識）→ WorldState；控制台與守夜 daemon 共用。

    只負責「看」，不送任何按鍵；所有安全決策都在 NightWatchCore。
    """

    def __init__(self, cfg, root_dir):
        import numpy as np
        from src.capture import ScreenCapture
        from src.vision import MinimapLocator, PlayerTracker, PlayerAnchor
        from src.vision.monster import MonsterDetector
        self._np = np
        self.cfg = cfg
        v = cfg["vision"]
        # 畫面正規化：不論遊戲視窗多大，一律先縮到基準尺寸再做辨識。
        # 使用者改視窗/解析度（實測 2736→3840）時，硬寫的 ROI 與模板會全部
        # 錯位；正規化後所有座標只需校準一次，且小圖處理也快得多。
        self.canon = tuple(v.get("canonical_size") or [1371, 808])
        self.raw_size = None      # 最近一次原始畫面尺寸（換算螢幕座標用）
        self.cap = ScreenCapture(backend="mss", window_title=cfg["window"]["title"])
        self.mm = MinimapLocator(v["minimap"])
        self.tracker = PlayerTracker()
        pa = v.get("player_anchor") or {}
        self.anchor = PlayerAnchor(
            template_path=os.path.join(root_dir, pa.get(
                "template", "assets/templates/player/nametag.png")),
            feet_offset_y=pa.get("feet_offset_y", 6),
            threshold=pa.get("match_threshold", 0.75))
        self.mondet = MonsterDetector(v["monster"])
        self.mondet.load_templates()
        self.y_band = cfg.get("combat", {}).get("attack_y_band") or [-30, 30]
        self.mon_every = float(v["monster"].get("detect_interval", 0.7))
        # UI 條的位置（基準尺度 px；正規化後不隨遊戲解析度改變）
        self.hp_scan = v.get("hp_scan") or [280, 770, 371, 30]
        self.exp_roi = v.get("exp_roi") or [511, 760, 130, 20]
        self.anchor_enabled = bool((v.get("player_anchor") or {}).get("enabled", False))
        self._anchor_fails = 0
        self._anchor_off_until = 0.0
        # UI 自動校準：遊戲 UI 不隨解析度等比縮放，硬寫座標必失效（實測
        # 2736→3840 全錯位）。每次啟動自動找 HP/MP 條、EXP 文字區、小地圖面板。
        from src.vision import BarReader
        self.hp_reader = BarReader("red")
        self.mp_reader = BarReader("blue")
        self.mm_rect = None
        self.exp_roi_auto = None
        self.calibrated = False
        self.calib_note = ""
        self._pos_miss = 0          # 連續讀不到玩家點的次數 → 觸發重新校準
        self._recalib_at = 0.0
        self.buffdet = None
        from src.vision import MotionDetector
        # 免模板後備：怪會動、背景不會（換地圖/換怪都能用）
        mo = v.get("motion") or {}
        self.motion = MotionDetector(
            diff_thresh=mo.get("diff_thresh", 25),
            dead_zone_frac=mo.get("dead_zone_frac", 0.07),
            min_blob=mo.get("min_blob", 40))
        self.motion_enabled = bool(mo.get("enabled", True))
        self._mon_t = 0.0
        self._mon = []
        self._prev_exp = None
        self._hp = {}
        self.exp_events = []

    # ---- HP 條 ----
    @staticmethod
    def _red(crop):
        b = crop[:, :, 0].astype(int)
        g = crop[:, :, 1].astype(int)
        r = crop[:, :, 2].astype(int)
        return (r > 150) & (r - g > 60) & (r - b > 60)

    def locate_hp(self, frame):
        sl, st, sw, sh = (int(x) for x in self.hp_scan)
        m = self._red(frame[st:st + sh, sl:sl + sw])
        best = (0, 0, 0)
        for y in range(m.shape[0]):
            n = st = 0
            for i, v in enumerate(m[y]):
                if v:
                    if n == 0:
                        st = i
                    n += 1
                    if n > best[0]:
                        best = (n, st, y)
                else:
                    n = 0
        L, s, y = best
        if L < 20:
            return False
        self._hp = {"roi": (st + y - 3, st + y + 4, sl + s - 2,
                            min(sl + sw, sl + s + int(L / 0.89) + 6)),
                    "full": int(L / 0.89)}
        return True

    def calibrate(self, raw):
        """在原始解析度畫面上自動定位 UI 元件。回傳是否全部找到。"""
        from src.vision import find_bars_pair, find_minimap_rect, exp_text_roi_from_bars
        hp, mp = find_bars_pair(raw)
        if hp:
            self.hp_reader.rect = hp
            self.hp_reader.full = max(self.hp_reader.full, hp["len"])
        if mp:
            self.mp_reader.rect = mp
            self.mp_reader.full = max(self.mp_reader.full, mp["len"])
        new_rect = find_minimap_rect(raw)
        if new_rect and self.mm_rect:
            # 面板尺寸應該穩定；差太多代表這次抓到的是別的深色視窗 → 保留舊的
            ow, oh = self.mm_rect[2], self.mm_rect[3]
            nw, nh = new_rect[2], new_rect[3]
            if abs(nw - ow) > ow * 0.4 or abs(nh - oh) > oh * 0.4:
                new_rect = self.mm_rect
        self.mm_rect = new_rect or self.mm_rect
        self.exp_roi_auto = exp_text_roi_from_bars(raw, hp, mp)
        if self.mm_rect:
            # 小地圖改用實測到的面板範圍（不再用固定 ROI + reference_size 換算）
            self.mm.roi = list(self.mm_rect)
            self.mm.reference_size = None
        ok = bool(hp and mp and self.mm_rect and self.exp_roi_auto)
        self.calib_note = (f"HP{'' if hp else '✗'} MP{'' if mp else '✗'} "
                           f"小地圖{self.mm_rect or '✗'} EXP{self.exp_roi_auto or '✗'}")
        self.calibrated = ok
        return ok

    def snapshot(self, now, cmd=""):
        """回傳 (WorldState, frame, info)。frame 可能為 None（抓不到畫面）。"""
        np = self._np
        import cv2
        try:
            raw = self.cap.grab()
        except Exception:
            raw = None
        frame = raw
        if raw is not None:
            self.raw_size = (raw.shape[1], raw.shape[0])
            # 世界（怪物模板）用正規化畫面；UI（血條/小地圖）用原始畫面——
            # UI 不隨解析度等比縮放，縮圖只會讓它更難認。
            if (raw.shape[1], raw.shape[0]) != self.canon:
                frame = cv2.resize(raw, self.canon, interpolation=cv2.INTER_AREA)
            # 首次校準；之後若連續讀不到玩家點（面板範圍可能抓歪或視窗移動）
            # 就重新校準——UI 位置會因開關視窗/移動視窗而改變。
            if not self.calibrated or (self._pos_miss >= 8 and now >= self._recalib_at):
                if self.calibrate(raw):
                    self._pos_miss = 0
                self._recalib_at = now + 15.0
        w = WorldState(now=now, cmd=cmd)
        info = {"anchor": None, "monsters": [], "anchor_score": 0.0,
                "same_layer": 0, "exp_per_hour": 0.0, "mp": None,
                "motion": None, "mon_hint": None,
                "raw_size": None, "calibrated": self.calibrated}
        info["raw_size"] = self.raw_size
        info["calibrated"] = self.calibrated
        if frame is None or float(frame.mean()) < 3:
            w.frame_ok = False
            return w, frame, info

        pos = self.tracker.update(self.mm.locate_player_candidates(raw))
        self._pos_miss = 0 if pos else self._pos_miss + 1
        w.pos = pos
        w.span = self.mm.platform_span(raw, pos) if pos else None
        w.hp = self.hp_reader.read(raw)
        w.mp = self.mp_reader.read(raw)
        info["mp"] = w.mp

        # EXP 變化（同時記錄時間戳供估算每小時進帳）
        er = self.exp_roi_auto or (0, 0, 1, 1)
        el, et, ew, eh = (int(x) for x in er)
        crop = raw[et:et + eh, el:el + ew].astype(np.int16)
        if self._prev_exp is not None:
            if float(np.abs(crop - self._prev_exp).mean()) > 1.5:
                w.exp_changed = True
                self.exp_events.append(now)
                if len(self.exp_events) > 4000:
                    del self.exp_events[:2000]
        self._prev_exp = crop
        info["exp_per_hour"] = exp_per_hour(self.exp_events, now)

        # 找怪（節流）
        if now - self._mon_t >= self.mon_every:
            self._mon_t = now
            try:
                self._mon = self.mondet.detect(frame)
            except Exception:
                self._mon = []
        ap = None
        if (self.anchor_enabled and self.anchor.available
                and now >= self._anchor_off_until):
            ap = self.anchor.find(frame)
            if ap is None:
                self._anchor_fails += 1
                if self._anchor_fails >= 3:
                    # 名牌比對失敗時會全幀重掃，實測吃掉 2.3 秒/圈——連續失敗
                    # 就先休息，避免拖垮整個迴圈（面向本來就以 EXP 回饋為主）
                    self._anchor_off_until = now + 60.0
                    self._anchor_fails = 0
            else:
                self._anchor_fails = 0
        info["anchor"], info["anchor_score"] = ap, self.anchor.last_score
        h, wpx = frame.shape[:2]
        axm, aym = ap if ap else (wpx // 2, h // 2)
        lo, hi = self.y_band
        left = right = 0
        for d in self._mon:
            same = lo <= (d.y + d.h) - aym <= hi
            info["monsters"].append((d.x, d.y, d.w, d.h, d.score, same))
            if same:
                if d.center[0] < axm:
                    left += 1
                else:
                    right += 1
        w.mon_left, w.mon_right = left, right
        info["same_layer"] = left + right
        if self.motion_enabled:
            md = self.motion.update(frame)
            info["motion"] = md
            if left + right == 0:          # 模板沒抓到 → 用活動偵測當後備
                w.mon_hint = self.motion.hint_side()
            info["mon_hint"] = w.mon_hint
        return w, frame, info

    def find_buff(self, frame):
        from src.vision.monster import MonsterDetector
        if frame is None:
            return None
        try:
            if self.buffdet is None:
                h, wpx = frame.shape[:2]
                bd = (self.cfg["vision"].get("buff_dispel") or {})
                self.buffdet = MonsterDetector({
                    "template_dir": bd.get("template_dir", "assets/templates/buffs"),
                    "match_threshold": bd.get("match_threshold", 0.80),
                    "roi": [int(wpx * 0.55), 0, wpx - int(wpx * 0.55), int(h * 0.25)],
                    "nms_iou": 0.3})
                self.buffdet.load_templates()
            dets = self.buffdet.detect(frame)
            if dets:
                win = self.cap.locate_window()
                ox, oy = (win.left, win.top) if win else (0, 0)
                return (ox + dets[0].center[0], oy + dets[0].center[1])
        except Exception:
            pass
        return None

    def close(self):
        try:
            self.cap.close()
        except Exception:
            pass


# ============================================================
#  執行殼：感知 → core.tick → 送鍵（薄層，安全規則全在核心）
# ============================================================

def run_daemon(cmd_path, status_path, log_path, max_seconds=7.5 * 3600):
    import ctypes
    import json
    import os
    import sys
    import time

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    import numpy as np
    import yaml
    from src.capture import ScreenCapture
    from src.vision import MinimapLocator, PlayerTracker, PlayerAnchor
    from src.vision.monster import MonsterDetector
    from tools.hold_and_wiggle import (_send_key, _find_window, _foreground,
                                       _PhysicalKeys, _right_click_at, _pressed, _VK)

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _user32.ShowWindow(_kernel32.GetConsoleWindow(), 6)
    time.sleep(0.3)

    logf = open(log_path, "a", encoding="utf-8")

    def log(msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        logf.write(line + "\n")
        logf.flush()
        print(line, flush=True)

    with open(os.path.join(ROOT, "config", "settings.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cap = ScreenCapture(backend="mss", window_title=cfg["window"]["title"])
    mmloc = MinimapLocator(cfg["vision"]["minimap"])
    tracker = PlayerTracker()
    # 怪物偵測（模板匹配）——找怪是本工具的核心任務
    mon_cfg = cfg["vision"]["monster"]
    mondet = MonsterDetector(mon_cfg)
    mondet.load_templates()
    y_band = cfg.get("combat", {}).get("attack_y_band") or [-60, 60]
    mon_every = float(mon_cfg.get("detect_interval", 0.7))
    mon_state = {"t": 0.0, "left": 0, "right": 0}
    # 角色螢幕定位：鏡頭夾邊時角色不在畫面中央（實測偏移 56×182px），
    # 用名牌模板定位才能正確判斷「怪在左邊還右邊 / 是不是同一層」。
    pa_cfg = cfg.get("vision", {}).get("player_anchor") or {}
    anchor = PlayerAnchor(
        template_path=os.path.join(ROOT, pa_cfg.get(
            "template", "assets/templates/player/nametag.png")),
        feet_offset_y=pa_cfg.get("feet_offset_y", 6),
        threshold=pa_cfg.get("match_threshold", 0.75))
    log("角色定位：" + ("名牌模板可用" if anchor.available else "找不到名牌模板 → 退回畫面中央"))
    hwnd = _find_window(cfg["window"]["title"])
    if not hwnd:
        log("找不到遊戲視窗，結束")
        return 1
    _foreground(hwnd)
    phys = _PhysicalKeys((0x25, 0x26, 0x27, 0x28))
    buffdet = None
    hp_band = {}
    exp_roi = (1520, 1556, 1020, 1280)
    prev_exp = [None]

    def red_mask(crop):
        b = crop[:, :, 0].astype(int)
        g = crop[:, :, 1].astype(int)
        r = crop[:, :, 2].astype(int)
        return (r > 150) & (r - g > 60) & (r - b > 60)

    def locate_hp(frame):
        m = red_mask(frame[1540:1596, 560:1300])
        best = (0, 0, 0, 0)
        for y in range(m.shape[0]):
            n = st = 0
            for i, v in enumerate(m[y]):
                if v:
                    if n == 0:
                        st = i
                    n += 1
                    if n > best[0]:
                        best = (n, st, i, y)
                else:
                    n = 0
        L, s, _e, y = best
        if L < 40:
            return False
        hp_band["roi"] = (1540 + y - 4, 1540 + y + 5, 560 + s - 2,
                          min(1300, 560 + s + int(L / 0.89) + 10))
        hp_band["full"] = int(L / 0.89)
        log(f"HP 條 y≈{1540 + y}，滿血寬估 {hp_band['full']}")
        return True

    def sense(now):
        try:
            frame = cap.grab()
        except Exception:
            frame = None
        w = WorldState(now=now)
        w.window = bool(hwnd and _user32.IsWindow(hwnd))
        w.fg = bool(w.window and _user32.GetForegroundWindow() == hwnd)
        if frame is None or float(frame.mean()) < 3:
            w.frame_ok = False
            return w, None
        pos = tracker.update(mmloc.locate_player_candidates(frame))
        w.pos = pos
        w.span = mmloc.platform_span(frame, pos) if pos else None
        if hp_band:
            y1, y2, x1, x2 = hp_band["roi"]
            wred = red_mask(frame[y1:y2, x1:x2]).any(axis=0).sum()
            w.hp = float(wred) / max(1, hp_band["full"])
        y1, y2, x1, x2 = exp_roi
        cur = frame[y1:y2, x1:x2].astype(np.int16)
        if prev_exp[0] is not None:
            w.exp_changed = float(np.abs(cur - prev_exp[0]).mean()) > 1.5
        prev_exp[0] = cur
        # 找怪：以「角色真實螢幕位置」為原點，數左右兩側同一層的怪
        if now - mon_state["t"] >= mon_every:
            mon_state["t"] = now
            try:
                h, wpx = frame.shape[:2]
                ap = anchor.find(frame) if anchor.available else None
                axm, aym = ap if ap else (wpx // 2, h // 2)
                lo, hi = y_band
                left = right = 0
                for d in mondet.detect(frame):
                    # 用「怪的底部」對齊「角色腳底」判斷是否同一層
                    if not (lo <= (d.y + d.h) - aym <= hi):
                        continue          # 別層的怪，箭是水平飛的射不到
                    if d.center[0] < axm:
                        left += 1
                    else:
                        right += 1
                mon_state["left"], mon_state["right"] = left, right
            except Exception:
                pass
        w.mon_left, w.mon_right = mon_state["left"], mon_state["right"]
        w.user_touch = phys.ok and any(
            phys.state.get(v, False) for v in (0x25, 0x26, 0x27, 0x28))
        try:
            with open(cmd_path, encoding="utf-8") as f:
                w.cmd = f.read().strip().lower()
        except Exception:
            w.cmd = ""
        return w, frame

    def detect_buff(frame):
        nonlocal buffdet
        if frame is None:
            return None
        try:
            if buffdet is None:
                h, wpx = frame.shape[:2]
                buffdet = MonsterDetector({
                    "template_dir": os.path.join(ROOT, "assets", "templates", "buffs"),
                    "match_threshold": 0.80,
                    "roi": [int(wpx * 0.55), 0, wpx - int(wpx * 0.55), int(h * 0.25)],
                    "nms_iou": 0.3})
                buffdet.load_templates()
            dets = buffdet.detect(frame)
            if dets:
                win = cap.locate_window()
                ox, oy = (win.left, win.top) if win else (0, 0)
                return (ox + dets[0].center[0], oy + dets[0].center[1])
        except Exception:
            pass
        return None

    def tap_key(key, hold):
        _send_key(key, keyup=False)
        try:
            time.sleep(hold)
        finally:
            _send_key(key, keyup=True)

    def do(a):
        if a.verb == "hold_attack":
            _send_key("ctrl", keyup=False)
        elif a.verb == "repress_attack":
            _send_key("ctrl", keyup=True)
            time.sleep(0.03)
            _send_key("ctrl", keyup=False)
        elif a.verb == "release_attack":
            _send_key("ctrl", keyup=True)
        elif a.verb == "tap":
            tap_key(a.arg, 0.05)
        elif a.verb == "tap_jump":
            tap_key("alt", 0.05)
        elif a.verb == "step":
            tap_key(a.arg[0], a.arg[1])
        elif a.verb == "turn":
            tap_key(a.arg, 0.03)      # 極短按：只轉身不走路
        elif a.verb == "release_moves":
            for k in ("left", "right"):
                _send_key(k, keyup=True)
        elif a.verb == "release_all":
            for k in ("left", "right", "up", "down", "ctrl", "alt"):
                _send_key(k, keyup=True)
        elif a.verb == "right_click":
            _right_click_at(a.arg[0], a.arg[1])
        elif a.verb == "log":
            log(str(a.arg))

    heal_mode = cfg.get("survival", {}).get("heal_mode", "self")
    profile = ClassProfile.from_config(cfg.get("class_profile"))
    log(f"職業設定：{profile.name}｜攻擊 {profile.attack_mode}"
        f"｜buff {len(profile.buffs)} 顆｜MP {'有' if profile.mp_key else '無'}")
    core = NightWatchCore(max_seconds=max_seconds, profile=profile,
                          potion_key=cfg["keys"].get("hp_potion", "delete"),
                          heal_mode=heal_mode,
                          last_resort_hp=float(cfg.get("survival", {})
                                               .get("last_resort_hp", 0.20)))
    log(f"補血模式：{heal_mode}"
        + ("（寵物/使用者負責，核心不碰藥水鍵）" if heal_mode == "external" else ""))
    f0 = cap.grab()
    if f0 is None or not locate_hp(f0):
        log("HP 條定位失敗（core 將因 hp=None 進入安全模式）")
    log(f"守夜 v2 開始（上限 {max_seconds/3600:.1f}h）")
    try:
        while core.state != "STOPPED":
            if _pressed(_VK["f12"]):
                log("F12 → 結束")
                break
            now = time.time()
            w, frame = sense(now)
            if core.state == "FARM":
                w.buff_hit = detect_buff(frame)
            for a in core.tick(w):
                do(a)
            try:
                with open(status_path, "w", encoding="utf-8") as f:
                    json.dump({"state": core.state, "t": time.strftime("%H:%M:%S"),
                               "hp": None if w.hp is None else round(w.hp, 2),
                               "pos": w.pos, "exp_count": core.stats.exp_count,
                               "potion_works": core.potion_works,
                               "stats": vars(core.stats)}, f, ensure_ascii=False)
            except Exception:
                pass
            time.sleep(0.35)
    except Exception as e:
        log(f"例外：{type(e).__name__}: {e}")
    finally:
        for k in ("left", "right", "up", "down", "ctrl", "alt"):
            try:
                _send_key(k, keyup=True)
            except Exception:
                pass
        cap.close()
        log(f"守夜結束。EXP {core.stats.exp_count} 次、藥 {core.stats.potions} 次、"
            f"推回 {core.stats.guard_pushes} 次、自動恢復 {core.stats.recoveries} 次")
        logf.close()
    return 0


if __name__ == "__main__":
    import argparse
    import os
    import sys
    p = argparse.ArgumentParser(description="守夜練等 daemon（需管理員）")
    p.add_argument("--run", action="store_true", help="實際啟動（避免誤觸，必須加此旗標）")
    p.add_argument("--dir", default=None, help="cmd/status/log 檔所在資料夾")
    p.add_argument("--hours", type=float, default=7.5)
    a = p.parse_args()
    if not a.run:
        p.print_help()
        sys.exit(0)
    d = a.dir or os.path.dirname(os.path.abspath(__file__))
    sys.exit(run_daemon(os.path.join(d, "daemon_cmd.txt"),
                        os.path.join(d, "daemon_status.json"),
                        os.path.join(d, "daemon_log.txt"),
                        max_seconds=a.hours * 3600))
