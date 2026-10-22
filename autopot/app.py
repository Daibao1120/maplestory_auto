"""主程式：一直盯著畫面，需要時自動去妙妙補貨。"""
from __future__ import annotations

import logging
import time

from .calib import ASSETS, ROOT, Calibration
from .digits import DigitReader, DigitTemplates
from .guardian import CountTracker, Guardian
from .notify import Notifier
from .shop import ShopBot, ShopError
from .vision import bar_framed, bar_ratio, bar_visible, crop, find

log = logging.getLogger("autopot")


def load_digit_reader() -> DigitReader:
    learned = ROOT / "data" / "digits_learned.npz"
    path = learned if learned.exists() else ASSETS / "digits_base.npz"
    return DigitReader(DigitTemplates.load(path))


class NotVisible(Exception):
    """遊戲畫面被擋住、縮小或截到黑畫面。"""


class Runner:
    def __init__(self, cfg: dict, win, dry_run: bool = False):
        self.cfg = cfg
        self.win = win
        self.cal = Calibration.load()
        miss = self.cal.missing([b["template"] for b in cfg["buy"].values()])
        if miss:
            raise SystemExit("校正還沒完成，缺少：\n  " + "\n  ".join(miss) + "\n請先執行 calibrate.py")
        self.shop = ShopBot(win, self.cal, cfg, dry_run=dry_run)
        self.notify = Notifier(cfg.get("notify", {}))
        sf = cfg["safety"]
        self.guard = Guardian(sf)
        # 快捷欄數量：用像素字形比對（autopot/qsdigits.py，讀不確定就回傳 None）
        from .qsdigits import QuickslotReader
        self.reader = QuickslotReader()
        self._qs_saved: dict[str, float] = {}
        # 升等時間預估（看 EXP 條）
        from .calib import DATA_DIR
        from .levelup import LevelForecast
        self.levels = LevelForecast.load(cfg.get("level"), DATA_DIR / "level.json")
        from .levelread import LevelReader
        self.levels.reader = LevelReader()          # 讀得到畫面上的等級就以畫面為準
        self._last_forecast_log = 0.0
        self.trackers = {k: CountTracker(sf["max_use_per_sec"], sf["ocr_jump_buffer"]) for k in cfg["buy"]}
        self.paused = False
        self.force_refill = False
        self.quit = False
        self.restart = False          # 收到 restart 指令：結束後重新啟動（載入新版程式）
        self.failures = 0
        self.stopped_reason = ""
        self.last_status = time.time()
        self.last_user_move = 0.0
        self._last_mouse = None
        self._our_click_until = 0.0
        self.ocr_ok = False
        self.ocr_warned = False
        self.hidden_since = 0.0
        self.snapshot: dict = {}

    # ---------- 熱鍵 ----------
    def setup_hotkeys(self) -> None:
        hk = self.cfg.get("hotkeys", {})
        try:
            import keyboard
        except ImportError:
            log.info("沒有安裝 keyboard 套件，熱鍵停用（可用 Ctrl+C 結束）")
            return

        def toggle():
            self.paused = not self.paused
            self.notify.send("info", "已暫停" if self.paused else "繼續監控")

        def refill():
            self.force_refill = True
            log.info("收到 F10：馬上補貨")

        def quit_():
            self.quit = True

        try:
            keyboard.add_hotkey(hk.get("pause", "f9"), toggle)
            keyboard.add_hotkey(hk.get("refill_now", "f10"), refill)
            keyboard.add_hotkey(hk.get("quit", "f12"), quit_)
            log.info("熱鍵：%s 暫停/繼續、%s 立刻補貨、%s 結束",
                     hk.get("pause", "f9"), hk.get("refill_now", "f10"), hk.get("quit", "f12"))
        except Exception as e:
            log.warning("熱鍵註冊失敗（可能需要用系統管理員身分執行）：%s", e)

    # ---------- 讀畫面 ----------
    _bar_shift = (0, 0)
    _bar_base = None          # 校正位置正常時，find_bars 找到的 (HP 條左邊 x, 狀態列中線 y)

    def _bar_regions(self):
        dx, dy = self._bar_shift
        out = []
        for k in ("hp_bar", "mp_bar"):
            x, y, w, h = self.cal.region(k)
            out.append([x + dx, y + dy, w, h])
        return out

    @staticmethod
    def _bars_ok(img, hp_r, mp_r) -> bool:
        if not (bar_visible(crop(img, hp_r), "hp") and bar_visible(crop(img, mp_r), "mp")):
            return False
        return bar_framed(img, hp_r) and bar_framed(img, mp_r)

    def _relocate_bars(self, img):
        """HP/MP 條不在校正的位置 → 在畫面下方找狀態列（HP、MP、EXP 三個框），找到就跟著移。
        只接受「框的寬度跟校正時一樣」的結果（避免把別的紅色東西當成 HP 條）。回傳新的位移或 None。"""
        try:
            from .expbar import find_bars
            bars = find_bars(img)
        except Exception:
            return None
        if not bars:
            return None
        y, ((hx0, hx1), (mx0, mx1), _) = bars
        hp = self.cal.region("hp_bar")
        mp = self.cal.region("mp_bar")
        if abs((hx1 - hx0) - hp[2]) > max(6, hp[2] // 20) or abs((mx0 - hx0) - (mp[0] - hp[0])) > max(6, hp[2] // 20):
            return None
        bx, by = self._bar_base if self._bar_base else (hp[0], y)   # 沒有基準就假設上下沒動
        dx, dy0 = hx0 - bx, y - by
        # 狀態列中線的 y 會差幾 px（實測移位後差 4px，但要再往上 8px 才框得剛好）→ 上下試幾個位置，用真的看得到外框的那個
        new = None
        for d in sorted(range(-12, 13), key=abs):
            for ddx in (0, -1, 1, -2, 2):
                hr = [hp[0] + dx + ddx, hp[1] + dy0 + d, hp[2], hp[3]]
                mr = [mp[0] + dx + ddx, mp[1] + dy0 + d, mp[2], mp[3]]
                if self._bars_ok(img, hr, mr):
                    new = (dx + ddx, dy0 + d)
                    break
            if new:
                break
        if new is None or new == self._bar_shift:
            return None
        self._bar_shift = new
        if new == (0, 0):
            log.info("HP/MP 條回到原本的位置了")
        else:
            log.warning("HP/MP 條的位置跟校正時差了 %s（遊戲下方狀態列移動了），改看新的位置", new)
        return new

    def read_state(self):
        img = self.win.capture()
        ws = self.cal.data.get("window_size")
        if ws and (img.shape[1], img.shape[0]) != tuple(ws) and self.fix_window_size():
            img = self.win.capture()
        if ws and (img.shape[1], img.shape[0]) != tuple(ws):
            raise ShopError(f"遊戲視窗大小變了（現在 {img.shape[1]}x{img.shape[0]}，校正時 {ws[0]}x{ws[1]}），"
                            "請把視窗調回原本大小，或重新校正")
        try:
            out = self._analyze(img)
        except NotVisible:
            # 遊戲被別的視窗蓋住了 → 改用「背景截圖」（不用把遊戲叫到前面，你可以繼續用別的視窗）
            bg = getattr(self.win, "capture_background", None)
            img2 = bg() if bg else None
            if img2 is None or (ws and (img2.shape[1], img2.shape[0]) != tuple(ws)):
                if self.background_mode:
                    self.background_mode = False
                    log.info("背景截圖也看不到遊戲（視窗縮小了？）")
                raise
            out = self._analyze(img2)
            if not self.background_mode:
                self.background_mode = True
                log.info("遊戲被別的視窗蓋住了，改用背景截圖繼續監控（不會把遊戲叫到前面）")
            return out
        if self.background_mode:
            self.background_mode = False
            log.info("遊戲又看得到了")
        return out

    background_mode = False

    def _analyze(self, img):
        ws = self.cal.data.get("window_size")
        # 截到全黑/全白（切換視窗瞬間、縮小）或被別的視窗擋住 → 這次不判斷
        if float(img.std()) < 4:
            raise NotVisible("截圖是空白畫面")
        h = img.shape[0]
        if find(img, self.cal.template("inv_button"), self.cfg.get("match_threshold", 0.85),
                [0, h * 2 // 3, img.shape[1], h - h * 2 // 3]) is None:
            raise NotVisible("看不到遊戲下方工具列，視窗可能被擋住或縮小")
        hp_r, mp_r = self._bar_regions()
        if not self._bars_ok(img, hp_r, mp_r):
            # 10/21 實測（手機版）：下方狀態列（等級、HP、MP、EXP）整塊往右移了 150px，
            # 程式一直以為 HP 條被擋住、整整一個多小時沒在顧 → 在畫面上重新找 HP/MP 條
            moved = self._relocate_bars(img)
            if moved is not None:
                hp_r, mp_r = self._bar_regions()
            if moved is None or not self._bars_ok(img, hp_r, mp_r):
                hp_img = crop(img, hp_r)
                mp_img = crop(img, mp_r)
                if not (bar_visible(hp_img, "hp") and bar_visible(mp_img, "mp")):
                    raise NotVisible("HP/MP 條被擋住了（可能有其他視窗蓋在遊戲左下角）")
                # 實測：切換到別的視窗時，遊戲左下角被深色的視窗蓋住，看起來像「空的 HP 條」→ 誤判死亡。
                # 真的 HP 條上下一定有白色外框，沒有外框就是被擋住了。
                raise NotVisible("看不到 HP/MP 條的外框（遊戲被別的視窗擋住了）")
        hp_img = crop(img, hp_r)
        mp_img = crop(img, mp_r)
        if self._bar_base is None and self._bar_shift == (0, 0):
            # 記下「正常位置」時狀態列在哪（之後狀態列移動了，才知道移了多少）
            try:
                from .expbar import find_bars
                b = find_bars(img)
                if b and abs(b[1][0][0] - hp_r[0]) <= 6:
                    self._bar_base = (b[1][0][0], b[0])
            except Exception:
                pass
        hp = bar_ratio(hp_img, "hp")
        mp = bar_ratio(mp_img, "mp")
        now = time.time()
        counts = {}
        if not self.cfg.get("ocr_enabled", False):
            return img, hp, mp, {k: None for k in self.cfg["buy"]}
        for k, b in self.cfg["buy"].items():
            if not b.get("quickslot") or not self.cal.has_region(b["quickslot"]):
                counts[k] = None             # 這個補品沒放快捷欄（或沒設定）→ 不讀數量，靠定時補貨
                continue
            sx = self.cal.data.get("digit_start", {}).get(b["quickslot"])
            slot = crop(img, self.cal.region(b["quickslot"]))
            v = self.reader.read(slot, start_x=sx)
            self._keep_qs_sample(k, slot, v, now)
            counts[k] = self.trackers[k].update(v, now)
        return img, hp, mp, counts

    # ---------- 遠端指令（data/command.txt） ----------
    COMMANDS = ("shot", "bgshot", "dryrun", "refill", "status", "restart")

    def check_command(self) -> str | None:
        """在資料夾裡放一個 command.txt（內容是 shot / dryrun / refill / status / restart）就會執行一次，執行完刪掉。
        用來從遠端測試：例如 dryrun = 走一次完整補貨流程、但每個對話框都按取消。"""
        from .calib import DATA_DIR
        f = DATA_DIR / "command.txt"
        if not f.exists():
            return None
        try:
            cmd = f.read_text(encoding="utf-8").strip().lower()
            f.unlink()
        except Exception:
            return None
        if cmd not in self.COMMANDS:
            log.warning("command.txt 裡的指令看不懂：%r（可以用 %s）", cmd, " / ".join(self.COMMANDS))
            return None
        log.info("收到指令：%s", cmd)
        try:
            if cmd == "shot":
                import cv2
                d = DATA_DIR / "debug"
                d.mkdir(parents=True, exist_ok=True)
                path = d / f"shot_{time.strftime('%m%d_%H%M%S')}.png"
                cv2.imwrite(str(path), self.win.capture())
                log.info("畫面存到 %s", path)
            elif cmd == "bgshot":
                import cv2
                d = DATA_DIR / "debug"
                d.mkdir(parents=True, exist_ok=True)
                bg = getattr(self.win, "capture_background", None)
                im = bg() if bg else None
                if im is None:
                    log.info("背景截圖失敗（這個遊戲/視窗不支援，或視窗縮小了）")
                else:
                    path = d / f"bgshot_{time.strftime('%m%d_%H%M%S')}.png"
                    cv2.imwrite(str(path), im)
                    log.info("背景截圖存到 %s", path)
            elif cmd == "status":
                _, hp, mp, counts = self.read_state()
                log.info("狀態：%s", self.status_line(hp, mp, counts))
            elif cmd == "refill":
                self.force_refill = True
            elif cmd == "restart":
                log.info("重新啟動程式（載入新版）…")
                self.restart = True
                self.quit = True
            elif cmd == "dryrun":
                was = self.shop.dry
                saved = (self.guard.last_refill, self.failures, getattr(self, "_retry_at", 0.0))
                self.shop.dry = True
                try:
                    ok = self.refill({k: None for k in self.cfg["buy"]}, "遠端試跑（不會真的買賣）")
                finally:
                    # 試跑不算一次補貨：定時補貨的時間、失敗次數都恢復原狀
                    self.shop.dry = was
                    self.guard.last_refill, self.failures, self._retry_at = saved
                log.info("試跑%s", "成功" if ok else "失敗（看上面的訊息）")
        except Exception as e:
            log.warning("指令 %s 失敗：%r", cmd, e)
        return cmd

    def track_exp(self, img, now: float) -> None:
        """記錄 EXP，每 5 分鐘印一次「下次升等 / 到 200 等」的預估（升等當下也印）。"""
        if self.levels.level and self.levels.level >= 200:
            # 已經滿等了（模擬器的 die7）不用預估；但還是要讀畫面上的等級 —— 轉生後會變回低等級（10/22 呆の天 轉生成 61 等）
            try:
                self.levels.update(img, now)
            except Exception:
                pass
            if self.levels.level >= 200:
                return
        try:
            lv0 = self.levels.level
            self.levels.update(img, now)
            leveled = self.levels.level != lv0
            if leveled or now - self._last_forecast_log >= 300:
                self._last_forecast_log = now
                log.info(self.levels.text(now))
        except Exception as e:
            log.debug("EXP 預估失敗：%r", e)

    def fix_window_size(self) -> bool:
        """遊戲視窗大小跟校正時不一樣 → 自動調回去。成功回傳 True。
        （每 10 秒最多試一次，避免你正在拖視窗時一直跟你搶）"""
        ws = self.cal.data.get("window_size")
        if not ws or not hasattr(self.win, "resize_client"):
            return False
        now = time.time()
        if now - getattr(self, "_resize_at", 0.0) < 10:
            return False
        self._resize_at = now
        try:
            before = tuple(self.win.client_rect()[2:])
            after = tuple(self.win.resize_client(int(ws[0]), int(ws[1])))
        except Exception as e:
            log.warning("自動調整遊戲視窗大小失敗：%s", e)
            return False
        if after == tuple(ws):
            log.info("遊戲視窗大小是 %dx%d，已自動調回校正時的 %dx%d", *before, *ws)
            return True
        log.warning("想把遊戲視窗調回 %dx%d，但只調到 %dx%d（螢幕可能不夠大，或遊戲不讓調）", *ws, *after)
        return False

    def _keep_qs_sample(self, k: str, slot, v, now: float) -> None:
        """偶爾把快捷欄那一格存起來（data/debug/qs_*.png）：讀不到的、和每小時一張讀到的，
        之後可以拿來檢查有沒有讀錯、改進辨識。"""
        key = f"{k}_{'ok' if v is not None else 'none'}"
        gap = 3600 if v is not None else 600
        if now - self._qs_saved.get(key, 0) < gap:
            return
        self._qs_saved[key] = now
        try:
            import cv2
            from .calib import DATA_DIR
            d = DATA_DIR / "debug"
            d.mkdir(parents=True, exist_ok=True)
            old = sorted(d.glob(f"qs_{k}_*.png"))
            for f in old[:-30]:
                f.unlink()
            cv2.imwrite(str(d / f"qs_{k}_{v if v is not None else 'none'}_{time.strftime('%m%d_%H%M%S')}.png"), slot)
        except Exception:
            pass

    def counts_reliable(self, now: float) -> bool:
        ks = [k for k, b in self.cfg["buy"].items() if b.get("quickslot") and self.cal.has_region(b["quickslot"])]
        return bool(ks) and all(not self.trackers[k].stale(now, 120) for k in ks)

    def user_active(self, now: float) -> bool:
        """你最近有沒有在用電腦（動滑鼠，或按鍵盤 —— 例如在看網頁、打字）。"""
        pos = self.win.mouse_pos()
        if self._last_mouse is not None and pos != self._last_mouse and now > self._our_click_until:
            self.last_user_move = now
        self._last_mouse = pos
        limit = self.cfg["safety"]["defer_if_user_active_sec"]
        idle = getattr(self.win, "idle_seconds", None)
        if idle and limit and now > self._our_click_until:
            # 鍵盤只在「你在用別的視窗」時才算（你在遊戲裡按技能打怪，不算在忙別的事）
            try:
                if self.win.foreground() != getattr(self.win, "hwnd", None) and idle() < limit:
                    return True
            except Exception:
                pass
        return now - self.last_user_move < limit

    # ---------- 補貨 ----------
    def plan(self, counts: dict, emergency: bool) -> dict[str, int]:
        out = {}
        for k, b in self.cfg["buy"].items():
            v = counts.get(k) if self.ocr_ok else None
            # 剛啟動、還沒累積幾筆可信數字就讀到很低 → 可能讀錯，保守用固定數量
            if v is not None and len(self.trackers[k].history) < 5 and v < b["refill_below"]:
                v = None
            if v is None:
                out[k] = int(b["fallback_buy"])
            else:
                out[k] = max(0, int(b["target"]) - int(v))
        return out

    def _save_fail_shot(self) -> None:
        try:
            import cv2
            from .calib import DATA_DIR
            cv2.imwrite(str(DATA_DIR / "refill_fail.png"), self.win.capture())
        except Exception:
            pass

    def refill(self, counts: dict, reason: str, emergency: bool = False) -> bool:
        plan = self.plan(counts, emergency)
        self.notify.send("warn" if emergency else "info",
                         f"開始{'緊急' if emergency else ''}補貨（{reason}）："
                         + "、".join(f"{self.cfg['buy'][k]['name']} {q} 個" for k, q in plan.items()))
        prev = getattr(self, "_fg_before", None) or self.win.foreground()
        self._fg_before = None
        ok = True
        try:
            self.win.activate()
            # 補貨時讓遊戲固定在最上層：實測其他程式跳出來的視窗（通知、聊天軟體…）會蓋住道具欄，
            # 截圖就看不到按鈕了
            if hasattr(self.win, "set_topmost"):
                try:
                    self.win.set_topmost(True)
                except Exception:
                    pass
            self._our_click_until = time.time() + 120
            self.shop.open_shop()
            sells = (self.cfg.get("sell") or []) if self.cfg.get("sell_enabled", False) else []
            missing = [s["template"] for s in sells if not self.cal.has_template(s["template"])]
            if missing:
                from .learn_items import capture_items
                self.shop.click_tab("shop_tab_use", "shop_leave")
                capture_items(self.shop, missing)
            ready = [s for s in sells if self.cal.has_template(s["template"])]
            if ready:
                t0 = time.time()
                try:
                    res = self.shop.sell_all(ready)
                    for s in ready:
                        n = res.get(s["template"], 0)
                        if n:
                            log.info("%s %s %d 組", "[試跑] 示範賣" if self.shop.dry else "賣掉", s.get("name", s["template"]), n)
                except ShopError as e:
                    # 賣東西失敗不影響買補品
                    log.warning("賣東西失敗：%s", e)
                    self.shop._cancel_dialog()
                log.info("賣東西花了 %.0f 秒，馬上買補品（不然空格會被撿到的東西佔掉）", time.time() - t0)
            # 先買 MP（刨冰）：背包空間有限，MP 補品比較容易先用完
            order = sorted(plan, key=lambda k: 0 if k == "mp" else 1)
            for k in order:
                if plan[k] > 0:
                    # 背包滿了只買到一部分（buy 回傳 False）不算補貨失敗：該買的都買了，只是裝不下
                    b = self.cfg["buy"][k]
                    self.shop.buy(b["template"], plan[k], th=b.get("threshold"), verify=bool(b.get("learned")))
        except ShopError as e:
            ok = False
            self._last_fail = str(e)
            self._save_fail_shot()
            self.notify.send("warn", f"補貨失敗：{e}（當下畫面存在 data/refill_fail.png）")
        except Exception as e:  # 任何意外都要把商店關掉
            if type(e).__name__ == "FailSafeException":
                raise
            ok = False
            self.notify.send("warn", f"補貨時發生錯誤：{e!r}")
            log.exception("refill")
        finally:
            try:
                if hasattr(self.win, "stats_text"):
                    st = getattr(self.shop, "scroll_stats", {})
                    log.info("這次補貨的操作統計：%s；清單捲動：點捲動鈕 %d 次、拖滑塊 %d 次（其中 %d 次是點了沒反應才改拖）",
                             self.win.stats_text(), st.get("tap", 0), st.get("drag", 0), st.get("tap_fail", 0))
                    self.shop.scroll_stats = {"tap": 0, "drag": 0, "tap_fail": 0}
            except Exception:
                pass
            try:
                self.shop.close_shop()
            except Exception as e:
                if type(e).__name__ == "FailSafeException":
                    raise
                self.notify.send("critical", f"商店關不起來，請手動檢查遊戲畫面：{e}")
            self._our_click_until = time.time() + 3
            if hasattr(self.win, "set_topmost"):
                try:
                    self.win.set_topmost(False)
                except Exception:
                    pass
            self.win.restore_foreground(prev)
        now = time.time()
        self.guard.last_refill = now
        for t in self.trackers.values():
            t.expect_increase_until = now + 60
        if getattr(self.shop, "bag_full", False):
            self.shop.bag_full = False
            self.notify.send("warn", "背包（消耗欄）快滿了，補品只買到一部分。請清出一些格子，"
                                     "或把 config.yaml 的 fallback_buy 調小", cooldown=1800)
        if ok:
            self.failures = 0
        else:
            self.failures += 1
            if self.failures >= self.cfg["safety"]["max_refill_failures"]:
                # 一直失敗通常是同一個原因（例如按鈕被擋住），每 2 分鐘重試沒有用 → 改成 10 分鐘試一次
                self._retry_at = time.time() + 600
                self.notify.send("critical", f"連續補貨失敗 {self.failures} 次（最後一次：{getattr(self, '_last_fail', '')}），"
                                             "10 分鐘後再試。請盡快回來看遊戲！", cooldown=600)
        return ok

    def verify_after_refill(self, before: dict, plan_hint: str) -> None:
        """補完 5 秒後再讀一次數量，確認真的有變多。"""
        time.sleep(5)
        try:
            _, _, _, after = self.read_state()
            time.sleep(self.cfg["check_interval_sec"])
            _, _, _, after = self.read_state()
        except Exception:
            return
        for k, v in after.items():
            b = before.get(k)
            if self.ocr_ok and b is not None and v is not None and v <= b and not self.shop.dry:
                self.notify.send("warn", f"補貨後 {self.cfg['buy'][k]['name']} 數量沒有增加（{b} → {v}），"
                                         "可能背包滿了、楓幣不夠，或沒有買成功")

    # ---------- 主迴圈 ----------
    def status_line(self, hp, mp, counts) -> str:
        now = time.time()
        parts = [f"HP {hp:.0%}", f"MP {mp:.0%}"]
        for k, v in counts.items():
            name = self.cfg["buy"][k]["name"]
            rate = self.trackers[k].rate_per_hour(now)
            eta = f"，約 {v / rate:.1f} 小時用完" if (v and rate) else ""
            if v is None and not self.cfg.get("ocr_enabled", False):
                continue          # 沒開數字辨識 → 不顯示「? 個」
            parts.append(f"{name} {v if v is not None else '?'} 個" + (f"（每小時 {rate:.0f}{eta}）" if rate else ""))
        if not self.cfg.get("ocr_enabled", False):
            nxt = self.guard.last_refill + self.cfg["safety"]["timer_refill_hours"] * 3600
            mins = max(0, (nxt - now) / 60)
            parts.append(f"下次定時補貨：約 {mins:.0f} 分鐘後（{time.strftime('%H:%M', time.localtime(nxt))}）")
        return "｜".join(parts)

    def run(self, refill_on_start: bool = False) -> None:
        cfg = self.cfg
        self.setup_hotkeys()
        # 一開始先把遊戲叫到最前面（命令視窗剛打開時會蓋在上面）
        try:
            self.win.activate()
        except Exception as e:
            log.warning("把遊戲視窗叫到前面失敗：%s", e)
        self.guard.last_refill = 0 if refill_on_start else time.time()
        self.notify.send("info", "開始監控" + ("（試跑模式：不會真的買賣）" if self.shop.dry else ""))
        if self.levels.level:
            log.info("目前等級：%d（之後會從畫面左下角讀等級；讀不到時才用這個數字）", self.levels.level)
        else:
            log.info("沒有設定目前等級：只預估下次升等時間。要算到 200 等，請在 config.yaml 填 level")
        last_log = 0.0
        while not self.quit:
            time.sleep(cfg["check_interval_sec"])
            self.check_command()
            if self.paused:
                continue
            if self.guard.dead:
                self.notify.send("critical", f"角色可能死亡，已停止自動操作。{self.stopped_reason}", cooldown=300)
                # HP 又回來了（誤判、或已經復活）→ 連續 3 次都正常就恢復監控
                try:
                    _, hp, _, _ = self.read_state()
                    self._alive_streak = getattr(self, "_alive_streak", 0) + 1 if hp == hp and hp > 0.3 else 0
                except Exception:
                    self._alive_streak = 0
                if getattr(self, "_alive_streak", 0) >= 3:
                    self.guard.dead = False
                    self.guard.dead_streak = 0
                    self._alive_streak = 0
                    self.notify.send("info", "HP 恢復正常了，繼續監控")
                continue
            try:
                img, hp, mp, counts = self.read_state()
                self.hidden_since = 0.0
            except NotVisible as e:
                now = time.time()
                self.hidden_since = self.hidden_since or now
                gone = now - self.hidden_since
                # 10/22：以前看不到遊戲 10 秒、你又沒在動滑鼠，就會把遊戲叫到前面 → 你看別的視窗時一直被搶走。
                # 現在平常「絕對不搶」：只在「該定時補貨了」而且你已經一陣子沒碰電腦時，才叫出遊戲去補貨
                # （補完會切回你原本的視窗）。被蓋住時大多可以用背景截圖照常監控，不會走到這裡。
                due = now - self.guard.last_refill >= cfg["safety"]["timer_refill_hours"] * 3600
                if due and gone > 10 and not self.user_active(now) and hasattr(self.win, "activate") \
                        and getattr(self.win, "hwnd", None) is not None:
                    log.info("%s；該定時補貨了，你也沒在用電腦 → 把遊戲叫出來補貨", e)
                    try:
                        self._fg_before = self.win.foreground()     # 補完要切回你原本的視窗
                        self.win.activate()
                    except Exception:
                        pass
                elif gone > 30:
                    self.notify.send("warn", f"{e}，已 {gone:.0f} 秒無法監控（不會自動把遊戲叫到前面）",
                                     cooldown=1800)
                continue
            except ShopError as e:
                self.notify.send("critical", str(e), cooldown=300)
                continue
            except Exception as e:
                self.notify.send("warn", f"讀取畫面失敗：{e!r}", cooldown=300)
                continue
            now = time.time()
            # 數字辨識是否可用
            was = self.ocr_ok
            self.ocr_ok = self.counts_reliable(now)
            if was and not self.ocr_ok and not self.ocr_warned:
                self.notify.send("warn", "快捷欄數字辨識暫時失效，改用血條/魔力條與定時補貨保護")
                self.ocr_warned = True
            if self.ocr_ok:
                self.ocr_warned = False
            thresholds = {k: b["refill_below"] for k, b in cfg["buy"].items()}
            action, why = self.guard.tick(now, hp, mp, counts if self.ocr_ok else {}, self.ocr_ok, thresholds)
            manual = False
            if self.force_refill:
                action, why, self.force_refill, manual = "refill", "手動要求（熱鍵）", False, True

            self.track_exp(img, now)
            # 給圖形介面看的最新狀態
            self.snapshot = {"time": now, "hp": hp, "mp": mp, "counts": dict(counts),
                             "line": self.status_line(hp, mp, counts), "level": self.levels.text(now),
                             "background": self.background_mode}
            if now - last_log > 60:
                log.info(self.status_line(hp, mp, counts))
                last_log = now
            every = cfg.get("notify", {}).get("status_every_min", 0)
            if every and now - self.last_status > every * 60:
                self.notify.send("info", "[狀態] " + self.status_line(hp, mp, counts) + "｜" + self.levels.text(now))
                self.last_status = now

            if action == "dead":
                self.stopped_reason = why
                self.notify.send("critical", why)
            elif action == "alarm":
                self.notify.send("critical", why, cooldown=120)
            elif action in ("refill", "emergency_refill") and not manual and now < getattr(self, "_retry_at", 0):
                if now - getattr(self, "_retry_log", 0) > 300:
                    self._retry_log = now
                    log.warning("補貨一直失敗，%.0f 分鐘後再試（%s）", (self._retry_at - now) / 60, why)
            elif action in ("refill", "emergency_refill"):
                emergency = action == "emergency_refill"
                if not emergency and not manual and self.user_active(now):
                    log.info("你正在使用電腦，先延後補貨（%s）", why)
                    continue
                before = dict(counts)
                self.refill(counts, why, emergency)
                self.verify_after_refill(before, why)
            else:
                self.user_active(now)   # 更新滑鼠狀態
        self.notify.send("info", "程式結束")
