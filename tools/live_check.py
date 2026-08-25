r"""實機驗證台——對著真正的遊戲畫面，逐個子系統報出可信度數字。

為什麼需要這個：這個專案反覆出現同一種失敗——離線測試全綠、實跑卻是壞的。
原因是測試驗的是「我以為的畫面」。實測打臉紀錄：
  - 名牌定位模板其實是一張草叢截圖，0/27 幀
  - 傷害偵測用的桃紅色帶 100% 是雜訊（抓到別人的技能特效）
  - 動靜偵測的左右判斷等同擲硬幣
  - 平台寬度量測被繪圖縫切碎
每一項都是離線測試看不出來、對著真畫面跑一次就現形。

用法（遊戲要開著；本工具**只讀畫面，不送任何按鍵**）：
    .venv\Scripts\python.exe tools\live_check.py            # 觀察 30 秒
    .venv\Scripts\python.exe tools\live_check.py --seconds 120
    .venv\Scripts\python.exe tools\live_check.py --save logs\frames   # 順便存畫面
"""
import argparse
import os
import statistics
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def verdict(ratio, good=0.85, ok=0.5):
    return "綠" if ratio >= good else ("黃" if ratio >= ok else "紅")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--interval", type=float, default=0.30)
    p.add_argument("--save", help="順便把畫面存到這個資料夾（除錯用）")
    args = p.parse_args(argv)

    import cv2
    import yaml
    from src.capture import ScreenCapture
    from src.vision import (DamageWatcher, PlayerLocator, find_damage,
                            MinimapLocator, PlayerTracker, find_bars_pair,
                            exp_text_roi_from_bars, region_changed)
    from src.vision.modal import ModalWatcher

    with open(os.path.join(ROOT, "config", "settings.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    canon = tuple(cfg["vision"]["canonical_size"])
    cap = ScreenCapture(backend="mss", window_title=cfg["window"]["title"])
    if cap.grab() is None:
        print("[錯誤] 抓不到畫面。遊戲開著嗎？")
        return 1

    loc = PlayerLocator(root=ROOT)
    dmg = DamageWatcher()
    mm = MinimapLocator(cfg["vision"]["minimap"])
    tracker = PlayerTracker()
    # 設定檔的鍵名和 find_modal 的參數名不同（max_center_x vs max_cx），
    # 必須明確對應——直接展開會 TypeError，或更糟：被當成未知參數靜默忽略。
    _md = cfg["vision"].get("modal") or {}
    modal = ModalWatcher(persist=_md.get("persist", 3),
                         min_area_frac=_md.get("min_area_frac", 0.045),
                         max_cx=_md.get("max_center_x", 0.16),
                         max_cy=_md.get("max_center_y", 0.20))
    if args.save:
        os.makedirs(args.save, exist_ok=True)

    # 模擬「判斷閘」的決定：不送任何按鍵，只印出它此刻會不會持續開火。
    # 這是使用者最想知道的事——腳本現在到底會不會朝空氣射。
    HIT_MEMORY = 1.2
    gate = Counter()
    last_dealt = [None]

    ok = Counter()
    n = 0
    ms = []
    widths = []
    mine = others = taken = 0
    feet = []
    exp_prev = [None]
    exp_hits = 0
    print(f"觀察 {args.seconds:.0f} 秒——只讀畫面，不送任何按鍵。你照常玩。\n")
    t0 = time.time()
    while time.time() - t0 < args.seconds:
        raw = cap.grab()
        if raw is None:
            continue
        c0 = time.perf_counter()
        fr = cv2.resize(raw, canon, interpolation=cv2.INTER_AREA) \
            if (raw.shape[1], raw.shape[0]) != canon else raw
        n += 1
        if args.save:
            cv2.imwrite(os.path.join(args.save, "%04d.png" % n), fr)

        # 角色定位（同層判定與傷害歸屬的基礎）
        pos = loc.find(fr)
        ok["角色定位"] += pos is not None
        if pos:
            feet.append(pos[1])

        # 傷害：自己的 vs 別人的
        groups = find_damage(fr)
        m = dmg.update(fr, time.time(), pos)
        mine += len(m)
        others += sum(1 for g in groups if g.kind == "dealt") - len(m)
        taken += sum(1 for g in groups if g.kind == "taken")

        # 判斷閘會怎麼決定（不送按鍵，只記錄）
        if m or (dmg.last_taken is not None
                 and time.time() - dmg.last_taken < 1e-6):
            last_dealt[0] = time.time()
        open_now = (last_dealt[0] is not None
                    and time.time() - last_dealt[0] <= HIT_MEMORY)
        gate["開" if open_now else "關"] += 1

        # UI 校準與 EXP 進帳
        hp, mp = find_bars_pair(raw)
        ok["HP 條"] += hp is not None
        ok["MP 條"] += mp is not None
        er = exp_text_roi_from_bars(raw, hp, mp) if hp else None
        ok["EXP 區"] += er is not None
        if er:
            x, y, w, h = (int(v) for v in er)
            crop = raw[y:y + h, x:x + w].astype("int16")
            changed, exp_prev[0] = region_changed(exp_prev[0], crop)
            exp_hits += bool(changed)

        # 小地圖與平台
        pt = tracker.update(mm.locate_player_candidates(raw))
        ok["小地圖玩家"] += pt is not None
        span = mm.platform_span(raw, pt) if pt else None
        ok["腳下平台"] += span is not None
        if span:
            widths.append(float(span["width"]))

        # 彈窗（測謊）誤報——誤報會讓整夜停擺
        ok["彈窗誤報"] += bool(modal.update(fr))

        ms.append((time.perf_counter() - c0) * 1000)
        time.sleep(max(0.0, args.interval - (time.perf_counter() - c0)))
    cap.close()

    dur = time.time() - t0
    print(f"=== 實機可信度（{n} 幀 / {dur:.0f} 秒，平均 "
          f"{statistics.mean(ms):.0f} ms/幀，最慢 {max(ms):.0f} ms）===\n")
    rows = [
        ("角色定位", ok["角色定位"] / n,
         "找不到角色 → 同層判定與傷害歸屬全失效，腳本會退回照打（空揮）。"
         "換過帽子就跑 tools/grab_helmet.py 重截"),
        ("HP 條", ok["HP 條"] / n, "讀不到血量 → 核心進安全模式"),
        ("MP 條", ok["MP 條"] / n, "MP 低於 HP 長度 8% 時本來就找不到，黃燈可接受"),
        ("EXP 區", ok["EXP 區"] / n, "定位不到 → 進帳偵測失效，停滯 watchdog 會瞎掉"),
        ("小地圖玩家", ok["小地圖玩家"] / n,
         "找不到黃點 → 防掉落失效。小地圖被 UI 視窗蓋住是最常見原因"),
        ("腳下平台", ok["腳下平台"] / n, "量不到平台 → 不敢移動"),
    ]
    for name, r, hint in rows:
        v = verdict(r)
        print(f"  [{v}] {name:10s} {r:5.0%}" + ("" if v == "綠" else f"   ← {hint}"))
    fp = ok["彈窗誤報"] / n
    print(f"  [{'綠' if fp < 0.05 else '紅'}] {'彈窗誤報':10s} {fp:5.0%}"
          + ("" if fp < 0.05 else "   ← 把 UI 當成測謊彈窗，會整夜停手"))

    print()
    if feet:
        print(f"  角色腳底 y：中位數 {statistics.median(feet):.0f}"
              f"（{min(feet)}~{max(feet)}）；畫面中央 y={canon[1] // 2}"
              f" → 差 {abs(statistics.median(feet) - canon[1] // 2):.0f}px")
    if widths:
        print(f"  平台寬度：中位數 {statistics.median(widths):.0f}"
              f"（{min(widths):.0f}~{max(widths):.0f}）")
    print(f"  傷害數字：自己 {mine} 組、其他玩家 {others} 組、自己被打 {taken} 組")
    if mine + others:
        print(f"    → 這張圖 {others / (mine + others):5.0%} 的傷害數字不是你打的。"
              f"沒有角色定位就會全部算成自己的 → 一路空揮")
    print(f"  EXP 進帳：{exp_hits} 次（{exp_hits / max(dur, 1) * 3600:.0f} 次/小時）")
    tot = gate["開"] + gate["關"]
    if tot:
        pct_on = gate["開"] / tot
        print()
        print(f"  判斷閘（模擬，不含暖機與試打）：持續開火 {pct_on:.0%}、"
              f"停火 {1 - pct_on:.0%}")
        print("    停火期間腳本只會每 2 秒試打 2 下並走動。停火比例越高，"
              "以前浪費掉的攻擊就越多。")
    print("\n  註：本工具只讀畫面。要驗證按鍵送不送得進遊戲，"
          "用 tools/run_snake_sweep_admin.bat。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
