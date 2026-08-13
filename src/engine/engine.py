"""主迴圈引擎。

負責把各層組裝起來並持續執行：
    擷取畫面 → 生存維持（補水）→ rune 偵測 → 執行 routine 當前步驟。

本檔提供可運行的骨架與清楚的擴充點；核心決策（走位、瞄準、rune）以 TODO 佔位。
"""
from __future__ import annotations

import time
from typing import Optional

from src.capture import ScreenCapture
from src.vision import TemplateMatcher, MinimapLocator, HealthBarDetector
from src.input import InputController
from src.routine import Routine, load_routine
from src.commands import CommandBook, CommandContext
from src.rune import RuneDetector, RuneSolver


class BotEngine:
    """自動化主引擎。

    參數：
        config: 已載入的設定 dict（見 config/settings.example.yaml）。
        dry_run: True 時輸入層只記錄不實際送鍵，便於在無遊戲環境測試流程。
    """

    def __init__(self, config, dry_run=False):
        self.config = config or {}
        self.dry_run = dry_run
        self.running = False

        # 各層元件（於 setup() 建立）
        self.capture: Optional[ScreenCapture] = None
        self.matcher: Optional[TemplateMatcher] = None
        self.minimap: Optional[MinimapLocator] = None
        self.health: Optional[HealthBarDetector] = None
        self.controller: Optional[InputController] = None
        self.routine: Optional[Routine] = None
        self.command_book: Optional[CommandBook] = None
        self.rune_detector: Optional[RuneDetector] = None
        self.rune_solver: Optional[RuneSolver] = None

        self._last_potion_ts = 0.0
        self._point_index = 0

    # ---- 初始化 ----
    def setup(self):
        """依設定建立各層元件（不啟動遊戲，只準備物件）。"""
        cfg = self.config
        win = cfg.get("window", {})

        self.capture = ScreenCapture(
            backend=cfg.get("capture", {}).get("backend", "mss"),
            window_title=win.get("title"),
            region=win.get("region"),
        )
        vision_cfg = cfg.get("vision", {})
        self.matcher = TemplateMatcher(threshold=vision_cfg.get("template_match_threshold", 0.8))
        self.minimap = MinimapLocator(vision_cfg.get("minimap"))
        self.health = HealthBarDetector(vision_cfg.get("health_bar"))
        self.controller = InputController(cfg.get("humanize"), dry_run=self.dry_run)
        self.command_book = CommandBook()
        self.rune_detector = RuneDetector(cfg.get("rune"))
        self.rune_solver = RuneSolver()

        routine_path = cfg.get("routine", {}).get("path")
        if routine_path:
            try:
                self.routine = load_routine(routine_path)
            except FileNotFoundError:
                print(f"[警告] 找不到路線檔：{routine_path}，將以空路線啟動。")
        return self

    # ---- 主迴圈 ----
    def start(self):
        """啟動主迴圈，直到 stop() 被呼叫或發生 KeyboardInterrupt。"""
        if self.capture is None:
            self.setup()
        self.running = True
        fps = self.config.get("capture", {}).get("fps_limit", 15)
        frame_interval = 1.0 / max(1, fps)

        try:
            while self.running:
                t0 = time.time()
                frame = self.capture.grab()

                self._maintain_survival(frame)
                self._handle_rune(frame)
                self._step_routine(frame)

                # 控制迴圈頻率，避免 CPU 過載
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

    # ---- 各階段（核心邏輯以 TODO 佔位）----
    def _maintain_survival(self, frame):
        """依 HP/MP 比例補水（含冷卻時間）。"""
        surv = self.config.get("survival", {})
        keys = self.config.get("keys", {})
        now = time.time()
        if now - self._last_potion_ts < surv.get("potion_cooldown", 1.0):
            return
        if self.health.read_hp_ratio(frame) < surv.get("hp_threshold", 0.5):
            self.controller.tap(keys.get("hp_potion", "delete"))
            self._last_potion_ts = now
        elif self.health.read_mp_ratio(frame) < surv.get("mp_threshold", 0.3):
            self.controller.tap(keys.get("mp_potion", "end"))
            self._last_potion_ts = now

    def _handle_rune(self, frame):
        """偵測並嘗試解 rune。"""
        if not self.config.get("rune", {}).get("enabled", True):
            return
        if self.rune_detector.detect(frame):
            # TODO: 先走到 rune 位置，再解謎
            self.rune_solver.solve(frame, self.controller)

    def _step_routine(self, frame):
        """執行 routine 目前定位點的所有動作，然後前進到下一點。"""
        if not self.routine or not self.routine.points:
            return
        point = self.routine.points[self._point_index]
        ctx = CommandContext(
            controller=self.controller,
            vision={
                "matcher": self.matcher,
                "minimap": self.minimap,
                "health": self.health,
                "frame": frame,
            },
            config=self.config,
        )
        for action in point.commands:
            cmd = self.command_book.create(action.get("action"), action.get("args", {}))
            cmd.execute(ctx)

        # 前進到下一點（循環）
        self._point_index = (self._point_index + 1) % len(self.routine.points)
