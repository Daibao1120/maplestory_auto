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

import time
from typing import List, Optional

from src.capture import ScreenCapture
from src.vision import TemplateMatcher, MinimapLocator, HealthBarDetector, MonsterDetector
from src.input import InputController
from src.routine import Routine, load_routine
from src.commands import (CommandBook, CommandContext, PlatformState,
                          plan_ranged, decision_to_actions,
                          filter_attackable, plan_two_platforms)
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

        # 兩平台輪流清怪（combat.platforms 有設定時啟用）
        self._platforms = []
        self._switch_cfg = {}
        self._attack_y_band = None
        self._plat_state = PlatformState()

        self._last_potion_ts = 0.0
        self._last_player = None
        self._last_monsters: List = []
        self._demo_source = ""

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
            from src.vision.synthetic import build_demo
            demo = build_demo(vision_cfg)
            if demo is not None:
                self.capture.scene_provider = (lambda d=demo: d.frame)
                for name, img in demo.monster_templates.items():
                    self.monster.add_template(name, img)
                self._demo_source = "合成場景（未指定 --demo-image）"
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

                # 感知
                player_mm = self.minimap.locate_player(frame)
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
                acted = self._combat(player_screen, attackable)
                if self._platforms:
                    self._plat_state, plan = plan_two_platforms(
                        player_mm, len(attackable), self._plat_state,
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

    def _combat(self, player_screen, monsters):
        """打怪：弓箭手遠程定點。有怪回傳 True（已處理），無怪回傳 False。"""
        if not monsters:
            return False
        decision = plan_ranged(player_screen, monsters, self._attack_range)
        if decision is None:
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
        return True

    def _patrol(self, player_mm, left=None, right=None, label=""):
        """沒有怪時沿平台左右巡邏；到折返點就換方向（避免走進水裡）。

        left/right 可由兩平台邏輯帶入該平台的邊界；未帶則用全域設定。
        """
        left = self._patrol_left if left is None else left
        right = self._patrol_right if right is None else right
        d = self._patrol_dir
        at_edge = False
        if player_mm is not None:
            px = player_mm[0]
            if px <= left:
                d, at_edge = "right", True
            elif px >= right:
                d, at_edge = "left", True
            self._patrol_dir = d
        if self.dry_run:
            edge = "（到邊界，折返）" if at_edge else ""
            where = f"[{label}] " if label else ""
            print(f"        ↳ 無怪 → {where}沿平台巡邏往{d}{edge}")
        ctx = self._make_ctx(None)
        self.command_book.create("approach", {"dir": d, "seconds": self._patrol_step}).execute(ctx)

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
        ctx = self._make_ctx(None)
        if kind == "jump_move":
            self.command_book.create("jump_move", {"dir": d}).execute(ctx)
        else:
            self.command_book.create("approach", {"dir": d, "seconds": self._patrol_step}).execute(ctx)

    def _step_routine(self, frame):
        """（保留）依 routine 定位點執行動作；本抓怪流程改用 _patrol，故預設不呼叫。"""
        if not self.routine or not self.routine.points:
            return
        point = self.routine.points[0]
        ctx = self._make_ctx(frame)
        for action in point.commands:
            self.command_book.create(action.get("action"), action.get("args", {})).execute(ctx)
