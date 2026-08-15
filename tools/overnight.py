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
    step / release_moves / release_all / right_click / log

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

import random
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
    exp_changed: bool = False
    buff_hit: Optional[Tuple[int, int]] = None
    cmd: str = ""


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
    IDLE_RECOVER_AFTER = 180.0    # IDLE_SAFE 待滿此秒數且條件乾淨 → 自動再試
    IDLE_CLEAN_NEED = 30.0        # 「乾淨」需持續的秒數
    IDLE_RECOVER_MAX = 4          # 一晚自動恢復次數上限
    IDLE_HP_DROP_SILENT = 0.12    # IDLE_SAFE 期間掉血超過此幅度 → 轉入靜默

    # 快捷欄全鍵位：不知道藥水放哪一格就自己一個一個試（看 HP 有沒有回升），
    # 不必問使用者。第一個是設定檔給的鍵，其餘依序輪替。
    POTION_CANDIDATES = ("delete", "insert", "home", "pageup", "end",
                         "pagedown", "shift")

    def __init__(self, max_seconds=7.5 * 3600, potion_key="delete", rng=None,
                 potion_candidates=None):
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

        # 補血（含驗證與絕望補血）
        self._potion_logic(w, acts)

        # 低血保險：hp=None 凍結（不重置）計時
        if w.hp is not None:
            if w.hp < self.HP_ABORT:
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

        # 攻擊保持
        if not self._atk_held:
            acts.append(Action("hold_attack"))
            self._atk_held = True
            self._atk_refresh = w.now + self.rng.uniform(*self.ATTACK_REPRESS)
        elif w.now >= self._atk_refresh:
            acts.append(Action("repress_attack"))
            self._atk_refresh = w.now + self.rng.uniform(*self.ATTACK_REPRESS)

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
    from src.vision import MinimapLocator, PlayerTracker
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

    core = NightWatchCore(max_seconds=max_seconds,
                          potion_key=cfg["keys"].get("hp_potion", "delete"))
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
