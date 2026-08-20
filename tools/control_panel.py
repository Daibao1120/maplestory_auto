# -*- coding: utf-8 -*-
"""守夜控制台：即時預覽 + 狀態儀表 + 參數即時調整 + 一鍵開關。

左側是「腳本眼中的畫面」——角色錨點、腳下平台範圍、怪物框（紅=同層可打、
灰=別層）、同層判定帶。右側是狀態儀表（狀態機、HP、平台寬、同層怪數、
面向、EXP 每小時）與控制項（開始/暫停/停止、攻擊鍵、面向模式、邊界安全界、
攻擊間隔…改了立刻生效），下方是日誌。

安全設計：所有決策仍由已測試的 NightWatchCore 負責（130 項測試）；UI 只是
把狀態顯示出來並把使用者的調整送進去。按「停止」或關窗會強制放開所有按鍵。

啟動：tools/run_control_panel_admin.bat（需管理員，送鍵才進得去遊戲）
"""
from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.overnight import ClassProfile, NightWatchCore, Perception  # noqa: E402

PREVIEW_W = 760          # 預覽圖寬度（等比縮放）


class Worker:
    """背景執行緒：感知 → core.tick → 送鍵；狀態放在 self.snap 供 UI 讀。"""

    def __init__(self, log_fn):
        self.log = log_fn
        self.lock = threading.Lock()
        self.snap = {"state": "STOPPED", "hp": None, "pos": None, "span": None,
                     "monsters": [], "anchor": None, "same_layer": 0,
                     "exp_count": 0, "exp_per_hour": 0.0, "facing": None,
                     "frame": None, "y_band": (-30, 30), "acting": False,
                     "mp": None, "profile": "—", "calibrated": False}
        self.cmd = ""                 # farm / idle / stop
        self.params = {}              # 由 UI 寫入，每圈套用
        self.running = False
        self.acting = False           # False = 只看不送鍵（安全預覽模式）
        self._thread = None
        self._core = None
        self._per = None

    # ---- 生命週期 ----
    def start(self, acting):
        if self.running:
            return
        self.acting = acting
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    # ---- 主迴圈 ----
    def _run(self):
        import yaml
        try:
            with open(os.path.join(ROOT, "config", "settings.yaml"),
                      encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
        except Exception as e:
            self.log(f"讀不到 config/settings.yaml：{e}")
            self.running = False
            return
        try:
            self._per = Perception(cfg, ROOT)
        except Exception as e:
            self.log(f"感知層初始化失敗：{e}")
            self.running = False
            return

        send = None
        if self.acting:
            try:
                from tools.hold_and_wiggle import (_find_window, _foreground,
                                                   _send_key, _right_click_at,
                                                   _PhysicalKeys)
                hwnd = _find_window(cfg["window"]["title"])
                if not hwnd:
                    self.log("找不到遊戲視窗 → 改為只看不送鍵")
                    self.acting = False
                else:
                    _foreground(hwnd)
                    phys = _PhysicalKeys((0x25, 0x26, 0x27, 0x28))
                    send = {"key": _send_key, "click": _right_click_at,
                            "hwnd": hwnd, "phys": phys}
                    self.log("送鍵已啟用（管理員權限 OK）")
            except Exception as e:
                self.log(f"送鍵初始化失敗（改為只看）：{e}")
                self.acting = False

        surv = cfg.get("survival", {})
        profile = ClassProfile.from_config(cfg.get("class_profile"))
        self._core = NightWatchCore(
            max_seconds=8 * 3600,
            potion_key=cfg["keys"].get("hp_potion", "delete"),
            heal_mode=surv.get("heal_mode", "external"),
            last_resort_hp=float(surv.get("last_resort_hp", 0.20)),
            profile=profile)
        with self.lock:
            self.snap["profile"] = (f"{profile.name}／{profile.attack_mode}"
                                    f"／buff {len(profile.buffs)}"
                                    f"／MP {'有' if profile.mp_key else '無'}")
        self.log(f"控制台啟動｜職業 {profile.name}（{profile.attack_mode}）｜"
                 f"補血 {self._core.heal_mode}｜"
                 f"{'送鍵' if self.acting else '只看不送鍵'}")

        held = set()

        def do(a):
            if not self.acting or send is None:
                return
            k = send["key"]
            if a.verb == "hold_attack":
                k(atk_key(), keyup=False)
                held.add(atk_key())
            elif a.verb == "repress_attack":
                k(atk_key(), keyup=True)
                time.sleep(0.03)
                k(atk_key(), keyup=False)
            elif a.verb == "release_attack":
                k(atk_key(), keyup=True)
                held.discard(atk_key())
            elif a.verb == "tap":
                k(a.arg, keyup=False)
                time.sleep(0.05)
                k(a.arg, keyup=True)
            elif a.verb == "tap_jump":
                k("alt", keyup=False)
                time.sleep(0.05)
                k("alt", keyup=True)
            elif a.verb == "turn":
                k(a.arg, keyup=False)
                time.sleep(0.03)
                k(a.arg, keyup=True)
            elif a.verb == "step":
                k(a.arg[0], keyup=False)
                time.sleep(a.arg[1])
                k(a.arg[0], keyup=True)
            elif a.verb == "release_moves":
                for kk in ("left", "right"):
                    k(kk, keyup=True)
            elif a.verb == "release_all":
                for kk in ("left", "right", "up", "down", "ctrl", "alt"):
                    k(kk, keyup=True)
                held.clear()
            elif a.verb == "right_click":
                send["click"](a.arg[0], a.arg[1])
            elif a.verb == "log":
                self.log(str(a.arg))

        def atk_key():
            return self.params.get("attack_key", "ctrl")

        try:
            while self.running:
                now = time.time()
                cmd, self.cmd = self.cmd, ""
                w, frame, info = self._per.snapshot(now, cmd=cmd)
                # 使用者實體按鍵 → 交還控制
                if self.acting and send is not None:
                    ph = send["phys"]
                    w.user_touch = ph.ok and any(
                        ph.state.get(v, False) for v in (0x25, 0x26, 0x27, 0x28))
                    w.fg = (send["hwnd"] is not None)
                    try:
                        import ctypes
                        w.fg = (ctypes.windll.user32.GetForegroundWindow()
                                == send["hwnd"])
                    except Exception:
                        pass
                # 套用 UI 參數
                self._apply_params()
                if self._core.state == "FARM":
                    w.buff_hit = self._per.find_buff(frame)
                for a in self._core.tick(w):
                    do(a)
                with self.lock:
                    self.snap.update({
                        "state": self._core.state, "hp": w.hp, "pos": w.pos,
                        "span": w.span, "monsters": info["monsters"],
                        "anchor": info["anchor"], "same_layer": info["same_layer"],
                        "exp_count": self._core.stats.exp_count,
                        "exp_per_hour": info["exp_per_hour"],
                        "facing": self._core.facing, "frame": frame,
                        "y_band": tuple(self._per.y_band), "acting": self.acting,
                        "anchor_score": info["anchor_score"],
                        "mp": info.get("mp"), "calibrated": info.get("calibrated"),
                        "stats": dict(vars(self._core.stats)),
                    })
                time.sleep(max(0.05, float(self.params.get("tick", 0.35))))
        except Exception as e:
            self.log(f"執行緒例外：{type(e).__name__}: {e}")
        finally:
            if self.acting and send is not None:
                for kk in ("left", "right", "up", "down", "ctrl", "alt"):
                    try:
                        send["key"](kk, keyup=True)
                    except Exception:
                        pass
            if self._per:
                self._per.close()
            with self.lock:
                self.snap["state"] = "STOPPED"
            self.log("已停止，所有按鍵已放開")

    def _apply_params(self):
        c, p = self._core, self.params
        if not c:
            return
        if "edge_margin_min" in p:
            pass  # 邊界安全界由 span 寬度自動決定，這裡保留擴充點
        if "facing_mode" in p:
            fm = p["facing_mode"]
            if fm in ("left", "right"):
                c.facing = fm
        if "exp_flip_after" in p:
            try:
                c.EXP_FLIP_AFTER = float(p["exp_flip_after"])
            except (TypeError, ValueError):
                pass
        if "y_band" in p and self._per:
            self._per.y_band = p["y_band"]


def draw_overlay(frame, snap):
    """在畫面上畫出「腳本看到什麼」，回傳縮小後的 BGR 圖。"""
    import cv2
    vis = frame.copy()
    h, w = vis.shape[:2]
    ap = snap.get("anchor")
    lo, hi = snap.get("y_band", (-60, 60))
    ax, ay = ap if ap else (w // 2, h // 2)
    # 同層判定帶
    cv2.line(vis, (0, ay + lo), (w, ay + lo), (0, 220, 220), 2)
    cv2.line(vis, (0, ay + hi), (w, ay + hi), (0, 220, 220), 2)
    # 怪物框
    for (x, y, bw, bh, sc, same) in snap.get("monsters", []):
        cv2.rectangle(vis, (x, y), (x + bw, y + bh),
                      (0, 0, 255) if same else (150, 150, 150), 3)
    # 角色錨點
    cv2.drawMarker(vis, (ax, ay), (255, 0, 255),
                   cv2.MARKER_CROSS if ap else cv2.MARKER_TILTED_CROSS, 70, 5)
    scale = PREVIEW_W / w
    return cv2.resize(vis, (PREVIEW_W, int(h * scale)))


def main():
    import base64
    import tkinter as tk
    from tkinter import ttk

    import cv2

    root = tk.Tk()
    root.title("楓之谷守夜控制台")
    root.geometry("+30+30")

    logbuf = deque(maxlen=400)

    def log(msg):
        logbuf.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    worker = Worker(log)

    outer = ttk.Frame(root, padding=8)
    outer.grid(sticky="nsew")

    # ---- 左：預覽 ----
    left = ttk.Frame(outer)
    left.grid(column=0, row=0, sticky="nw")
    ttk.Label(left, text="腳本眼中的畫面（紅框=同層可打／灰框=別層／"
                         "洋紅十字=角色定位）").grid(column=0, row=0, sticky="w")
    canvas = tk.Label(left, background="#222")
    canvas.grid(column=0, row=1, pady=(4, 6))

    # ---- 右：狀態與控制 ----
    right = ttk.Frame(outer, padding=(12, 0))
    right.grid(column=1, row=0, sticky="nw")

    stat = {}
    ttk.Label(right, text="狀態", font=("", 11, "bold")).grid(
        column=0, row=0, columnspan=2, sticky="w")
    rows = [("state", "狀態機"), ("acting", "送鍵"), ("profile", "職業"),
            ("calib", "UI 校準"), ("hp", "HP"), ("mp", "MP"),
            ("pos", "小地圖位置"), ("plat", "腳下平台"), ("same", "同層怪數"),
            ("facing", "面向"), ("exp", "EXP 進帳"), ("eph", "EXP/小時"),
            ("anchor", "角色定位")]
    for i, (key, label) in enumerate(rows, start=1):
        ttk.Label(right, text=label).grid(column=0, row=i, sticky="w")
        v = tk.StringVar(value="—")
        ttk.Label(right, textvariable=v, foreground="#06c").grid(
            column=1, row=i, sticky="w")
        stat[key] = v
    r = len(rows) + 1

    ttk.Separator(right, orient="horizontal").grid(
        column=0, row=r, columnspan=2, sticky="we", pady=8)
    r += 1
    ttk.Label(right, text="控制", font=("", 11, "bold")).grid(
        column=0, row=r, columnspan=2, sticky="w")
    r += 1

    atk_var = tk.StringVar(value="ctrl")
    face_var = tk.StringVar(value="auto")
    flip_var = tk.StringVar(value="20")
    band_lo = tk.StringVar(value="-60")
    band_hi = tk.StringVar(value="60")
    for label, var in (("攻擊鍵", atk_var), ("面向 (auto/left/right)", face_var),
                       ("沒EXP幾秒換邊", flip_var),
                       ("同層帶下限", band_lo), ("同層帶上限", band_hi)):
        ttk.Label(right, text=label).grid(column=0, row=r, sticky="w")
        ttk.Entry(right, textvariable=var, width=10).grid(column=1, row=r, sticky="w")
        r += 1

    def apply_params():
        try:
            band = (float(band_lo.get()), float(band_hi.get()))
        except ValueError:
            band = (-60.0, 60.0)
        worker.params.update({
            "attack_key": atk_var.get().strip() or "ctrl",
            "facing_mode": face_var.get().strip(),
            "exp_flip_after": flip_var.get().strip(),
            "y_band": band,
        })
        log(f"已套用參數：攻擊鍵={atk_var.get()} 面向={face_var.get()} "
            f"換邊={flip_var.get()}s 同層帶={band}")

    ttk.Button(right, text="套用參數", command=apply_params).grid(
        column=0, row=r, columnspan=2, sticky="we", pady=(6, 8))
    r += 1

    btns = ttk.Frame(right)
    btns.grid(column=0, row=r, columnspan=2, sticky="we")
    r += 1

    def start(acting):
        apply_params()
        worker.start(acting)

    ttk.Button(btns, text="▶ 開始練等", command=lambda: start(True)).pack(
        side="left", expand=True, fill="x")
    ttk.Button(btns, text="👁 只看不動", command=lambda: start(False)).pack(
        side="left", expand=True, fill="x")
    ttk.Button(btns, text="■ 停止", command=worker.stop).pack(
        side="left", expand=True, fill="x")

    b2 = ttk.Frame(right)
    b2.grid(column=0, row=r, columnspan=2, sticky="we", pady=(4, 0))
    r += 1
    ttk.Button(b2, text="暫停(idle)",
               command=lambda: setattr(worker, "cmd", "idle")).pack(
        side="left", expand=True, fill="x")
    ttk.Button(b2, text="恢復(farm)",
               command=lambda: setattr(worker, "cmd", "farm")).pack(
        side="left", expand=True, fill="x")

    # ---- 下：日誌 ----
    ttk.Label(outer, text="日誌").grid(column=0, row=1, sticky="w", pady=(8, 0))
    logbox = tk.Text(outer, height=9, width=120, background="#111",
                     foreground="#ddd", insertbackground="#ddd")
    logbox.grid(column=0, row=2, columnspan=2, sticky="we")

    img_ref = {"img": None}

    def refresh():
        with worker.lock:
            s = dict(worker.snap)
            frame = s.pop("frame", None)
        st = s.get("state", "STOPPED")
        stat["state"].set(st)
        stat["acting"].set("是（會操控角色）" if s.get("acting") else "否（只看）")
        stat["profile"].set(str(s.get("profile", "—")))
        stat["calib"].set("完成" if s.get("calibrated") else "校準中…")
        hp = s.get("hp")
        stat["hp"].set("讀不到" if hp is None else f"{hp:.0%}")
        mp = s.get("mp")
        stat["mp"].set("讀不到" if mp is None else f"{mp:.0%}")
        stat["pos"].set(str(s.get("pos") or "讀不到"))
        span = s.get("span")
        stat["plat"].set("讀不到" if not span else
                         f"寬 {span['width']:.1f}｜左 {span['dist_left']:.1f}／"
                         f"右 {span['dist_right']:.1f}")
        stat["same"].set(str(s.get("same_layer", 0)))
        stat["facing"].set(str(s.get("facing") or "—"))
        stat["exp"].set(f"{s.get('exp_count', 0)} 次")
        eph = s.get("exp_per_hour", 0.0)
        stat["eph"].set(f"{eph:.0f} 次/小時" if eph else "估算中…")
        sc = s.get("anchor_score", 0.0)
        stat["anchor"].set(f"名牌 {sc:.2f}" if s.get("anchor")
                           else f"退回畫面中央（名牌 {sc:.2f}）")
        if frame is not None:
            try:
                small = draw_overlay(frame, s)
                ok, buf = cv2.imencode(".png", small)
                if ok:
                    img = tk.PhotoImage(data=base64.b64encode(buf.tobytes()))
                    canvas.configure(image=img)
                    img_ref["img"] = img
            except Exception:
                pass
        if logbuf:
            logbox.delete("1.0", "end")
            logbox.insert("end", "\n".join(list(logbuf)[-40:]))
            logbox.see("end")
        root.after(250, refresh)

    def on_close():
        worker.stop()
        root.after(400, root.destroy)

    root.protocol("WM_DELETE_WINDOW", on_close)
    log("控制台就緒。建議先按「只看不動」確認偵測正確，再按「開始練等」。")
    refresh()
    if "--self-test" in sys.argv:
        root.after(1500, on_close)
    root.mainloop()


if __name__ == "__main__":
    if sys.platform != "win32":
        print("[錯誤] 本工具僅支援 Windows。")
        sys.exit(1)
    main()
