"""掃描學習：把妙妙商店（或背包的消耗欄）從頭捲到尾，列出每一種道具的圖片，讓使用者挑。

不需要事先知道道具叫什麼、也不用管畫面多大：
  1. 清單每一列切出來（列高用 ShopBot._pitch，列的起點用列與列之間的亮色分隔線對齊）
  2. 每一列只取「名稱那一行」（上半部、圖示右邊）當模板 —— 背包圖示上的數量、商店/背包不同的價格都不會影響
  3. 名稱一樣的列只留一個
挑好之後存成 templates/<名稱>.png，補貨程式就用它在清單裡找。
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

log = logging.getLogger("autopot.catalog")


@dataclass
class ListItem:
    name_img: np.ndarray            # 名稱那一行（當模板用）
    row_img: np.ndarray             # 整列（給介面顯示）
    first_row: int                  # 第一次出現在清單第幾列（從 0 開始）
    count: int = 1                  # 清單裡有幾列是它（背包裡可能有好幾組）
    key: str = field(default="")

    def __post_init__(self):
        if not self.key:
            g = cv2.resize(cv2.cvtColor(self.name_img, cv2.COLOR_BGR2GRAY), (32, 8))
            self.key = hashlib.md5(g.tobytes()).hexdigest()[:10]


def _separator_phase(view: np.ndarray, p: float) -> float | None:
    """列與列之間的分隔是「亮－暗（2~3px 的深色細線）－亮」。找出暗線的位置（取多條的相位平均）。
    實測：電腦版、手機版的清單都是這樣（最左邊幾 px 的直條上最清楚，不會被圖示和文字干擾）。"""
    h, w = view.shape[:2]
    g = cv2.cvtColor(view[:, : max(4, int(w * 0.02)) + 2], cv2.COLOR_BGR2GRAY).astype(np.float32)
    prof = g[:, 2:].mean(axis=1) if g.shape[1] > 3 else g.mean(axis=1)
    cands = []
    y = 0
    while y < h:
        if prof[y] < 165:
            y1 = y
            while y1 + 1 < h and prof[y1 + 1] < 165:
                y1 += 1
            above = prof[max(0, y - 4):y]
            below = prof[y1 + 1:y1 + 5]
            if y1 - y <= 4 and len(above) and len(below) and above.max() > 205 and below.max() > 205:
                cands.append((y + y1) / 2.0)
            y = y1 + 1
        else:
            y += 1
    if not cands:
        return None
    ph = np.array([c % p for c in cands])
    ref = ph[0]
    ph = np.where(ph - ref > p / 2, ph - p, np.where(ref - ph > p / 2, ph + p, ph))   # 繞一圈的相位
    return float(np.median(ph)) % p


def row_bands(view: np.ndarray, pitch: float) -> list[tuple[int, int]]:
    """清單畫面裡每一個「完整露出來」的列：[(y0, y1), ...]。"""
    h = view.shape[0]
    p = float(pitch)
    if p < 8 or h < p * 0.9:
        return []
    sep = _separator_phase(view, p)
    if sep is not None:
        start = sep + 3                      # 列從暗線下面開始
    else:
        # 找不到暗線：用「亮度最高的相位」當列的分界
        g = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY).astype(np.float32)
        prof = g[:, : max(1, int(view.shape[1] * 0.6))].mean(axis=1)
        start = max(range(int(p)), key=lambda o: float(prof[np.arange(o, h, p).astype(int)].mean()))
    out = []
    y = start % p - p
    while y < h:
        y0, y1 = int(round(y)), int(round(y + p)) - 1
        if y0 < 0 and y0 >= -p * 0.06:      # 第一列上面被清單邊框切掉幾 px 沒關係
            y0 = 0
        if y1 > h and y1 - h <= p * 0.1:       # 最後一列下面被切掉一點也沒關係（名稱在上半部）
            y1 = h
        if y0 >= 0 and y1 <= h and y1 - y0 > p * 0.85:
            out.append((y0, y1))
        y += p
    return out


def name_crop(row: np.ndarray) -> np.ndarray | None:
    """一列 → 名稱那一行（深色字的外框再留一點邊）。沒有字（空格）回傳 None。"""
    h, w = row.shape[:2]
    x0 = int(h * 0.92)                      # 圖示大約是正方形，字在圖示右邊
    y0, y1 = int(h * 0.06), int(h * 0.50)   # 上半部是名稱，下半部是價格
    if x0 >= w - 10:
        return None
    area = row[y0:y1, x0:int(w * 0.97)]
    g = cv2.cvtColor(area, cv2.COLOR_BGR2GRAY)
    ink = g < 110
    cols = np.where(ink.sum(axis=0) >= 1)[0]
    rows = np.where(ink.sum(axis=1) >= 1)[0]
    if len(cols) < 3 or len(rows) < 3 or ink.sum() < 15:
        return None
    pad = max(2, h // 30)
    # 右邊固定切到這一行的盡頭（不是切到字的結尾）：這樣「藥水」跟「藥水(大)」的模板才分得出來
    cx0, cx1 = max(0, cols[0] - pad), area.shape[1]
    cy0, cy1 = max(0, rows[0] - pad), min(area.shape[0], rows[-1] + pad + 1)
    out = area[cy0:cy1, cx0:cx1]
    if out.shape[0] < 6 or out.shape[1] < 10:
        return None
    return np.ascontiguousarray(out)


def _ink(img):
    return (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) < 110).astype(np.uint8) * 255


def same_item(a: np.ndarray, b: np.ndarray, min_cc: float = 0.88, max_diff: float = 0.12) -> bool:
    """兩個名稱圖是不是同一個道具（大小差不多、深色字幾乎一樣）。
    實測（真實截圖）：同一種道具在清單不同列（遊戲畫字會差零點幾 px），筆畫相似度 0.91~0.97、不同的點 3~8%；
    不同道具（中文名稱至少差一個字）相似度 < 0.65、不同的點 > 25%。"""
    if abs(a.shape[0] - b.shape[0]) > 3 or abs(a.shape[1] - b.shape[1]) > 4:
        return False
    big, small = (a, b) if a.shape[0] * a.shape[1] >= b.shape[0] * b.shape[1] else (b, a)
    pad = cv2.copyMakeBorder(_ink(big), 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=0)
    s = _ink(small)
    if pad.shape[0] < s.shape[0] or pad.shape[1] < s.shape[1]:
        return False
    r = cv2.matchTemplate(pad, s, cv2.TM_CCOEFF_NORMED)
    _, mv, _, (x, y) = cv2.minMaxLoc(np.nan_to_num(r, nan=-1.0))
    if mv < min_cc:
        return False
    # 對齊後逐點比：不一樣的筆畫要很少（「紅色藥水」跟「橙色藥水」只差一個字也要分得出來）
    win = pad[y:y + s.shape[0], x:x + s.shape[1]]
    diff = np.count_nonzero(win != s)
    ink = max(1, np.count_nonzero(s) + np.count_nonzero(win))
    return diff / ink < max_diff


def scan_list(bot, rect, max_steps: int = 400, on_progress=None) -> list[ListItem]:
    """把 rect 這個清單從最上面捲到最下面，回傳每一種道具（依第一次出現的順序）。"""
    bot.scroll_end(rect, -1)
    pitch = bot._pitch(rect)
    items: list[ListItem] = []
    seen_rows: dict[int, str] = {}          # 絕對列號 → 道具 key（同一列在下一個畫面又出現時不要重複算）
    row = 0
    bot._park()
    view = bot._view(bot.shot(), rect)
    for step in range(max_steps):
        for k, (y0, y1) in enumerate(row_bands(view, pitch)):
            # 每一列都重新看（不靠列號跳過：捲動偶爾一次捲兩列，列號會對不上，跳過就會漏掉道具）
            absrow = row + k
            r = view[y0:y1]
            nm = name_crop(r)
            if nm is None:
                seen_rows.setdefault(absrow, "")
                continue
            hit = next((it for it in items if same_item(it.name_img, nm)), None)
            if hit is None:
                hit = ListItem(nm, r.copy(), absrow, count=0)
                items.append(hit)
            if absrow not in seen_rows:          # 組數只是參考（列號對不上時可能少算）
                hit.count += 1
                seen_rows[absrow] = hit.key
            hit.count = max(hit.count, 1)
        if on_progress:
            on_progress(step, len(items))
        k = bot._step(rect, +1)
        if not k:
            break
        row += k
        view = bot._view(bot.shot(), rect)
    log.info("清單掃完：%d 種道具（共 %d 列）", len(items), len([v for v in seen_rows.values() if v]))
    return items


def threshold_for(item: ListItem, items: list[ListItem], base: float = 0.85) -> tuple[float, float]:
    """這個道具的模板要用多嚴格的比對門檻，才不會比對到清單裡別的道具。
    回傳 (門檻, 最像的別的道具的分數)。最像的分數 >= 0.95 表示幾乎分不出來（介面要警告）。"""
    from .vision import find
    worst = 0.0
    for other in items:
        if other is item:
            continue
        m = find(other.row_img, item.name_img, 0.0, robust=True)
        if m is not None:
            worst = max(worst, float(m.score))
    th = min(0.97, max(base, worst + 0.05))
    return round(th, 3), round(worst, 3)


def scan_shop(bot, on_progress=None) -> list[ListItem]:
    """打開妙妙商店，掃左邊的商品清單（掃完商店保持開著）。"""
    bot.open_shop()
    return scan_list(bot, bot.region("shop_list"), on_progress=on_progress)


def scan_inventory(bot, on_progress=None) -> list[ListItem]:
    """打開妙妙商店，切到右邊背包的「消耗」分頁，掃一遍（掃完商店保持開著）。"""
    bot.open_shop()
    bot.click_tab("shop_tab_use", "shop_leave")
    return scan_list(bot, bot.region("inv_list"), on_progress=on_progress)


def save_item_template(name: str, item: ListItem) -> str:
    """存成 templates/<name>.png，回傳模板名稱。"""
    from . import calib
    calib.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(calib.TEMPLATE_DIR / f"{name}.png"), item.name_img)
    return name


def png_bytes(img: np.ndarray, max_w: int | None = None) -> bytes:
    if max_w and img.shape[1] > max_w:
        f = max_w / img.shape[1]
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes() if ok else b""
