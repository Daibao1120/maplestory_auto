"""楓之谷自動補水：圖形介面（打包成 exe 給朋友用）。

三個分頁：
  * 監控：開始 / 暫停 / 停止、立刻補貨、試跑；HP、MP、補品數量、升等預估；執行紀錄
  * 設定：1. 自動校正  2. 掃描妙妙商店挑補品  3. 掃描背包挑要賣的東西  4. 其他
  * 說明

所有會操作遊戲的事情（校正、掃描、監控）都在背景執行緒跑，畫面不會卡住；
同一時間只能做一件事（避免兩個流程同時搶滑鼠）。
"""
from __future__ import annotations

import base64
import logging
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import calib, catalog, settings, setup_logic

log = logging.getLogger("autopot.gui")

APP_TITLE = "楓之谷自動補水"
QS_CHOICES = ["Shift", "Ins", "Home", "PgUp", "Ctrl", "Del", "End", "PgDn", "沒放快捷欄"]
VERSION = "1.0.0"


def qs_to_region(label: str) -> str | None:
    return None if label not in QS_CHOICES[:-1] else f"qs_{label.lower()}"


def region_to_qs(region: str | None) -> str:
    if not region or not region.startswith("qs_"):
        return QS_CHOICES[-1]
    k = region[3:]
    for c in QS_CHOICES[:-1]:
        if c.lower() == k:
            return c
    return QS_CHOICES[-1]


class QueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        try:
            self.q.put(("log", self.format(record)))
        except Exception:
            pass


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.cfg = settings.load()
        self.win = None
        self.runner = None
        self.worker: threading.Thread | None = None
        self.task_name = ""
        self.shop_items: list[catalog.ListItem] = []
        self.inv_items: list[catalog.ListItem] = []
        self._imgs = []                      # tk 圖片要留著參考，不然會被回收
        self._setup_logging()
        root.title(f"{APP_TITLE} v{VERSION}")
        root.minsize(760, 560)
        self._build()
        self._refresh_setup_status()
        root.after(200, self._poll)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ================= 基本 =================
    def _setup_logging(self):
        calib.DATA_DIR.mkdir(parents=True, exist_ok=True)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%m-%d %H:%M:%S")
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        h = QueueHandler(self.q)
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        root.addHandler(h)
        try:
            from logging.handlers import RotatingFileHandler
            fh = RotatingFileHandler(calib.DATA_DIR / "autopot.log", maxBytes=2_000_000, backupCount=3,
                                     encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except Exception:
            pass

    def _game(self):
        """找遊戲視窗；找不到丟出錯誤（訊息給使用者看）。"""
        from .window import GameWindow
        title = self.cfg.get("window_title") or "MapleStory Worlds"
        if self.win is None or self.win.keyword != title:
            self.win = GameWindow(title, self.cfg.get("window_exclude"))
        if not self.win.locate():
            raise RuntimeError(f"找不到遊戲視窗（標題要包含「{title}」）。請先開遊戲、登入角色")
        return self.win

    def _busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _start_task(self, name: str, fn) -> bool:
        """在背景執行緒做 fn()；同一時間只做一件事。"""
        if self._busy():
            messagebox.showinfo(APP_TITLE, f"正在「{self.task_name}」，請等它做完（或先按停止）")
            return False

        def run():
            try:
                fn()
            except Exception as e:
                if type(e).__name__ == "FailSafeException":
                    log.warning("緊急停止（滑鼠甩到螢幕左上角）")
                elif isinstance(e, SystemExit):
                    log.warning(str(e))
                else:
                    log.warning("%s 失敗：%s", name, e)
                    logging.getLogger("autopot").debug("detail", exc_info=True)
            finally:
                self.q.put(("done", name))

        self.task_name = name
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()
        self._set_state(f"{name}中…")
        return True

    def _poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self._append_log(data)
                elif kind == "done":
                    self._set_state("待命")
                    self._refresh_setup_status()
                    self._unhook_hotkeys()
                elif kind == "shop_items":
                    self.shop_items = data
                    self._show_shop_items()
                elif kind == "inv_items":
                    self.inv_items = data
                    self._show_inv_items()
        except queue.Empty:
            pass
        r = self.runner
        if r is not None and self._busy() and self.task_name == "監控":
            snap = getattr(r, "snapshot", {}) or {}
            if snap:
                self.var_hp.set(f"{snap['hp']:.0%}" if snap.get("hp") == snap.get("hp") else "?")
                self.var_mp.set(f"{snap['mp']:.0%}" if snap.get("mp") == snap.get("mp") else "?")
                self.var_line.set(snap.get("line", ""))
                self.var_level.set(snap.get("level", ""))
                age = time.time() - snap.get("time", 0)
                tag = "（背景截圖）" if snap.get("background") else ""
                self._set_state(("已暫停" if r.paused else "監控中") + tag
                                + (f"｜{age:.0f} 秒沒看到遊戲畫面" if age > 15 else ""))
        self.root.after(300, self._poll)

    def _append_log(self, line: str):
        t = self.txt_log
        t.configure(state="normal")
        t.insert("end", line + "\n")
        n = int(t.index("end-1c").split(".")[0])
        if n > 2000:
            t.delete("1.0", f"{n - 2000}.0")
        t.see("end")
        t.configure(state="disabled")

    def _set_state(self, s: str):
        self.var_state.set(s)

    def _unhook_hotkeys(self):
        try:
            import keyboard
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass

    def _on_close(self):
        if self.runner is not None:
            self.runner.quit = True
        self.root.after(300, self.root.destroy)

    def _img(self, bgr, max_w: int):
        data = base64.b64encode(catalog.png_bytes(bgr, max_w)).decode("ascii")
        im = tk.PhotoImage(data=data)
        self._imgs.append(im)
        return im

    # ================= 畫面 =================
    def _build(self):
        style = ttk.Style()
        try:
            style.theme_use("vista" if sys.platform == "win32" else "clam")
        except Exception:
            pass
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_mon = ttk.Frame(nb, padding=10)
        self.tab_set = ttk.Frame(nb)
        self.tab_help = ttk.Frame(nb, padding=10)
        nb.add(self.tab_mon, text="  監控  ")
        nb.add(self.tab_set, text="  設定  ")
        nb.add(self.tab_help, text="  說明  ")
        self.nb = nb
        self._build_monitor()
        self._build_setup()
        self._build_help()

    def _build_monitor(self):
        f = self.tab_mon
        self.var_state = tk.StringVar(value="待命")
        self.var_hp = tk.StringVar(value="-")
        self.var_mp = tk.StringVar(value="-")
        self.var_line = tk.StringVar(value="")
        self.var_level = tk.StringVar(value="")
        self.var_problem = tk.StringVar(value="")
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="狀態：").grid(row=0, column=0, sticky="w")
        ttk.Label(top, textvariable=self.var_state, font=("", 11, "bold")).grid(row=0, column=1, sticky="w")
        ttk.Label(top, text="HP：").grid(row=1, column=0, sticky="w")
        ttk.Label(top, textvariable=self.var_hp).grid(row=1, column=1, sticky="w")
        ttk.Label(top, text="MP：").grid(row=1, column=2, sticky="w", padx=(20, 0))
        ttk.Label(top, textvariable=self.var_mp).grid(row=1, column=3, sticky="w")
        ttk.Label(top, textvariable=self.var_line, wraplength=700).grid(row=2, column=0, columnspan=6, sticky="w")
        ttk.Label(top, textvariable=self.var_level, wraplength=700).grid(row=3, column=0, columnspan=6, sticky="w")
        ttk.Label(top, textvariable=self.var_problem, foreground="#b00020", wraplength=700).grid(
            row=4, column=0, columnspan=6, sticky="w", pady=(4, 0))
        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="▶ 開始監控", command=self.start_monitor).pack(side="left")
        ttk.Button(btns, text="⏸ 暫停 / 繼續", command=self.toggle_pause).pack(side="left", padx=4)
        ttk.Button(btns, text="■ 停止", command=self.stop).pack(side="left")
        ttk.Button(btns, text="立刻補貨", command=self.refill_now).pack(side="left", padx=(16, 4))
        ttk.Button(btns, text="試跑一次（不會真的買賣）", command=self.dry_run).pack(side="left")
        hk = self.cfg.get("hotkeys", {})
        ttk.Label(f, text=f"熱鍵：{hk.get('pause', 'f9').upper()} 暫停/繼續、{hk.get('refill_now', 'f10').upper()} "
                          f"立刻補貨、{hk.get('quit', 'f12').upper()} 停止　｜　緊急停止：滑鼠甩到螢幕左上角",
                  foreground="#555").pack(anchor="w")
        self.txt_log = ScrolledText(f, height=16, state="disabled", font=("Consolas", 9))
        self.txt_log.pack(fill="both", expand=True, pady=(6, 0))

    def _scrollable(self, parent):
        canvas = tk.Canvas(parent, highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, padding=10)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def wheel(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind_all("<MouseWheel>", lambda e: wheel(e) if self.nb.index("current") == 1 else None)
        return inner

    def _build_setup(self):
        f = self._scrollable(self.tab_set)
        # ---- 第 1 步 ----
        s1 = ttk.LabelFrame(f, text="第 1 步：自動校正（約 1 分鐘，期間不要動滑鼠）", padding=8)
        s1.pack(fill="x", pady=(0, 8))
        self.var_title = tk.StringVar(value=self.cfg.get("window_title", "MapleStory Worlds"))
        row = ttk.Frame(s1)
        row.pack(fill="x")
        ttk.Label(row, text="遊戲視窗標題包含：").pack(side="left")
        ttk.Entry(row, textvariable=self.var_title, width=30).pack(side="left")
        ttk.Label(s1, text="開始前：遊戲維持平常掛機的大小、整個視窗在螢幕內、不要被擋住；現金欄裡要有妙妙。",
                  foreground="#555", wraplength=680).pack(anchor="w", pady=4)
        row = ttk.Frame(s1)
        row.pack(fill="x")
        ttk.Button(row, text="開始自動校正", command=self.autocal).pack(side="left")
        self.var_cal = tk.StringVar()
        ttk.Label(row, textvariable=self.var_cal).pack(side="left", padx=10)

        # ---- 第 2 步 ----
        s2 = ttk.LabelFrame(f, text="第 2 步：選要買的補品（掃描妙妙商店）", padding=8)
        s2.pack(fill="x", pady=(0, 8))
        ttk.Label(s2, text="按下去程式會打開妙妙商店，把商品清單從頭捲到尾，列出每一種商品；"
                           "在要買的那一列選「HP 補品」或「MP 補品」。", wraplength=680, foreground="#555").pack(anchor="w")
        row = ttk.Frame(s2)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="掃描妙妙商店", command=self.scan_shop).pack(side="left")
        self.var_buy_sum = tk.StringVar()
        ttk.Label(row, textvariable=self.var_buy_sum, justify="left").pack(side="left", padx=10)
        self.shop_frame = ttk.Frame(s2)
        self.shop_frame.pack(fill="x")
        self.buy_vars: dict[str, dict] = {}
        grid = ttk.Frame(s2)
        grid.pack(fill="x", pady=(6, 0))
        heads = ["", "名稱", "放在快捷欄", "補到幾個", "低於幾個就去補", "讀不到數量時每次買"]
        for c, h in enumerate(heads):
            ttk.Label(grid, text=h).grid(row=0, column=c, sticky="w", padx=3)
        for r, kind in enumerate(("hp", "mp"), 1):
            b = (self.cfg.get("buy") or {}).get(kind, {})
            d = settings.BUY_DEFAULTS[kind]
            v = {"name": tk.StringVar(value=b.get("name", "")),
                 "qs": tk.StringVar(value=region_to_qs(b.get("quickslot")) if b else
                                    ("Home" if kind == "hp" else "PgUp")),
                 "target": tk.IntVar(value=b.get("target", d["target"])),
                 "below": tk.IntVar(value=b.get("refill_below", d["refill_below"])),
                 "fallback": tk.IntVar(value=b.get("fallback_buy", d["fallback_buy"]))}
            self.buy_vars[kind] = v
            ttk.Label(grid, text=setup_logic.KIND_LABEL[kind]).grid(row=r, column=0, sticky="w")
            ttk.Entry(grid, textvariable=v["name"], width=12).grid(row=r, column=1, padx=3)
            ttk.Combobox(grid, textvariable=v["qs"], values=QS_CHOICES, width=10, state="readonly").grid(
                row=r, column=2, padx=3)
            for c, k in enumerate(("target", "below", "fallback"), 3):
                ttk.Spinbox(grid, textvariable=v[k], from_=0, to=99999, increment=100, width=8).grid(
                    row=r, column=c, padx=3)
        ttk.Button(s2, text="套用補品設定", command=self.apply_buy).pack(anchor="w", pady=(6, 0))
        self.var_buy_warn = tk.StringVar()
        ttk.Label(s2, textvariable=self.var_buy_warn, foreground="#b06000", wraplength=680).pack(anchor="w")

        # ---- 第 3 步 ----
        s3 = ttk.LabelFrame(f, text="第 3 步：要賣的東西（掃描背包的消耗欄，可以不設）", padding=8)
        s3.pack(fill="x", pady=(0, 8))
        ttk.Label(s3, text="掛機撿到、不需要的藥水可以每次補貨時順便賣掉（背包才不會滿）。"
                           "只會賣你勾的；「保留」= 背包最上面那幾組不賣。", wraplength=680,
                  foreground="#555").pack(anchor="w")
        row = ttk.Frame(s3)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="掃描背包", command=self.scan_inv).pack(side="left")
        ttk.Button(row, text="套用要賣的東西", command=self.apply_sell).pack(side="left", padx=6)
        self.inv_frame = ttk.Frame(s3)
        self.inv_frame.pack(fill="x")

        # ---- 第 4 步 ----
        s4 = ttk.LabelFrame(f, text="第 4 步：其他", padding=8)
        s4.pack(fill="x", pady=(0, 8))
        sf = self.cfg["safety"]
        self.var_timer = tk.DoubleVar(value=sf.get("timer_refill_hours", 3))
        self.var_hpem = tk.IntVar(value=int(round(sf.get("hp_emergency", 0.35) * 100)))
        self.var_ocr = tk.BooleanVar(value=bool(self.cfg.get("ocr_enabled", True)))
        self.var_lv = tk.StringVar(value=str(self.cfg.get("level") or ""))
        self.var_hook = tk.StringVar(value=self.cfg.get("notify", {}).get("discord_webhook", ""))
        rows = [("每幾小時定時補貨一次（順便賣東西）", ttk.Spinbox(s4, textvariable=self.var_timer, from_=0.5, to=24,
                                                      increment=0.5, width=6)),
                ("HP 低於幾 % 就緊急補貨（要比遊戲自動喝水的 % 低）", ttk.Spinbox(s4, textvariable=self.var_hpem, from_=5,
                                                                    to=90, increment=5, width=6)),
                ("現在幾等（預估到 200 等用，可以不填）", ttk.Entry(s4, textvariable=self.var_lv, width=8)),
                ("Discord Webhook（選填，有狀況時通知你）", ttk.Entry(s4, textvariable=self.var_hook, width=50))]
        for r, (lab, w) in enumerate(rows):
            ttk.Label(s4, text=lab).grid(row=r, column=0, sticky="w", pady=2)
            w.grid(row=r, column=1, sticky="w", padx=6)
        ttk.Checkbutton(s4, text="讀快捷欄上的補品數量（數量低就提早去補）", variable=self.var_ocr).grid(
            row=len(rows), column=0, columnspan=2, sticky="w")
        ttk.Button(s4, text="儲存", command=self.save_other).grid(row=len(rows) + 1, column=0, sticky="w", pady=6)

    def _build_help(self):
        t = ScrolledText(self.tab_help, wrap="word", font=("", 10))
        t.pack(fill="both", expand=True)
        t.insert("end", HELP_TEXT)
        t.configure(state="disabled")

    # ================= 設定頁 =================
    def _refresh_setup_status(self):
        cal = calib.Calibration.load()
        miss = cal.missing()
        self.var_cal.set("✅ 已校正" if not miss else "還沒校正")
        self.var_buy_sum.set(setup_logic.summary(self.cfg))
        self.var_buy_warn.set("\n".join(setup_logic.warnings_for(self.cfg)) if self.cfg.get("buy") else "")
        p = settings.problems(self.cfg)
        self.var_problem.set(("開始之前還要：" + "；".join(p)) if p else "")

    def autocal(self):
        self.cfg["window_title"] = self.var_title.get().strip() or "MapleStory Worlds"
        settings.save(self.cfg)

        def job():
            from .autocal import AutoCalibrator
            win = self._game()
            AutoCalibrator(win, self.cfg, say=log.info).run()
        self._start_task("自動校正", job)

    def _shop_bot(self):
        from .shop import ShopBot
        cal = calib.Calibration.load()
        if cal.missing():
            raise RuntimeError("還沒做自動校正（第 1 步）")
        return ShopBot(self._game(), cal, self.cfg)

    def scan_shop(self):
        def job():
            bot = self._shop_bot()
            bot.win.activate()
            time.sleep(0.5)
            log.info("掃描妙妙商店…（期間不要動滑鼠）")
            items = catalog.scan_shop(bot)
            bot.close_shop()
            log.info("掃描完成：商店裡有 %d 種商品", len(items))
            self.q.put(("shop_items", items))
        self._start_task("掃描商店", job)

    def scan_inv(self):
        def job():
            bot = self._shop_bot()
            bot.win.activate()
            time.sleep(0.5)
            log.info("掃描背包的消耗欄…（期間不要動滑鼠）")
            items = catalog.scan_inventory(bot)
            bot.close_shop()
            log.info("掃描完成：消耗欄裡有 %d 種道具", len(items))
            self.q.put(("inv_items", items))
        self._start_task("掃描背包", job)

    def _show_shop_items(self):
        for w in self.shop_frame.winfo_children():
            w.destroy()
        self.shop_choice: dict[int, tk.StringVar] = {}
        cols = 2
        for i, it in enumerate(self.shop_items):
            cell = ttk.Frame(self.shop_frame, padding=2, relief="groove")
            cell.grid(row=i // cols, column=i % cols, sticky="w", padx=2, pady=2)
            ttk.Label(cell, image=self._img(it.row_img, 300)).pack(side="left")
            v = tk.StringVar(value="")
            box = ttk.Frame(cell)
            box.pack(side="left", padx=4)
            ttk.Radiobutton(box, text="HP 補品", value="hp", variable=v,
                            command=lambda i=i: self._pick_buy(i, "hp")).pack(anchor="w")
            ttk.Radiobutton(box, text="MP 補品", value="mp", variable=v,
                            command=lambda i=i: self._pick_buy(i, "mp")).pack(anchor="w")
            ttk.Radiobutton(box, text="不買", value="", variable=v,
                            command=lambda i=i: self._pick_buy(i, "")).pack(anchor="w")
            self.shop_choice[i] = v

    def _pick_buy(self, i, kind):
        # 同一種（HP/MP）只能選一個商品
        for j, v in self.shop_choice.items():
            if j != i and v.get() == kind and kind:
                v.set("")

    def apply_buy(self):
        if not self.shop_items:
            messagebox.showinfo(APP_TITLE, "請先按「掃描妙妙商店」")
            return
        picks = {v.get(): i for i, v in self.shop_choice.items() if v.get()}
        if "hp" not in picks:
            if not messagebox.askyesno(APP_TITLE, "沒有選 HP 補品，確定嗎？"):
                return
        try:
            for kind in ("hp", "mp"):
                v = self.buy_vars[kind]
                if kind in picks:
                    setup_logic.apply_buy(self.cfg, kind, self.shop_items[picks[kind]], self.shop_items,
                                          v["name"].get().strip(), qs_to_region(v["qs"].get()),
                                          v["target"].get(), v["below"].get(), v["fallback"].get())
                else:
                    setup_logic.remove_buy(self.cfg, kind)
            settings.save(self.cfg)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"儲存失敗：{e}")
            return
        self._refresh_setup_status()
        messagebox.showinfo(APP_TITLE, "補品設定好了！\n\n" + setup_logic.summary(self.cfg))

    def _show_inv_items(self):
        for w in self.inv_frame.winfo_children():
            w.destroy()
        self.inv_vars = []
        for i, it in enumerate(self.inv_items):
            cell = ttk.Frame(self.inv_frame, padding=2, relief="groove")
            cell.grid(row=i, column=0, sticky="w", pady=2)
            ttk.Label(cell, image=self._img(it.row_img, 300)).pack(side="left")
            box = ttk.Frame(cell)
            box.pack(side="left", padx=6)
            v_sell = tk.BooleanVar(value=False)
            v_name = tk.StringVar(value="")
            v_keep = tk.IntVar(value=0)
            ttk.Checkbutton(box, text="要賣", variable=v_sell).grid(row=0, column=0, sticky="w")
            ttk.Label(box, text=f"背包裡約 {it.count} 組").grid(row=0, column=1, sticky="w", padx=6)
            ttk.Label(box, text="名稱").grid(row=1, column=0, sticky="w")
            ttk.Entry(box, textvariable=v_name, width=12).grid(row=1, column=1, sticky="w")
            ttk.Label(box, text="保留幾組").grid(row=2, column=0, sticky="w")
            ttk.Spinbox(box, textvariable=v_keep, from_=0, to=99, width=5).grid(row=2, column=1, sticky="w")
            self.inv_vars.append((it, v_sell, v_name, v_keep))

    def apply_sell(self):
        if not self.inv_items:
            messagebox.showinfo(APP_TITLE, "請先按「掃描背包」")
            return
        picks = [(it, n.get().strip(), int(k.get() or 0)) for it, s, n, k in self.inv_vars if s.get()]
        # 不能把要買的補品也設成要賣
        from .vision import find
        cal = calib.Calibration.load()
        for it, name, _ in picks:
            for b in (self.cfg.get("buy") or {}).values():
                if cal.has_template(b["template"]) and find(it.row_img, cal.template(b["template"]), 0.85,
                                                            robust=True) is not None:
                    messagebox.showerror(APP_TITLE, f"「{name or '這個道具'}」好像就是你要買的補品「{b['name']}」，不能設成要賣")
                    return
        setup_logic.apply_sell(self.cfg, picks, self.inv_items)
        settings.save(self.cfg)
        self._refresh_setup_status()
        messagebox.showinfo(APP_TITLE, f"好了，之後每次補貨會順便賣掉 {len(picks)} 種道具" if picks else "已清除：不賣任何東西")

    def save_other(self):
        sf = self.cfg["safety"]
        try:
            sf["timer_refill_hours"] = float(self.var_timer.get())
            sf["hp_emergency"] = max(0.05, min(0.9, int(self.var_hpem.get()) / 100))
            lv = self.var_lv.get().strip()
            self.cfg["level"] = int(lv) if lv else None
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"數字格式不對：{e}")
            return
        self.cfg["ocr_enabled"] = bool(self.var_ocr.get())
        self.cfg.setdefault("notify", {})["discord_webhook"] = self.var_hook.get().strip()
        self.cfg["window_title"] = self.var_title.get().strip() or "MapleStory Worlds"
        settings.save(self.cfg)
        self._refresh_setup_status()
        messagebox.showinfo(APP_TITLE, "已儲存")

    # ================= 監控 =================
    def _can_start(self) -> bool:
        p = settings.problems(self.cfg)
        if p:
            messagebox.showwarning(APP_TITLE, "還不能開始：\n\n" + "\n".join("・" + x for x in p))
            self.nb.select(1)
            return False
        return True

    def start_monitor(self):
        if not self._can_start():
            return

        def job():
            from .app import Runner
            win = self._game()
            self.runner = Runner(self.cfg, win)
            self.runner.run()
        if self._start_task("監控", job):
            log.info("開始監控（按「停止」或 %s 結束）", self.cfg.get("hotkeys", {}).get("quit", "f12").upper())

    def toggle_pause(self):
        if self.runner is not None and self._busy() and self.task_name == "監控":
            self.runner.paused = not self.runner.paused
            log.info("已暫停" if self.runner.paused else "繼續監控")

    def stop(self):
        if self.runner is not None and self._busy() and self.task_name == "監控":
            self.runner.quit = True
            log.info("停止中…")
        elif self._busy():
            messagebox.showinfo(APP_TITLE, f"「{self.task_name}」做完就會停（要馬上停：把滑鼠甩到螢幕左上角）")

    def refill_now(self):
        if self.runner is not None and self._busy() and self.task_name == "監控":
            self.runner.force_refill = True
            log.info("馬上補貨")
        else:
            messagebox.showinfo(APP_TITLE, "要先「開始監控」")

    def dry_run(self):
        if not self._can_start():
            return

        def job():
            from .app import Runner
            win = self._game()
            r = Runner(self.cfg, win, dry_run=True)
            ok = r.refill({k: None for k in self.cfg["buy"]}, "試跑（不會真的買賣）")
            log.info("試跑%s", "成功 ✅" if ok else "失敗（看上面的訊息）")
        self._start_task("試跑", job)


HELP_TEXT = f"""{APP_TITLE} v{VERSION}

這個程式做什麼
  掛機時幫你顧補品：快捷欄上的補品快用完、HP 一直很低、或每隔幾小時，就自動打開妙妙商店，
  （可選）把撿到不要的藥水賣掉，再把補品買回來。全部用「看畫面 + 模擬滑鼠鍵盤」完成，不讀也不改遊戲記憶體。

第一次使用
  1. 開遊戲、登入角色，遊戲視窗調成平常掛機的大小（之後不要再改大小）。現金欄裡要有妙妙。
  2. 「設定」→ 第 1 步：開始自動校正。約 1 分鐘，期間不要動滑鼠。
  3. 第 2 步：掃描妙妙商店 → 在要買的商品選「HP 補品 / MP 補品」→ 填快捷欄位置、數量 → 套用。
  4. （可選）第 3 步：掃描背包 → 勾要賣的東西 → 套用。
  5. 「監控」→ 先按「試跑一次」看整個流程（最後都按取消，不會真的買賣），沒問題再「開始監控」。

平常使用
  ・打開程式 → 開始監控。遊戲被別的視窗蓋住也能監控（背景截圖），不會一直把遊戲叫到前面。
  ・要補貨時，如果你正在用電腦（最近 60 秒有動滑鼠或在別的視窗打字），會等你停下來才補；HP 危險時直接補。
  ・熱鍵：F9 暫停/繼續、F10 立刻補貨、F12 停止。緊急停止：把滑鼠甩到螢幕左上角。

注意
  ・使用自動化程式違反大部分線上遊戲的使用條款，帳號可能被處分，請自行評估。
  ・防毒軟體有時會誤判這類程式，需要按「允許」。
  ・遊戲視窗大小改了、或介面位置變了，請重新做自動校正。
  ・執行紀錄在 exe 旁邊的 data/autopot.log，有問題可以把它和 data 資料夾裡的圖傳給作者。
"""


def main():
    # 一定要在 import pyautogui（window.py）之前設定 DPI 感知，介面才不會模糊、座標才不會跑掉
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    if "--selftest" in sys.argv:
        return selftest()
    root = tk.Tk()
    try:
        dpi = root.winfo_fpixels("1i")
        root.tk.call("tk", "scaling", dpi / 72.0)
        w, h = int(root.winfo_screenwidth() * 0.45), int(root.winfo_screenheight() * 0.75)
        root.geometry(f"{max(800, w)}x{max(600, h)}")
    except Exception:
        pass
    App(root)
    root.mainloop()


def selftest() -> int:
    """打包後的檢查（GitHub 自動打包時跑）：所有模組都載入得了、介面建得起來、內建的參考圖都在。"""
    out = []
    ok = True
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        import yaml  # noqa: F401
        from . import app, autocal, catalog, qsdigits, shop  # noqa: F401
        if sys.platform == "win32":
            from . import window  # noqa: F401
            import keyboard  # noqa: F401
        out.append("modules ok")
        refs = list((calib.ASSETS / "refs").glob("*.png"))
        out.append(f"refs {len(refs)}")
        ok &= len(refs) >= 15 and (calib.ASSETS / "refs" / "refs.json").exists()
        root = tk.Tk()
        root.withdraw()
        a = App(root)
        root.update()
        out.append(f"gui ok ({len(a.buy_vars)} buy rows)")
        root.destroy()
    except Exception as e:
        import traceback
        out.append("FAIL " + repr(e))
        out.append(traceback.format_exc())
        ok = False
    out.append("SELFTEST " + ("PASS" if ok else "FAIL"))
    try:
        (calib.ROOT / "selftest.txt").write_text("\n".join(out), encoding="utf-8")
    except Exception:
        pass
    print("\n".join(out))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
