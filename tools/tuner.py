# -*- coding: utf-8 -*-
"""掛機調參 UI（Tkinter）：邊跑邊調參數＋按鍵記錄量平台＋偵測預覽。

三個區塊：
1. 參數：攻擊間隔／挪步間隔／步伐秒數／邊界安全界…改完按「儲存」寫進
   config/tuning.yaml——正在跑的 hold_and_wiggle 每 2 秒自動套用，免重啟。
2. 量平台（按鍵記錄）：你手動在遊戲裡「從平台最左走到最右」，這裡同時記錄
   方向鍵按住時長與小地圖 x，推算出平台寬度、走路速度、「走多久會掉」，
   一鍵把安全上限套用到參數。
3. 偵測預覽：截一張畫面跑「小地圖人物定位／左右動靜量／邊緣探測」給你看
   偵測到底準不準。

需以「系統管理員」執行（遊戲提權後，讀鍵盤狀態會被 UIPI 擋掉）；
用 tools/run_tuner_admin.bat 啟動即可。僅供學習研究。
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TUNING_PATH = os.path.join(ROOT, "config", "tuning.yaml")

_user32 = ctypes.windll.user32 if sys.platform == "win32" else None
_VK = {"left": 0x25, "right": 0x27}

# 參數定義：(鍵名, 中文說明, 預設, 型別)
PARAMS = [
    ("attack_interval", "攻擊間隔（秒/下）", 0.22, float),
    ("interval_min", "挪步間隔下限（秒）", 40.0, float),
    ("interval_max", "挪步間隔上限（秒）", 55.0, float),
    ("move_time", "每步按方向鍵秒數（<0.1 只轉身不走）", 0.18, float),
    ("max_step_seconds", "每步秒數安全上限（量平台後自動算）", 0.6, float),
    ("edge_margin", "小地圖安全界（起始點 ± px）", 6, int),
    ("guard_interval", "位置警衛巡界間隔（秒；出界立即推回）", 2.5, float),
    ("swap_every", "每幾次挪步換邊（輪流模式）", 1, int),
    ("alternate_face", "挪步時輪流換邊 (1/0)", 0, int),
    ("smart_face", "哪邊怪多打哪邊 (1/0)", 0, int),
    ("dispel_interval", "buff 檢查間隔（秒）", 5.0, float),
]


def load_tuning(path=TUNING_PATH):
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def save_tuning(data, path=TUNING_PATH):
    import yaml
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 掛機調參檔（tools/tuner.py 產生；hold_and_wiggle 每 2 秒自動套用）\n")
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


# ============================================================
#  量平台：按鍵記錄 + 小地圖 x → 平台寬 / 走速 / 走多久會掉
# ============================================================

def analyze_walk_samples(samples, min_seg_seconds=0.25):
    """從 (時間, 按鍵方向或None, 小地圖x或None) 樣本推算平台幾何。

    - 平台寬度 = 過程中小地圖 x 的最大最小差（請真的走到兩端）。
    - 走速 = 每段「連續按住同方向」期間的 |Δx|/Δt，取中位數。
    - 走多久會掉 = 全寬/走速（從一端走到另一端），半寬 = 從中間走到邊。
    樣本不足回傳 None。
    """
    xs = [x for _t, _k, x in samples if x is not None]
    if len(xs) < 5:
        return None
    width = max(xs) - min(xs)

    speeds = []
    seg = None  # {"k", "points": [(t, x)...]}

    def _flush(s):
        if s and len(s["points"]) >= 2:
            (t0, x0), (t1, x1) = s["points"][0], s["points"][-1]
            dt, dx = t1 - t0, abs(x1 - x0)
            if dt >= min_seg_seconds and dx > 0:
                speeds.append(dx / dt)

    for t, k, x in samples:
        if k in ("left", "right"):
            if seg is None or seg["k"] != k:
                _flush(seg)   # 方向直接切換（沒放開）也要結算前一段
                seg = {"k": k, "points": []}
            if x is not None:
                seg["points"].append((t, x))
        else:
            _flush(seg)
            seg = None
    _flush(seg)  # 收尾

    if not speeds or width <= 0:
        return None
    speeds.sort()
    v = speeds[len(speeds) // 2]
    return {
        "width_px": round(width, 1),
        "speed_px_s": round(v, 2),
        "cross_seconds": round(width / v, 2),        # 一端走到另一端
        "half_seconds": round(width / 2 / v, 2),     # 中間走到邊（= 走多久會掉）
        "segments": len(speeds),
        "x_min": min(xs), "x_max": max(xs),
    }


def suggest_from_geometry(geo):
    """由平台幾何給建議值：安全界、每步秒數上限。"""
    if not geo:
        return {}
    return {
        # 安全界：平台半寬的 6 成（小地圖 px），至少 2
        "edge_margin": max(2, int(round(geo["width_px"] * 0.3))),
        # 每步上限：走到邊時間的 1/3——連走三步內都掉不下去
        "max_step_seconds": max(0.1, round(geo["half_seconds"] / 3, 2)),
    }


class PlatformRecorder:
    """背景執行緒：50Hz 記錄方向鍵按住狀態＋小地圖玩家 x。"""

    def __init__(self, window_keyword="新楓之谷"):
        self.window_keyword = window_keyword
        self.samples = []          # [(t, "left"/"right"/None, x或None), ...]
        self.status = "未開始"
        self.running = False
        self._thread = None
        self._cap = None
        self._mmloc = None
        self._tracker = None

    def _init_vision(self):
        from src.capture import ScreenCapture
        from src.vision import MinimapLocator, PlayerTracker
        import yaml
        cfgpath = os.path.join(ROOT, "config", "settings.yaml")
        if not os.path.exists(cfgpath):
            cfgpath = os.path.join(ROOT, "config", "settings.example.yaml")
        with open(cfgpath, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self._cap = ScreenCapture(backend="mss", window_title=self.window_keyword)
        self._mmloc = MinimapLocator(cfg["vision"]["minimap"])
        self._tracker = PlayerTracker()

    def start(self):
        if self.running:
            return
        self.samples = []
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def _pressed(self, name):
        return bool(_user32 and _user32.GetAsyncKeyState(_VK[name]) & 0x8000)

    def _run(self):
        try:
            self._init_vision()
        except Exception as e:
            self.status = f"辨識初始化失敗：{e}"
            self.running = False
            return
        self.status = "記錄中…請在遊戲裡從平台最左走到最右（可來回）"
        last_grab = 0.0
        x = None
        t0 = time.time()
        while self.running:
            now = time.time()
            key = ("left" if self._pressed("left")
                   else "right" if self._pressed("right") else None)
            if now - last_grab >= 0.12:      # 小地圖 x 約 8Hz 更新即可
                last_grab = now
                try:
                    frame = self._cap.grab()
                    pos = self._tracker.update(
                        self._mmloc.locate_player_candidates(frame))
                    x = pos[0] if pos else None
                except Exception:
                    x = None
            self.samples.append((now - t0, key, x))
            self.status = (f"記錄中 {now - t0:.0f}s｜按鍵:{key or '—'}｜"
                           f"小地圖x:{x if x is not None else '讀不到'}｜"
                           f"樣本:{len(self.samples)}")
            time.sleep(0.02)
        try:
            if self._cap:
                self._cap.close()
        except Exception:
            pass
        self.status = "已停止"


# ============================================================
#  偵測預覽：一鍵檢查人物定位 / 左右動靜 / 邊緣探測
# ============================================================

def detection_snapshot(window_keyword="新楓之谷"):
    """截一張畫面跑三種偵測，回傳文字報告。"""
    lines = []
    try:
        import numpy as np
        import yaml
        from src.capture import ScreenCapture
        from src.vision import MinimapLocator, PlayerTracker, probe_ahead_safe
        cfgpath = os.path.join(ROOT, "config", "settings.yaml")
        if not os.path.exists(cfgpath):
            cfgpath = os.path.join(ROOT, "config", "settings.example.yaml")
        with open(cfgpath, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cap = ScreenCapture(backend="mss", window_title=window_keyword)
        f1 = cap.grab()
        h, w = f1.shape[:2]
        lines.append(f"視窗畫面：{w}×{h}")

        # 1) 小地圖人物＋腳下平台
        mmloc = MinimapLocator(cfg["vision"]["minimap"])
        cands = mmloc.locate_player_candidates(f1)
        pos = PlayerTracker().update(cands)
        lines.append(f"小地圖人物：{pos if pos else '讀不到'}"
                     f"（候選 {len(cands)} 個：{cands[:3]}）")
        span = mmloc.platform_span(f1, pos) if pos else None
        if span:
            lines.append(f"腳下平台：寬 {span['width']:.0f}｜距左端 {span['dist_left']:.0f}"
                         f"｜距右端 {span['dist_right']:.0f}"
                         "（小地圖校準px，1px ≈ 37 畫面px）")
        else:
            lines.append("腳下平台：讀不到地形列（跳躍中/掉落中？）")

        # 2) 左右動靜（怪在哪邊）
        time.sleep(0.15)
        f2 = cap.grab()
        y0, y1 = int(h * 0.20), int(h * 0.85)
        cx, dead = w // 2, int(w * 0.08)
        diff = np.abs(f1[y0:y1].astype(np.int16) - f2[y0:y1].astype(np.int16)).max(axis=2)
        moving = diff > 20
        left, right = int(moving[:, :cx - dead].sum()), int(moving[:, cx + dead:].sum())
        side = ("左" if left > right * 1.25 else "右" if right > left * 1.25 else "差不多")
        lines.append(f"動靜量：左 {left}／右 {right} → 怪較多的邊：{side}")

        # 3) 邊緣探測（畫面第二道防線）
        ep = (cfg.get("vision", {}) or {}).get("edge_probe", {}) or {}
        anchor = (w // 2, h // 2)
        safe_l = probe_ahead_safe(f1, anchor, "left", ep)
        safe_r = probe_ahead_safe(f1, anchor, "right", ep)
        lines.append(f"邊緣探測（以畫面中央為角色）：往左 {'安全' if safe_l else '危險'}／"
                     f"往右 {'安全' if safe_r else '危險'}")
        cap.close()
    except Exception as e:
        lines.append(f"偵測失敗：{e}")
    return "\n".join(lines)


# ============================================================
#  Tkinter UI
# ============================================================

def main():
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.title("楓之谷掛機調參")
    root.attributes("-topmost", True)      # 蓋在遊戲上方便邊玩邊調
    root.geometry("+40+40")

    saved = load_tuning()
    entries = {}

    frm = ttk.Frame(root, padding=10)
    frm.grid(sticky="nsew")

    # ---- 區塊 1：參數 ----
    ttk.Label(frm, text="① 參數（儲存後，跑著的掛機工具 2 秒內自動套用）",
              font=("", 10, "bold")).grid(column=0, row=0, columnspan=2,
                                          sticky="w", pady=(0, 4))
    row = 1
    for key, label, default, cast in PARAMS:
        ttk.Label(frm, text=label).grid(column=0, row=row, sticky="w")
        val = saved.get(key, default)
        if isinstance(val, bool):   # 布林顯示成 1/0，存檔時才不會解析失敗
            val = int(val)
        var = tk.StringVar(value=str(val))
        ttk.Entry(frm, textvariable=var, width=10).grid(column=1, row=row, sticky="e")
        entries[key] = (var, cast)
        row += 1

    status_var = tk.StringVar(value="")

    def do_save():
        data = {}
        for key, (var, cast) in entries.items():
            try:
                v = cast(float(var.get()))
                data[key] = bool(v) if key in ("alternate_face", "smart_face") else v
            except ValueError:
                messagebox.showerror("格式錯誤", f"「{key}」不是數字：{var.get()}")
                return
        save_tuning(data)
        status_var.set(f"已儲存 {time.strftime('%H:%M:%S')}（掛機工具 2 秒內套用）")

    ttk.Button(frm, text="儲存並套用", command=do_save).grid(
        column=0, row=row, columnspan=2, sticky="we", pady=(4, 10))
    row += 1

    # ---- 區塊 2：量平台 ----
    ttk.Label(frm, text="② 量平台：按「開始」後到遊戲裡從平台最左走到最右（可來回幾趟）",
              font=("", 10, "bold")).grid(column=0, row=row, columnspan=2,
                                          sticky="w", pady=(0, 4))
    row += 1
    rec = PlatformRecorder()
    rec_status = tk.StringVar(value="未開始")
    result_var = tk.StringVar(value="")
    geo_holder = {"geo": None}

    def poll_status():
        rec_status.set(rec.status)
        root.after(200, poll_status)

    def start_rec():
        rec.start()

    def stop_rec():
        rec.stop()
        time.sleep(0.1)
        geo = analyze_walk_samples(rec.samples)
        geo_holder["geo"] = geo
        if not geo:
            result_var.set("樣本不足：請確認有按方向鍵走動、小地圖讀得到人物")
            return
        sug = suggest_from_geometry(geo)
        result_var.set(
            f"平台寬 {geo['width_px']} 小地圖px（x {geo['x_min']}~{geo['x_max']}）\n"
            f"走速 {geo['speed_px_s']} px/秒（{geo['segments']} 段取中位數）\n"
            f"⚠ 從中間走到邊 ≈ {geo['half_seconds']} 秒；走完全程 ≈ {geo['cross_seconds']} 秒\n"
            f"建議：edge_margin={sug['edge_margin']}、max_step_seconds={sug['max_step_seconds']}")

    def apply_sug():
        geo = geo_holder["geo"]
        if not geo:
            messagebox.showinfo("尚無結果", "請先量一次平台")
            return
        sug = suggest_from_geometry(geo)
        entries["edge_margin"][0].set(str(sug["edge_margin"]))
        entries["max_step_seconds"][0].set(str(sug["max_step_seconds"]))
        do_save()

    btns = ttk.Frame(frm)
    btns.grid(column=0, row=row, columnspan=2, sticky="we")
    ttk.Button(btns, text="開始量測", command=start_rec).pack(side="left", expand=True, fill="x")
    ttk.Button(btns, text="停止並計算", command=stop_rec).pack(side="left", expand=True, fill="x")
    ttk.Button(btns, text="套用建議", command=apply_sug).pack(side="left", expand=True, fill="x")
    row += 1
    ttk.Label(frm, textvariable=rec_status, foreground="#06c").grid(
        column=0, row=row, columnspan=2, sticky="w")
    row += 1
    ttk.Label(frm, textvariable=result_var, justify="left").grid(
        column=0, row=row, columnspan=2, sticky="w", pady=(0, 10))
    row += 1

    # ---- 區塊 3：偵測預覽 ----
    ttk.Label(frm, text="③ 偵測預覽", font=("", 10, "bold")).grid(
        column=0, row=row, columnspan=2, sticky="w", pady=(0, 4))
    row += 1
    det_var = tk.StringVar(value="")

    def snap():
        det_var.set("偵測中…")
        def work():
            report = detection_snapshot()
            root.after(0, det_var.set, report)
        threading.Thread(target=work, daemon=True).start()

    ttk.Button(frm, text="截圖分析（人物定位／動靜／邊緣）", command=snap).grid(
        column=0, row=row, columnspan=2, sticky="we")
    row += 1
    ttk.Label(frm, textvariable=det_var, justify="left").grid(
        column=0, row=row, columnspan=2, sticky="w")
    row += 1
    ttk.Label(frm, textvariable=status_var, foreground="#080").grid(
        column=0, row=row, columnspan=2, sticky="w", pady=(6, 0))

    poll_status()
    if "--self-test" in sys.argv:      # 建構驗證用：開 1.5 秒自動關
        root.after(1500, root.destroy)
    root.mainloop()


if __name__ == "__main__":
    if sys.platform != "win32":
        print("[錯誤] 本工具僅支援 Windows。")
        sys.exit(1)
    main()
