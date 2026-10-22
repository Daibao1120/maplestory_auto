"""從低解析度參考圖（assets/refs/）在背包清單裡找道具，剪下原尺寸模板存到 templates/。

用在「要賣的道具」：背包裡現在沒有的道具找不到，就先跳過，等之後撿到再自動補學。
"""
from __future__ import annotations

import ctypes
import json
import logging


import cv2

from . import calib
from .calib import ASSETS, ROOT
from .vision import crop

log = logging.getLogger("autopot.learn")
REFS = ASSETS / "refs"


def _factor() -> float:
    meta = json.loads((REFS / "refs.json").read_text(encoding="utf-8"))
    try:
        sw = ctypes.windll.user32.GetSystemMetrics(0)
    except Exception:
        sw = meta["native_screen_width"]
    return meta["ref_screen_width"] / sw


def capture_items(bot, names: list[str], min_score: float = 0.9) -> list[str]:
    """商店要已經開著、右邊在「消耗」分頁。回傳成功學到的模板名稱。"""
    names = [n for n in names if (REFS / f"{n}.png").exists()]
    if not names:
        return []
    F = _factor()
    rect = bot.region("inv_list")
    bot.scroll_top(rect)
    best = {n: None for n in names}
    steps = 0
    for _ in range(150):
        bot._park()                       # 滑鼠移開，免得道具說明框擋住清單
        img = bot.shot()
        view = crop(img, rect)
        # 參考圖是從縮小的截圖剪的，縮放比例可能差一點點 → 試幾個相近的比例取最好的
        for f in [F * (0.95 + 0.01 * i) for i in range(11)]:
            lo = cv2.resize(view, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            for n in names:
                ref = cv2.imread(str(REFS / f"{n}.png"))
                if lo.shape[0] < ref.shape[0] or lo.shape[1] < ref.shape[1]:
                    continue
                r = cv2.matchTemplate(lo, ref, cv2.TM_CCOEFF_NORMED)
                _, mv, _, ml = cv2.minMaxLoc(r)
                if best[n] is None or mv > best[n][0]:
                    h, w = ref.shape[:2]
                    box = [round(ml[0] / f), round(ml[1] / f), round(w / f), round(h / f)]
                    best[n] = (mv, box, view.copy())
        steps += 1
        if not bot._step(rect, +1):
            break
    log.info("學道具：背包往下捲了 %d 列", steps)
    learned = []
    tdir = calib.TEMPLATE_DIR
    tdir.mkdir(exist_ok=True)
    for n, b in best.items():
        if not b or b[0] < min_score:
            log.info("背包裡目前沒有「%s」（最高相似度 %.2f），之後撿到再學", n, b[0] if b else 0)
            if b:
                try:
                    (calib.DATA_DIR / "debug").mkdir(exist_ok=True)
                    cv2.imwrite(str(calib.DATA_DIR / "debug" / f"learn_{n}_best.png"), b[2])
                except Exception:
                    pass
            continue
        _, (x, y, w, h), view = b
        tpl = view[y + 1:y + h - 1, x + 1:x + w - 1]
        cv2.imwrite(str(tdir / f"{n}.png"), tpl)
        bot.cal._tpl_cache.pop(n, None)
        log.info("已學會要賣的道具「%s」（相似度 %.2f）", n, b[0])
        learned.append(n)
    return learned
