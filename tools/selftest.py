# -*- coding: utf-8 -*-
"""端到端整備度檢查：把所有元件串起來實跑（只看不送鍵），輸出健康報告。

為什麼要有這支：各元件都有單元測試，但「整套接起來在真實畫面上到底行不行」
是另一回事。這支跑真實迴圈但**完全不送按鍵**，統計每個子系統的讀取成功率、
迴圈速度、以及核心「會做什麼動作」，最後給每項綠/黃/紅判定。

用法：
    python tools/selftest.py            # 預設觀察 45 秒
    python tools/selftest.py --seconds 90
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Windows 終端機預設 cp950，中文報告會變亂碼
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def verdict(ok_ratio, good=0.9, warn=0.5):
    if ok_ratio >= good:
        return "綠"
    return "黃" if ok_ratio >= warn else "紅"


def run(seconds=45.0, cfg_path=None):
    import yaml
    from tools.overnight import ClassProfile, NightWatchCore, Perception

    cfg_path = cfg_path or os.path.join(ROOT, "config", "settings.yaml")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    per = Perception(cfg, ROOT)
    profile = ClassProfile.from_config(cfg.get("class_profile"))
    core = NightWatchCore(heal_mode=cfg.get("survival", {}).get("heal_mode", "self"),
                          profile=profile)

    n = 0
    ok = Counter()
    acts = Counter()
    states = Counter()
    widths, dists = [], []
    exp_events = 0
    logs = []            # 核心的狀態轉換理由——診斷「為什麼停手」的關鍵
    tick_ms = []
    t0 = time.time()
    print(f"觀察 {seconds:.0f} 秒（只看不送鍵）…職業={profile.name}／"
          f"{profile.attack_mode}")
    while time.time() - t0 < seconds:
        s = time.perf_counter()
        w, frame, info = per.snapshot(time.time())
        out = core.tick(w)
        tick_ms.append((time.perf_counter() - s) * 1000)
        n += 1
        ok["畫面"] += bool(w.frame_ok)
        ok["在遊戲中"] += bool(info.get("in_game", True))
        ok["UI 校準"] += bool(info.get("calibrated"))
        ok["HP"] += w.hp is not None
        ok["MP"] += w.mp is not None
        ok["小地圖玩家"] += w.pos is not None
        ok["腳下平台"] += w.span is not None
        if w.span:
            widths.append(float(w.span["width"]))
            dists.append((float(w.span["dist_left"]),
                          float(w.span["dist_right"])))
        ok["怪物模板"] += bool(info.get("monsters"))
        ok["活動偵測"] += bool((info.get("motion") or {}).get("ready"))
        ok["彈窗誤報"] += bool(info.get("modal"))
        ok["遇人"] += bool(info.get("others_near"))
        exp_events += bool(w.exp_changed)
        states[core.state] += 1
        for a in out:
            acts[a.verb] += 1
            if a.verb == "log":
                logs.append(f"{time.time() - t0:5.1f}s  {a.arg}")
        time.sleep(0.05)
    per.close()

    dur = time.time() - t0
    if ok["在遊戲中"] / max(1, n) < 0.5:
        print("\n=== 整備度報告 ===")
        print("  [紅] 目前不在遊戲畫面內（登入畫面／選頻道／斷線？）")
        print("       請先回到遊戲中再跑——其他讀值在此狀態下沒有意義。")
        print(f"  （{n} 圈中只有 {ok['在遊戲中']} 圈偵測到遊戲內 UI）")
        return 0
    print(f"\n=== 整備度報告（{n} 圈 / {dur:.0f} 秒，平均 "
          f"{sum(tick_ms)/max(1,len(tick_ms)):.0f} ms/圈）===")
    rows = [
        ("畫面擷取", ok["畫面"] / n, "抓不到畫面 → 遊戲沒開或視窗認錯"),
        ("UI 自動校準", ok["UI 校準"] / n, "找不到血條/小地圖 → 檢查遊戲 UI 是否被遮住"),
        ("HP 讀值", ok["HP"] / n, "讀不到血量"),
        ("MP 讀值", ok["MP"] / n, "讀不到魔力（法師必要）"),
        ("小地圖定位", ok["小地圖玩家"] / n, "找不到角色黃點 → 小地圖要開啟"),
        ("腳下平台", ok["腳下平台"] / n, "量不到平台 → 防掉落會保守停手"),
        ("活動偵測", ok["活動偵測"] / n, "背景模型未就緒或鏡頭一直在動"),
    ]
    for name, ratio, hint in rows:
        v = verdict(ratio)
        note = "" if v == "綠" else f"  ← {hint}"
        print(f"  [{v}] {name:12s} {ratio:5.0%}{note}")
    tmpl = ok["怪物模板"] / n
    print(f"  [{'綠' if tmpl > 0.2 else '黃'}] {'怪物模板':12s} {tmpl:5.0%}"
          f"{'' if tmpl > 0.2 else '  ← 這張圖的怪沒有模板（改用活動偵測後備）'}")
    fp = ok["彈窗誤報"] / n
    print(f"  [{'綠' if fp < 0.05 else '紅'}] {'彈窗偵測':12s} 誤報 {fp:5.0%}"
          f"{'' if fp < 0.05 else '  ← 誤把 UI 當彈窗，會一直停手'}")
    # 平台寬度：核心要求 width >= WIDE_ENOUGH 才開打。這個門檻是寫死的常數，
    # 而 width 的單位取決於小地圖面板實際多大（校準後就是原始像素）。
    # 不把實測值印出來，「為什麼整夜都沒開打」根本無從判斷。
    if widths:
        sw = sorted(widths)
        med = sw[len(sw) // 2]
        passing = sum(1 for x in widths if x >= core.WIDE_ENOUGH) / len(widths)
        v = "綠" if passing > 0.8 else ("黃" if passing > 0.3 else "紅")
        print(f"  [{v}] {'平台寬度':12s} 中位數 {med:.0f}"
              f"（{min(widths):.0f}~{max(widths):.0f}），"
              f"{passing:.0%} 的圈數 >= 開打門檻 {core.WIDE_ENOUGH:.0f}")
        if passing <= 0.3:
            print("       ← 量到的平台太窄，核心永遠不會進入 FARM。"
                  "若你用調參 UI 量到的寬度明顯大於這個數字，就是量測被切碎了。")
        dl = sorted(d[0] for d in dists)[len(dists) // 2]
        dr = sorted(d[1] for d in dists)[len(dists) // 2]
        print(f"       距左緣 {dl:.0f} / 距右緣 {dr:.0f}（下降時會往較近的一側走）")
    else:
        print(f"  [紅] {'平台寬度':12s} 一圈都沒量到 → 核心不會開打")
    print(f"\n  EXP 進帳：{exp_events} 次"
          f"（{exp_events / max(dur, 1) * 3600:.0f} 次/小時）")
    print(f"  狀態分佈：{dict(states)}")
    st = {k: v for k, v in vars(core.stats).items() if v}
    print(f"  核心計數：{st or '無'}")
    print(f"  核心會送出的動作：{dict(acts) or '無'}")
    if logs:
        print("  核心事件（狀態轉換理由）：")
        for line in logs[:12]:
            print(f"    {line}")
        if len(logs) > 12:
            print(f"    …另有 {len(logs) - 12} 筆")
    slow = sum(1 for t in tick_ms if t > 800) / max(1, len(tick_ms))
    print(f"  迴圈速度：{'綠' if slow < 0.1 else '黃'}（超過 800ms 的圈佔 {slow:.0%}）")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="端到端整備度檢查（不送按鍵）")
    p.add_argument("--seconds", type=float, default=45.0)
    p.add_argument("--config", default=None)
    a = p.parse_args(argv)
    return run(a.seconds, a.config)


if __name__ == "__main__":
    sys.exit(main())
