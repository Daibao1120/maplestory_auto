# -*- coding: utf-8 -*-
"""怪物模板自動採集器（唯讀，不送任何按鍵）。

原理：三畫格差分——只有「會動的東西」會留下輪廓。前段先觀察，把固定位置
反覆擺動的景物（風吹樹叢、瀑布）列入黑名單；鏡頭移動（你在走位）的幀自動
跳過。候選裁圖存到 assets/templates/_candidates/，並產生編號縮圖總表
candidates_sheet.png 供人工挑選——把是怪物的圖改名搬進
assets/templates/monsters/ 即可（傷害數字、寵物、玩家請刪除）。

用法（站在目標怪附近、停止攻擊讓怪活著爬，鏡頭別動）：
    python tools/collect_monster_templates.py                 # 預設收 90 秒
    python tools/collect_monster_templates.py --seconds 120 --min-w 60 --max-w 260

僅供學習研究。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main(argv=None):
    p = argparse.ArgumentParser(description="怪物模板自動採集（三畫格差分）")
    p.add_argument("--seconds", type=int, default=90, help="採集時長（秒，預設 90）")
    p.add_argument("--warmup", type=int, default=10, help="景物黑名單觀察秒數（預設 10）")
    p.add_argument("--min-w", type=int, default=70)
    p.add_argument("--max-w", type=int, default=320)
    p.add_argument("--min-h", type=int, default=40)
    p.add_argument("--max-h", type=int, default=200)
    p.add_argument("--center-excl", type=int, default=110,
                   help="畫面中央排除半寬 px（自己的角色；預設 110）")
    p.add_argument("--max-cands", type=int, default=18)
    p.add_argument("--window", default="新楓之谷")
    args = p.parse_args(argv)

    try:
        import cv2
        import numpy as np
        from src.capture import ScreenCapture
    except ImportError as e:
        print(f"[錯誤] 缺套件：{e}（需要 opencv/numpy/mss）")
        return 1

    out_dir = os.path.join(ROOT, "assets", "templates", "_candidates")
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):          # 清掉上次的候選
        if f.startswith("cand_") and f.endswith(".png"):
            os.remove(os.path.join(out_dir, f))

    cap = ScreenCapture(backend="mss", window_title=args.window)
    frames, cands, last_boxes = [], [], []
    blacklist = {}
    t0 = time.time()
    print(f"觀察 {args.warmup}s（建立景物黑名單）→ 採集 {args.seconds}s。"
          "請站著別動、讓怪活著爬。Ctrl+C 可提前結束。")
    try:
        while time.time() - t0 < args.warmup + args.seconds and len(cands) < args.max_cands:
            f = cap.grab()
            if f is None:
                time.sleep(0.3)
                continue
            frames.append(f)
            if len(frames) > 3:
                frames.pop(0)
            if len(frames) < 3:
                time.sleep(0.22)
                continue
            f1, f2, f3 = frames
            h, w = f2.shape[:2]
            d12 = (np.abs(f2.astype(np.int16) - f1.astype(np.int16)).max(axis=2) > 25)
            if d12[int(h * 0.22):int(h * 0.88), :].mean() > 0.20:
                time.sleep(0.22)           # 鏡頭在移動
                continue
            d23 = (np.abs(f2.astype(np.int16) - f3.astype(np.int16)).max(axis=2) > 25)
            motion = (d12 & d23).astype(np.uint8)
            motion[:int(h * 0.22), :] = 0  # 上方 UI/小地圖
            motion[int(h * 0.88):, :] = 0  # 底部 UI
            cx, cy = w // 2, h // 2
            motion[max(0, cy - 380):, max(0, cx - args.center_excl):cx + args.center_excl] = 0
            motion = cv2.dilate(motion, np.ones((7, 7), np.uint8))
            num, _l, stats, _c = cv2.connectedComponentsWithStats(motion, connectivity=8)
            warm = (time.time() - t0) < args.warmup
            for i in range(1, num):
                x, y, bw, bh, area = stats[i]
                if not (args.min_w <= bw <= args.max_w and args.min_h <= bh <= args.max_h):
                    continue
                if area < 500:
                    continue
                key = (x // 80, y // 80)
                if warm:
                    blacklist[key] = blacklist.get(key, 0) + 1
                    continue
                if blacklist.get(key, 0) >= 3:
                    continue               # 固定位置一直動 = 景物
                if any(abs(x - px) < 110 and abs(y - py) < 70 for px, py in last_boxes):
                    continue
                x1, y1 = max(0, x - 8), max(0, y - 8)
                x2, y2 = min(w, x + bw + 8), min(h, y + bh + 8)
                idx = len(cands)
                cv2.imwrite(os.path.join(out_dir, f"cand_{idx:02d}.png"), f2[y1:y2, x1:x2])
                cands.append(idx)
                last_boxes.append((x, y))
                print(f"  候選 {idx:02d}（{bw}x{bh} @ {x},{y}）")
            time.sleep(0.22)
    except KeyboardInterrupt:
        print("提前結束。")
    finally:
        cap.close()

    if not cands:
        print("沒有採到候選：確認畫面上有活著會動的怪、且角色沒有走動。")
        return 1

    thumbs = []
    for i in cands:
        img = cv2.imread(os.path.join(out_dir, f"cand_{i:02d}.png"))
        s = 160 / max(img.shape[:2])
        img = cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))))
        canvas = np.full((170, 170, 3), 40, dtype=np.uint8)
        canvas[:img.shape[0], :img.shape[1]] = img
        thumbs.append(canvas)
    cols = 5
    rows = (len(thumbs) + cols - 1) // cols
    sheet = np.full((rows * 180, cols * 180, 3), 40, dtype=np.uint8)
    for i, tmb in enumerate(thumbs):
        r, c = divmod(i, cols)
        sheet[r * 180:r * 180 + 170, c * 180:c * 180 + 170] = tmb
        cv2.putText(sheet, str(i), (c * 180 + 4, r * 180 + 165),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    sheet_path = os.path.join(out_dir, "candidates_sheet.png")
    cv2.imwrite(sheet_path, sheet)
    print(f"完成：{len(cands)} 個候選。請打開 {sheet_path} 挑選，")
    print("把「是怪物」的 cand_XX.png 改名搬到 assets/templates/monsters/（其餘刪除）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
