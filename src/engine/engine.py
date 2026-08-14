"""主迴圈引擎。

每一圈：擷取畫面 → 感知（小地圖玩家、HP/MP、鱷魚）→ 決策/行動：
    1) 生存維持（低血/藍就喝水）
    2) rune 偵測
    3) 打怪優先：偵測到鱷魚 → 面向怪較多的一側 → 弓箭手遠程放技能連射（射程外則走近）
    4) 沒有怪 → 沿平台左右巡邏，到邊界前折返（避免走進水裡）

dry-run 會把整條「感知 → 決策」印出來但不送實體鍵；可用 --demo-image 餵真實截圖。
核心 CV 已實作，實際數值（ROI/HSV/鍵位/巡邏邊界）由 settings 設定並需在遊戲內校準。
"""
from __future__ import annotations

import random
import time
from typing import List, Optional

from src.capture import ScreenCapture
from src.vision import (TemplateMatcher, MinimapLocator, PlayerTracker,
                        HealthBarDetector, MonsterDetector, probe_ahead_safe)
from src.input import InputController
from src.routine import Routine, load_routine
from src.commands import (CommandBook, CommandContext, PlatformState,
                          plan_ranged, decision_to_actions, plan_patrol,
                          approach_is_safe, filter_attackable, on_platform,
                          plan_two_platforms)
from src.rune import RuneDetector, RuneSolver


class BotEngine:
    """自動化主引擎。

    參數：
        config: 已載入的設定 dict（見 config/settings.example.yaml）。
        dry_run: True 時輸入層只記錄不實際送鍵、擷取層改用合成/指定畫面。
        demo_image: dry_run 時可指定一張截圖當畫面來源（在真畫面上偵測鱷魚、讀 HP/MP）。
    """

    def __init__(self, config, dry_run=False, demo_image=None):
        self.config = config or {}
        self.dry_run = dry_run
        self.demo_image = demo_image
        self.running = False

        # 各層元件（於 setup() 建立）
        self.capture: Optional[ScreenCapture] = None
        self.matcher: Optional[TemplateMatcher] = None
        self.minimap: Optional[MinimapLocator] = None
        self.health: Optional[HealthBarDetector] = None
        self.monster: Optional[MonsterDetector] = None
        self.controller: Optional[InputController] = None
        self.routine: Optional[Routine] = None
        self.command_book: Optional[CommandBook] = None
        self.rune_detector: Optional[RuneDetector] = None
        self.rune_solver: Optional[RuneSolver] = None

        # 打怪 / 巡邏參數（於 setup() 從 config 讀入）
        self._attack_key = "ctrl"
        self._attack_range = 700
        self._combo = 3
        self._patrol_left = 40
        self._patrol_right = 175
        self._patrol_step = 0.35
        self._patrol_dir = "right"
        self._edge_margin = 2
        self._tracker = PlayerTracker()

        # 兩平台輪流清怪（combat.platforms 有設定時啟用）
        self._platforms = []
        self._switch_cfg = {}
        self._attack_y_band = None
        self._plat_state = PlatformState()

        self._last_potion_ts = 0.0
        self._last_player = None
        self._last_monsters: List = []
        self._demo_source = ""
        # 跨指令共享狀態（例如目前面向），讓「同方向連續攻擊」不必每圈重按轉向鍵
        self._cmd_state = {}
        # 防掉落：被邊界擋下的追怪方向（黏性，避免「走近→被擋→巡邏→再走近」乒乓）
        self._blocked_side = None
        self._combat_blocked = False  # 本圈打怪是否因邊界被擋（供換平台計數用）
        # 靜止看門狗：送了移動鍵但小地圖座標多圈完全不動 → 可能鎖到靜止的
        # 黃色標記（誤判點永遠距離 0、永遠贏），重新定位玩家點。
        self._moved_last_loop = False
        self._stall_loops = 0
        self._stall_limit = 8
        # 防攻擊失效：定點站著打 ~60 秒攻擊會失效，需定時小步移動＋換邊打
        self._repo_min = 45.0
        self._repo_max = 65.0
        self._repo_step = 0.2
        self._repo_swap = True
        self._next_reposition_ts = 0.0
        self._repo_alt = True  # 讀不到位置時左右極小交替用
        # 防掉落第二道防線：以畫面上角色前方的地面顏色判斷是否還在平台上
        #（小地圖解析度太低，貼邊時不夠精細）
        self._last_frame = None
        self._edge_probe_cfg = {}
        self._edge_probe_enabled = True
        # 不想要的 buff（如別人丟的「速度激發」——移速變快步伐全會走過頭）
        # → 偵測到就右鍵點掉
        self.buff_detector: Optional[MonsterDetector] = None
        self._buff_enabled = True
        self._buff_interval = 5.0
        self._buff_roi_given = False
        self._next_buff_check = 0.0

    # ---- 初始化 ----
    def setup(self):
        """依設定建立各層元件（不啟動遊戲，只準備物件）。"""
        cfg = self.config
        win = cfg.get("window", {})
        vision_cfg = cfg.get("vision", {})
        combat = cfg.get("combat", {})
        keys = cfg.get("keys", {})

        self.capture = ScreenCapture(
            backend=cfg.get("capture", {}).get("backend", "mss"),
            window_title=win.get("title"),
            region=win.get("region"),
            dry_run=self.dry_run,
        )
        self.matcher = TemplateMatcher(threshold=vision_cfg.get("template_match_threshold", 0.8))
        self.minimap = MinimapLocator(vision_cfg.get("minimap"))
        self.health = HealthBarDetector(vision_cfg.get("health_bar"))
        self.monster = MonsterDetector(vision_cfg.get("monster"))
        self.controller = InputController(cfg.get("humanize"), dry_run=self.dry_run)
        self.command_book = CommandBook()
        self.rune_detector = RuneDetector(cfg.get("rune"))
        self.rune_solver = RuneSolver()

        # 打怪 / 巡邏參數
        self._attack_key = combat.get("attack_key") or keys.get("attack", "ctrl")
        self._attack_range = int(combat.get("attack_range_px", 700))
        self._combo = int(combat.get("combo", 3))
        self._patrol_left = int(combat.get("patrol_left_x", 40))
        self._patrol_right = int(combat.get("patrol_right_x", 175))
        self._patrol_step = float(combat.get("patrol_step_seconds", 0.35))
        # 防掉落：折返點往內縮的安全邊界（小地圖 px）。感知→送鍵有延遲，
        # 貼著邊界才折返會走過頭掉下去。
        self._edge_margin = int(combat.get("patrol_edge_margin", 2))
        mm_cfg = vision_cfg.get("minimap") or {}
        # 防掉落：小地圖玩家點跨圈追蹤，過濾「座標亂跳」誤判（亂讀一次就會走錯方向）。
        self._tracker = PlayerTracker(
            max_jump_px=int(mm_cfg.get("max_jump_px", 20)),
            reacquire_misses=int(mm_cfg.get("reacquire_misses", 10)),
        )
        self._stall_limit = int(mm_cfg.get("stall_reset_loops", 8))
        # 防攻擊失效：間隔隨機（預設 45~65 秒，趕在 ~60 秒失效前動一下）。
        # reposition_min_seconds 設 0 可整個關閉。
        self._repo_min = float(combat.get("reposition_min_seconds", 45))
        self._repo_max = max(float(combat.get("reposition_max_seconds", 65)), self._repo_min)
        # 按住須明顯超過 0.1 秒才會真的走路（≲0.1 只轉身），太小等於沒動
        self._repo_step = float(combat.get("reposition_step_seconds", 0.2))
        self._repo_swap = bool(combat.get("reposition_swap_facing", True))
        # 邊緣探測（畫面防線）與 buff 自動點掉
        ep = vision_cfg.get("edge_probe") or {}
        self._edge_probe_enabled = bool(ep.get("enabled", True))
        self._edge_probe_cfg = ep
        bd = vision_cfg.get("buff_dispel") or {}
        self._buff_enabled = bool(bd.get("enabled", True))
        self._buff_interval = float(bd.get("check_interval", 5.0))
        self._buff_roi_given = bd.get("roi") is not None
        # MonsterDetector 是通用的多模板偵測器，直接拿來找 buff 圖示
        self.buff_detector = MonsterDetector({
            "template_dir": bd.get("template_dir", "assets/templates/buffs"),
            "match_threshold": bd.get("match_threshold", 0.80),
            "roi": bd.get("roi"),
            "nms_iou": 0.3,
        })
        self.buff_detector.load_templates()  # 資料夾不存在／缺 cv2 → 保持空
        self._platforms = list(combat.get("platforms") or [])
        self._switch_cfg = combat.get("platform_switch") or {}
        self._attack_y_band = combat.get("attack_y_band")

        routine_path = cfg.get("routine", {}).get("path")
        if routine_path:
            try:
                self.routine = load_routine(routine_path)
            except FileNotFoundError:
                print(f"[警告] 找不到路線檔：{routine_path}，將以空路線啟動。")

        if self.dry_run:
            self._setup_dry_run_scene(vision_cfg)
        else:
            self.monster.load_templates()  # 從 template_dir 載入你的鱷魚截圖
        return self

    def _setup_dry_run_scene(self, vision_cfg):
        """dry-run 畫面來源：優先用 --demo-image 的真實截圖，否則用合成場景。"""
        frame = None
        if self.demo_image:
            try:
                import cv2  # 延遲載入
                import numpy as np
                # 不用 cv2.imread：Windows 上遇到中文路徑（如 擷取.PNG）會靜默回傳 None
                frame = cv2.imdecode(np.fromfile(self.demo_image, dtype=np.uint8),
                                     cv2.IMREAD_COLOR)
            except Exception:
                frame = None

        if frame is not None:
            self.capture.scene_provider = (lambda f=frame: f)
            self.monster.load_templates()  # 真實鱷魚模板
            self._demo_source = f"真實截圖：{self.demo_image}"
        else:
            if self.demo_image:
                # 別靜默吞掉：使用者以為在對真實截圖校準，實際卻是合成場景
                print(f"[警告] --demo-image 讀取失敗：{self.demo_image} → 改用合成場景")
            from src.vision.synthetic import build_demo
            demo = build_demo(vision_cfg)
            why = ("--demo-image 讀取失敗" if self.demo_image else "未指定 --demo-image")
            if demo is not None:
                self.capture.scene_provider = (lambda d=demo: d.frame)
                for name, img in demo.monster_templates.items():
                    self.monster.add_template(name, img)
                self._demo_source = f"合成場景（{why}）"
            else:
                self._demo_source = "全黑畫面（缺 cv2/numpy）"

    # ---- 主迴圈 ----
    def start(self, max_loops=None):
        """啟動主迴圈，直到 stop()、達到 max_loops、或 KeyboardInterrupt。"""
        if self.capture is None:
            self.setup()
        self.running = True
        if self.dry_run and self._demo_source:
            print(f"[資訊] dry-run 畫面來源：{self._demo_source}")
        fps = self.config.get("capture", {}).get("fps_limit", 12)
        frame_interval = 1.0 / max(1, fps)

        loops = 0
        try:
            while self.running:
                t0 = time.time()
                frame = self.capture.grab()

                # 感知（玩家點經 PlayerTracker 過濾：亂跳的誤判座標寧可當作沒讀到）
                player_mm = self._tracker.update(
                    self.minimap.locate_player_candidates(frame))
                # 靜止看門狗：上一圈有送移動鍵、座標卻完全沒動——連續多圈就當作
                # 追蹤點鎖錯（靜止的黃色標記）或角色卡住，重新定位玩家點。
                if (self._moved_last_loop and player_mm is not None
                        and player_mm == self._last_player):
                    self._stall_loops += 1
                    if self._stall_loops >= self._stall_limit:
                        self._tracker.reset()
                        self._stall_loops = 0
                        if self.dry_run:
                            print("        ↳ 有移動但小地圖座標多圈未變 → 重新定位玩家點")
                elif player_mm is not None:
                    self._stall_loops = 0
                self._moved_last_loop = False
                self._last_frame = frame
                hp = self.health.read_hp_ratio(frame)
                mp = self.health.read_mp_ratio(frame)
                monsters = self.monster.detect(frame)
                player_screen = self._player_screen(frame)
                self._last_player, self._last_monsters = player_mm, monsters

                # 同高度帶過濾：箭是水平飛的，別的平台的怪看得到但射不到
                attackable = filter_attackable(player_screen[1], monsters, self._attack_y_band)

                if self.dry_run:
                    title = self.config.get("window", {}).get("title", "")
                    pstr = f"{player_mm}" if player_mm else "偵測不到"
                    extra = (f"（同高度帶 {len(attackable)}）"
                             if self._attack_y_band and len(attackable) != len(monsters) else "")
                    print(f"[loop {loops + 1}] 視窗:「{title}」 | 玩家(小地圖):{pstr}  "
                          f"HP:{hp:.0%}  MP:{mp:.0%}  鱷魚:{len(monsters)}{extra}")

                # 決策 / 行動
                self._maintain_survival(frame, hp, mp)
                self._handle_rune(frame)
                self._dispel_buffs(frame)
                self._combat_blocked = False
                acted = self._combat(player_screen, attackable, player_mm)
                if self._platforms:
                    # 被邊界擋下的怪視同「打不到」：不然牠會一直把 empty 計數
                    # 歸零，換平台永遠不觸發，角色就卡在邊界旁乾等。
                    n_att = 0 if self._combat_blocked else len(attackable)
                    self._plat_state, plan = plan_two_platforms(
                        player_mm, n_att, self._plat_state,
                        self._platforms, self._switch_cfg)
                    if not acted:
                        self._execute_platform_plan(plan)
                elif not acted:
                    self._patrol(player_mm)

                loops += 1
                if max_loops is not None and loops >= max_loops:
                    self.running = False
                    break

                elapsed = time.time() - t0
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        """停止主迴圈並釋放資源。"""
        self.running = False
        if self.capture is not None:
            self.capture.close()

    # ---- 感知輔助 ----
    def _player_screen(self, frame):
        """角色在畫面上的座標：優先用 combat.player_screen_anchor，否則取畫面中央。"""
        a = self.config.get("combat", {}).get("player_screen_anchor")
        if a:
            return (int(a[0]), int(a[1]))
        if frame is not None and hasattr(frame, "shape"):
            h, w = frame.shape[:2]
            return (w // 2, h // 2)
        return (0, 0)

    def _make_ctx(self, frame):
        return CommandContext(
            controller=self.controller,
            vision={"matcher": self.matcher, "minimap": self.minimap,
                    "health": self.health, "monster": self.monster, "frame": frame},
            config=self.config,
            state=self._cmd_state,  # 跨圈共享（面向等），攻擊才不必每圈重按轉向鍵
        )

    # ---- 各階段 ----
    def _maintain_survival(self, frame, hp=None, mp=None):
        """依 HP/MP 比例補水（含冷卻時間）。hp/mp 可由呼叫端預先算好傳入。"""
        surv = self.config.get("survival", {})
        keys = self.config.get("keys", {})
        if hp is None:
            hp = self.health.read_hp_ratio(frame)
        if mp is None:
            mp = self.health.read_mp_ratio(frame)
        now = time.time()
        if now - self._last_potion_ts < surv.get("potion_cooldown", 1.0):
            return
        hp_thr = surv.get("hp_threshold", 0.5)
        mp_thr = surv.get("mp_threshold", 0.3)
        if hp < hp_thr:
            if self.dry_run:
                print(f"        ↳ 決策：HP {hp:.0%} < {hp_thr:.0%} → 喝血({keys.get('hp_potion', 'delete')})")
            self.controller.tap(keys.get("hp_potion", "delete"))
            self._last_potion_ts = now
        elif mp < mp_thr:
            if self.dry_run:
                print(f"        ↳ 決策：MP {mp:.0%} < {mp_thr:.0%} → 喝魔({keys.get('mp_potion', 'end')})")
            self.controller.tap(keys.get("mp_potion", "end"))
            self._last_potion_ts = now

    def _handle_rune(self, frame):
        """偵測並嘗試解 rune。"""
        if not self.config.get("rune", {}).get("enabled", True):
            return
        if self.rune_detector.detect(frame):
            self.rune_solver.solve(frame, self.controller)

    def _dispel_buffs(self, frame):
        """偵測「不想要的 buff」圖示並右鍵點掉。

        例：別人丟的「速度激發」——移速變快後，校準好的步伐全部走過頭，
        一動就掉出平台，必須在右上角 buff 列用滑鼠右鍵移除。
        要點掉哪些 buff 由使用者把圖示截圖放進 template_dir 決定。
        """
        if not self._buff_enabled or self.buff_detector is None or frame is None:
            return
        if not self.buff_detector.template_names:
            return  # 沒放任何 buff 圖示模板 → 功能靜默停用
        now = time.time()
        if now < self._next_buff_check:
            return
        self._next_buff_check = now + self._buff_interval
        if not self._buff_roi_given and hasattr(frame, "shape"):
            # 未指定 ROI → 用畫面尺寸推算右上角 buff 列區域（右 45%、上 25%）
            h, w = frame.shape[:2]
            self.buff_detector.roi = [int(w * 0.55), 0, w - int(w * 0.55), int(h * 0.25)]
            self._buff_roi_given = True
        dets = self.buff_detector.detect(frame)
        if not dets:
            return
        x, y = dets[0].center
        off_l = off_t = 0
        if not self.dry_run:
            region = self.capture.locate_window()  # frame 是視窗相對座標 → 換算螢幕座標
            if region is not None:
                off_l, off_t = region.left, region.top
        if self.dry_run:
            print(f"        ↳ 偵測到要點掉的 buff「{dets[0].name}」({x},{y}) → 右鍵移除")
        self.controller.right_click(off_l + x, off_t + y)

    def _combat(self, player_screen, monsters, player_mm=None):
        """打怪：弓箭手遠程定點。有怪回傳 True（已處理），無怪回傳 False。

        防掉落：怪在射程外需要走近時，先確認往那個方向走仍在平台／巡邏
        邊界內——怪可能站在水裡或平台外側，不設限就會一路追出平台。
        被擋下的方向會記住（_blocked_side），之後同側射程外的怪直接忽略，
        避免「走近→到邊界被擋→巡邏走回→又走近」的乒乓；有怪進射程或
        完全沒怪時解除。被擋的圈會標記 _combat_blocked，讓換平台計數把
        這隻怪當作「打不到」。
        """
        if not monsters:
            self._blocked_side = None
            return False
        decision = plan_ranged(player_screen, monsters, self._attack_range)
        if decision is None:
            return False
        if decision.in_range:
            self._blocked_side = None
            # 防攻擊失效：定點站著打 ~60 秒會失效 → 到時間就小步移動一下，
            # 並讓這一輪換邊打（下一圈會自動轉回怪較多的那側）。
            now = time.time()
            if self._repo_min > 0:
                if self._next_reposition_ts == 0.0:
                    self._next_reposition_ts = now + random.uniform(self._repo_min,
                                                                    self._repo_max)
                elif now >= self._next_reposition_ts:
                    self._do_reposition(player_mm)
                    if self._repo_swap:
                        decision.facing = ("left" if decision.facing == "right"
                                           else "right")
                        if self.dry_run:
                            print("        ↳ 防攻擊失效：這一輪換邊打")
                    self._next_reposition_ts = now + random.uniform(self._repo_min,
                                                                    self._repo_max)
        else:
            if decision.facing == self._blocked_side:
                self._combat_blocked = True
                if self.dry_run:
                    print(f"        ↳ 打怪：{decision.facing}側邊界外的怪持續忽略"
                          "（防乒乓）→ 交給巡邏/換平台")
                return False
            if not self._approach_allowed(player_mm, decision.facing):
                self._blocked_side = decision.facing
                self._combat_blocked = True
                if self.dry_run:
                    print(f"        ↳ 打怪：最近的怪在巡邏邊界外（往{decision.facing}）"
                          "→ 防掉落，不追")
                return False
        if self.dry_run:
            tx, ty = decision.target.center
            verb = (f"放技能 {self._attack_key}×{self._combo}" if decision.in_range
                    else f"走近({decision.facing})")
            print(f"        ↳ 打怪：偵測到 {decision.count} 隻 → 最近({tx},{ty}) "
                  f"dx={decision.distance} → 面向{decision.facing} → {verb}")
        ctx = self._make_ctx(None)
        for action in decision_to_actions(decision, self._combo):
            self.command_book.create(action["action"], action.get("args", {})).execute(ctx)
        if not decision.in_range:
            self._moved_last_loop = True  # 走近了一步（靜止看門狗用）
        return True

    def _patrol(self, player_mm, left=None, right=None, label=""):
        """沒有怪時沿平台左右巡邏；到折返點前提早換方向（避免走進水裡）。

        left/right 可由兩平台邏輯帶入該平台的邊界；未帶則用全域設定。
        防掉落：小地圖讀不到玩家時原地等待——沿上次方向盲走，
        連續幾圈就會走出平台。
        """
        left = self._patrol_left if left is None else left
        right = self._patrol_right if right is None else right
        px = player_mm[0] if player_mm is not None else None
        d, at_edge = plan_patrol(px, self._patrol_dir, left, right, self._edge_margin)
        if d is None:
            if self.dry_run:
                print("        ↳ 無怪 → 小地圖讀不到玩家，原地等待（不盲走以免掉出平台）")
            return
        # 第二道防線：畫面上角色前方若不是同一種地面（水面/懸空）就先折返；
        # 兩邊都不安全就原地等待。小地圖解析度低，貼邊時以畫面為準。
        if not self._ahead_is_safe(d):
            opp = "left" if d == "right" else "right"
            if not self._ahead_is_safe(opp):
                if self.dry_run:
                    print("        ↳ 無怪 → 畫面偵測兩側地面都可疑，原地等待")
                return
            d, at_edge = opp, True
            if self.dry_run:
                print(f"        ↳ 畫面偵測前方不是平台地面 → 改往{d}")
        self._patrol_dir = d
        if self.dry_run:
            edge = "（近邊界，折返）" if at_edge else ""
            where = f"[{label}] " if label else ""
            print(f"        ↳ 無怪 → {where}沿平台巡邏往{d}{edge}")
        ctx = self._make_ctx(None)
        self.command_book.create("approach", {"dir": d, "seconds": self._patrol_step}).execute(ctx)
        self._moved_last_loop = True

    def _do_reposition(self, player_mm):
        """防攻擊失效的小步移動（不能走遠）。

        方向優先「往平台中心」——絕不往邊界外挪；讀不到位置／不知邊界時
        改用左右極小交替（兩次相抵、不會愈走愈遠）。
        """
        bounds = self._bounds_at(player_mm) if player_mm is not None else None
        if player_mm is not None and bounds is not None:
            center = (bounds[0] + bounds[1]) / 2
            d = "right" if player_mm[0] < center else "left"
        else:
            d = "right" if self._repo_alt else "left"
            self._repo_alt = not self._repo_alt
        if not self._ahead_is_safe(d):
            opp = "left" if d == "right" else "right"
            if not self._ahead_is_safe(opp):
                if self.dry_run:
                    print("        ↳ 防攻擊失效：兩側地面都可疑 → 這次不挪步")
                return
            d = opp
        if self.dry_run:
            print(f"        ↳ 防攻擊失效：小步移動往{d}（{self._repo_step:.2f}s，"
                  "定點久站攻擊會失效）")
        ctx = self._make_ctx(None)
        self.command_book.create("approach",
                                 {"dir": d, "seconds": self._repo_step}).execute(ctx)
        self._moved_last_loop = True

    # ---- 防掉落輔助 ----
    def _ahead_is_safe(self, direction):
        """第二道防線：畫面上角色前方是否還是「與腳下同種」的地面。

        小地圖 1px ≈ 數十畫面 px、貼邊時太粗；這裡直接比對腳下與前方
        取樣區的平均色。讀不到畫面／缺 numpy 時不否決（回傳 True）。
        """
        if not self._edge_probe_enabled or self._last_frame is None:
            return True
        anchor = self._player_screen(self._last_frame)
        return probe_ahead_safe(self._last_frame, anchor, direction,
                                self._edge_probe_cfg)

    def _bounds_at(self, player_mm):
        """回傳目前位置適用的巡邏邊界 (left, right)（小地圖 x）。

        兩平台模式下取「玩家目前站著的平台」的範圍；不在任何平台上
        （換平台途中）回 None，交給 plan_two_platforms 導航。
        單平台模式一律回全域 patrol_left/right。
        """
        if not self._platforms:
            return self._patrol_left, self._patrol_right
        y_tol = int(self._switch_cfg.get("y_tolerance", 1))
        x_tol = int(self._switch_cfg.get("x_tolerance", 2))
        for plat in self._platforms:
            if on_platform(player_mm, plat, y_tol, x_tol):
                return plat["x_range"][0], plat["x_range"][1]
        return None

    def _approach_allowed(self, player_mm, direction):
        """走近怪之前的防掉落檢查：在邊界內才允許往 direction 再走一步。

        換平台途中（不在任何平台上）也不追：此時完全沒有邊界可依據，
        而且追怪會蓋掉 plan_two_platforms 的導航（acted=True）——原本
        「追出平台」的 bug 會從這裡鑽回來。射程內的怪照打不受影響。
        """
        if player_mm is None:
            return False  # 讀不到自己的位置就不盲走
        if not self._ahead_is_safe(direction):
            return False  # 畫面偵測前方不是平台地面（第二道防線）
        bounds = self._bounds_at(player_mm)
        if bounds is None:
            return False
        return approach_is_safe(player_mm[0], direction,
                                bounds[0], bounds[1], self._edge_margin)

    def _execute_platform_plan(self, plan):
        """執行兩平台決策的單步動作（patrol / approach / jump_move / hold）。"""
        kind = plan.get("kind")
        name = plan.get("name", "")
        st = self._plat_state
        if kind == "patrol":
            self._patrol(self._last_player, plan["left"], plan["right"],
                         label=f"{name} 無怪{st.empty_loops}圈")
            return
        if kind == "hold":
            if self.dry_run:
                print("        ↳ 小地圖讀不到玩家 → 原地等待")
            return
        d = plan.get("dir", "right")
        if self.dry_run:
            verb = "邊走邊跳上" if kind == "jump_move" else "走向"
            print(f"        ↳ 換平台：{verb}「{name}」(往{d}，第{st.move_loops}圈)")
        if kind != "jump_move" and not self._ahead_is_safe(d):
            # 走路換平台但畫面偵測前方可疑 → 這一圈不走（move_loops 會累積，
            # 卡太久 plan_two_platforms 自己會放棄改守另一平台）
            if self.dry_run:
                print(f"        ↳ 換平台：畫面偵測往{d}的前方不是地面 → 暫停一步")
            return
        ctx = self._make_ctx(None)
        if kind == "jump_move":
            self.command_book.create("jump_move", {"dir": d}).execute(ctx)
        else:
            self.command_book.create("approach", {"dir": d, "seconds": self._patrol_step}).execute(ctx)
        self._moved_last_loop = True

    def _step_routine(self, frame):
        """（保留）依 routine 定位點執行動作；本抓怪流程改用 _patrol，故預設不呼叫。"""
        if not self.routine or not self.routine.points:
            return
        point = self.routine.points[0]
        ctx = self._make_ctx(frame)
        for action in point.commands:
            self.command_book.create(action.get("action"), action.get("args", {})).execute(ctx)
