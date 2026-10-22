"""全自動校正：自己操作遊戲畫面，把按鈕、圖示、區域全部找出來存好。

使用方式：
  1. 遊戲視窗維持平常掛機的大小，不要被其他視窗擋住
  2. 執行 autocal.bat（或 python autocal.py）
  3. 等它跑完（約 30 秒～1 分鐘），期間不要動滑鼠

原理：assets/refs/ 裡有從你的遊戲截圖做的「低解析度參考圖」，
程式先用它們在畫面上找到每個東西，再從你的螢幕直接剪下「原尺寸」的模板存起來，
所以存下來的模板跟你的畫面完全一致。
"""
from __future__ import annotations

import ctypes
import json
import sys
import time

import cv2
import numpy as np
import yaml

from autopot.calib import DATA_DIR, ROOT, TEMPLATE_DIR, Calibration
from autopot.window import GameWindow

REFS = ROOT / "assets" / "refs"
META = json.loads((REFS / "refs.json").read_text(encoding="utf-8"))
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
WIN = GameWindow(CFG["window_title"], CFG.get("window_exclude"))
CAL = Calibration.load()

SCREEN_W = ctypes.windll.user32.GetSystemMetrics(0)   # DPI 感知後是實際像素
F = META["ref_screen_width"] / SCREEN_W               # 原尺寸 → 參考圖尺寸的縮放比例
_ref_cache: dict[str, np.ndarray] = {}


class CalFail(Exception):
    pass


def ref(name: str) -> np.ndarray:
    if name not in _ref_cache:
        _ref_cache[name] = cv2.imread(str(REFS / f"{name}.png"))
    return _ref_cache[name]


def shot() -> np.ndarray:
    for _ in range(5):
        img = WIN.capture()
        if float(img.std()) > 4:
            return img
        time.sleep(0.4)
    raise CalFail("截圖一直是空白畫面，遊戲視窗可能被縮小了")


def low(img: np.ndarray) -> np.ndarray:
    return cv2.resize(img, None, fx=F, fy=F, interpolation=cv2.INTER_AREA)


def locate(name: str, img=None, thr: float = 0.72):
    """在畫面上找參考圖，回傳 (分數, [x, y, w, h] 原尺寸座標) 或 None。"""
    img = shot() if img is None else img
    lo = low(img)
    t0 = ref(name)
    best = None
    for s in (0.94, 0.97, 1.0, 1.03, 1.06):
        t = cv2.resize(t0, None, fx=s, fy=s, interpolation=cv2.INTER_LINEAR) if s != 1 else t0
        if lo.shape[0] < t.shape[0] or lo.shape[1] < t.shape[1]:
            continue
        r = cv2.matchTemplate(lo, t, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(r)
        if best is None or mv > best[0]:
            best = (mv, ml, t.shape[1], t.shape[0])
    if not best or best[0] < thr:
        return None
    mv, (x, y), w, h = best
    return mv, [round(x / F), round(y / F), round(w / F), round(h / F)]


def wait_locate(name: str, timeout: float = 6, thr: float = 0.72):
    end = time.time() + timeout
    while time.time() < end:
        m = locate(name, thr=thr)
        if m:
            return m
        time.sleep(0.4)
    return None


def save_tpl(name: str, box, img=None, inset: int = 1) -> None:
    img = shot() if img is None else img
    x, y, w, h = box
    crop = img[y + inset:y + h - inset, x + inset:x + w - inset]
    TEMPLATE_DIR.mkdir(exist_ok=True)
    cv2.imwrite(str(TEMPLATE_DIR / f"{name}.png"), crop)
    # 確認存下來的模板在原畫面上找得到（相似度要很高）
    r = cv2.matchTemplate(img, crop, cv2.TM_CCOEFF_NORMED)
    print(f"  ✔ {name:14s} 位置 {box[:2]}  自我比對 {r.max():.3f}")


def layout(name: str, anchor_ref: str, anchor_box) -> list[int]:
    """依參考圖的版面，把區域換算成原尺寸座標（以某個已找到的東西為基準）。"""
    lx0, ly0, lx1, ly1 = META["layout"][name]
    ax0, ay0 = META["items"][anchor_ref][:2]
    bx, by = anchor_box[:2]
    return [round(bx + (lx0 - ax0) / F), round(by + (ly0 - ay0) / F),
            round((lx1 - lx0) / F), round((ly1 - ly0) / F)]


def item_center_offset(item: str, anchor_ref: str) -> tuple[int, int]:
    x0, y0, x1, y1 = META["items"][item]
    ax0, ay0 = META["items"][anchor_ref][:2]
    return round(((x0 + x1) / 2 - ax0) / F), round(((y0 + y1) / 2 - ay0) / F)


def click_box(box, double: bool = False) -> None:
    x, y, w, h = box
    (WIN.double_click if double else WIN.click)(x + w // 2, y + h // 2)


def need(m, what: str):
    if not m:
        raise CalFail(f"畫面上找不到「{what}」")
    return m


# ----------------------------------------------------------------------
def step_hud() -> list[int]:
    print("\n[1/6] 下方工具列、HP/MP 條、快捷欄")
    img = shot()
    CAL.data["window_size"] = [img.shape[1], img.shape[0]]
    _, box = need(locate("inv_button", img), "道具欄按鈕（下方工具列）")
    save_tpl("inv_button", box, img)
    for n in ("hp_bar", "mp_bar", "qs_hp", "qs_mp"):
        CAL.set_region(n, layout(n, "inv_button", box))
        print(f"  ✔ 區域 {n:10s} {CAL.region(n)}")
    from autopot.vision import bar_ratio, crop
    hp = bar_ratio(crop(img, CAL.region("hp_bar")), "hp")
    mp = bar_ratio(crop(img, CAL.region("mp_bar")), "mp")
    print(f"    目前讀到 HP {hp:.0%}、MP {mp:.0%}")
    if not (hp > 0.05):
        raise CalFail("HP 條位置好像不對（讀到 0%）")
    return box


def step_inventory() -> None:
    print("\n[2/6] 打開道具欄 → 現金分頁 → 找妙妙")
    m = locate("inv_open")
    if not m:
        _, b = need(locate("inv_button"), "道具欄按鈕")
        click_box(b)
        m = need(wait_locate("inv_open"), "道具欄視窗（ITEM INVENTORY）")
    _, inv = m
    save_tpl("inv_open", inv)
    dx, dy = item_center_offset("inv_tab_cash", "inv_open")
    WIN.click(inv[0] + dx, inv[1] + dy)
    time.sleep(0.8)
    img = shot()
    _, tab = need(locate("inv_tab_cash", img), "現金分頁")
    save_tpl("inv_tab_cash", tab, img)
    CAL.set_anchor("inv_tab_cash", "inv_open", dx, dy)
    _, mm = need(locate("miaomiao", img, thr=0.75), "妙妙圖示")
    save_tpl("miaomiao", mm, img)


def step_shop() -> list[int]:
    print("\n[3/6] 雙擊妙妙打開商店")
    mm = None
    for attempt in range(3):
        m = locate("miaomiao")
        if m:
            mm = m[1]
        if not mm:
            raise CalFail("找不到妙妙")
        click_box(mm, double=True)
        s = wait_locate("shop_leave", 8)
        if s:
            break
        print("  商店還沒開，再試一次")
    else:
        raise CalFail("雙擊妙妙三次都沒有開出商店")
    time.sleep(0.8)
    img = shot()
    _, leave = s
    save_tpl("shop_leave", leave, img)
    for n in ("shop_buy", "shop_sell"):
        _, b = need(locate(n, img), n)
        save_tpl(n, b, img)
    CAL.set_region("shop_list", layout("shop_list", "shop_leave", leave))
    CAL.set_region("inv_list", layout("inv_list", "shop_leave", leave))
    # 只在商品清單右側找（聊天框也有一樣的捲動鈕，不能抓錯）
    sl = CAL.region("shop_list")
    sx0, sy0 = sl[0] + sl[2] - 20, max(0, sl[1] - 30)
    sub = img[sy0:sl[1] + sl[3] + 30, sx0:sl[0] + sl[2] + 120]
    _, up = need(locate("list_up", sub), "清單 ▲ 鈕")
    _, dn = need(locate("list_down", sub), "清單 ▼ 鈕")
    up = [up[0] + sx0, up[1] + sy0, up[2], up[3]]
    dn = [dn[0] + sx0, dn[1] + sy0, dn[2], dn[3]]
    save_tpl("list_up", up, img, inset=0)
    save_tpl("list_down", dn, img, inset=0)
    # 右邊切到「消耗」
    dx, dy = item_center_offset("shop_tab_use", "shop_leave")
    WIN.click(leave[0] + dx, leave[1] + dy)
    time.sleep(0.8)
    img = shot()
    _, tab = need(locate("shop_tab_use", img), "商店右邊的消耗分頁")
    save_tpl("shop_tab_use", tab, img)
    CAL.set_anchor("shop_tab_use", "shop_leave", dx, dy)
    CAL.save()
    return leave


def step_items() -> None:
    print("\n[4/6] 在商品清單裡找馴鹿奶、紅豆刨冰")
    from autopot.shop import ShopBot
    from autopot.vision import crop, same_view
    bot = ShopBot(WIN, Calibration.load(), CFG)
    rect = bot.cal.region("shop_list")
    bot.scroll_top(rect)
    down = bot._arrow(rect, "list_down")
    if not down:
        raise CalFail("找不到商品清單的 ▼ 鈕")
    want = {CFG["buy"]["hp"]["template"]: "buy_milk", CFG["buy"]["mp"]["template"]: "buy_ice"}
    best = {k: None for k in want}
    prev = None
    same = 0
    for _ in range(120):
        img = shot()
        view = crop(img, rect)
        for tpl_name, refname in want.items():
            # 只在商品清單範圍內找
            lo = low(view)
            r = cv2.matchTemplate(lo, ref(refname), cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(r)
            if best[tpl_name] is None or mv > best[tpl_name][0]:
                h, w = ref(refname).shape[:2]
                box = [rect[0] + round(ml[0] / F), rect[1] + round(ml[1] / F), round(w / F), round(h / F)]
                best[tpl_name] = (mv, box, img)
        if prev is not None and same_view(prev, view):
            same += 1
            if same >= 3:
                break
        else:
            same = 0
        prev = view
        WIN.click(*down.center, jitter=1)
        time.sleep(0.12)
    for tpl_name, refname in want.items():
        b = best[tpl_name]
        if not b or b[0] < 0.75:
            raise CalFail(f"清單裡找不到「{refname}」（最高相似度 {b[0] if b else 0:.2f}）")
        print(f"    {refname} 最佳相似度 {b[0]:.2f}")
        save_tpl(tpl_name, b[1], b[2], inset=2)


def step_dialog() -> None:
    print("\n[5/6] 打開購買數量視窗（會按取消，不會真的買）")
    from autopot.shop import ShopBot
    bot = ShopBot(WIN, Calibration.load(), CFG)
    item = bot.locate_in_list(bot.cal.region("shop_list"), CFG["buy"]["mp"]["template"])
    if not item:
        raise CalFail("找不到刨冰那一列")
    bot.click_match(item)
    time.sleep(0.4)
    bot.click_tpl("shop_buy")
    m = need(wait_locate("qty_dialog", 5), "購買數量視窗")
    time.sleep(0.5)
    img = shot()
    _, dlg = m
    save_tpl("qty_dialog", dlg, img)
    for n in ("dialog_ok", "dialog_cancel"):
        _, b = need(locate(n, img), n)
        save_tpl(n, b, img)
    qi = layout("qty_input", "qty_dialog", dlg)
    CAL.set_region("qty_input", qi)
    CAL.set_anchor("qty_input", "qty_dialog", qi[0] - dlg[0], qi[1] - dlg[1])
    CAL.save()
    _, c = locate("dialog_cancel", img)
    click_box(c)
    time.sleep(0.6)
    if locate("qty_dialog"):
        raise CalFail("數量視窗沒有關掉，請手動按取消")
    print("  ✔ 已按取消")


def step_finish() -> None:
    print("\n[6/6] 離開商店、測試數字辨識")
    m = locate("shop_leave")
    if m:
        click_box(m[1])
        time.sleep(1.0)
    m = locate("inv_open")
    if m:
        b = locate("inv_button")
        if b:
            click_box(b[1])
    time.sleep(0.8)
    if not CFG.get("ocr_enabled", False):
        CAL.save()
        print("  （快捷欄數字辨識預設關閉，略過）")
        return
    from autopot.app import load_digit_reader
    from autopot.vision import crop
    r = load_digit_reader()
    for k in ("qs_hp", "qs_mp"):
        reads = []
        for _ in range(3):
            txt, _, pos = r.read_detail(crop(shot(), CAL.region(k)))
            reads.append((txt, pos[0][0] if pos else None))
            time.sleep(0.5)
        name = CFG["buy"]["hp" if k == "qs_hp" else "mp"]["name"]
        txts = [t for t, _ in reads if t]
        if txts:
            xs = sorted(x for t, x in reads if t)
            CAL.data.setdefault("digit_start", {})[k] = xs[len(xs) // 2]
            print(f"  ✔ {name} 讀到 {txts[-1]}（請對照遊戲快捷欄確認）")
        else:
            print(f"  ⚠ {name} 的數字讀不到 → 程式會改用血條＋定時補貨保護（可之後跑 digits.bat 學習）")
    CAL.save()


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("楓之谷自動補水：全自動校正")
    print("接下來約 1 分鐘程式會操作遊戲畫面，請不要動滑鼠。滑鼠甩到螢幕左上角可緊急停止。")
    if SCREEN_W != META["native_screen_width"]:
        print(f"⚠ 你的螢幕寬度是 {SCREEN_W}，參考圖是在 {META['native_screen_width']} 做的，可能比較不準")
    if not WIN.locate():
        raise SystemExit("找不到遊戲視窗，請先開遊戲")
    if CFG.get("arrange_windows", True):
        try:
            WIN.arrange_side_by_side()
        except Exception as e:
            print(f"（排列視窗失敗，不影響執行：{e}）")
    WIN.activate()
    time.sleep(1)
    try:
        step_hud()
        CAL.save()
        step_inventory()
        CAL.save()
        step_shop()
        step_items()
        step_dialog()
        step_finish()
    except CalFail as e:
        DATA_DIR.mkdir(exist_ok=True)
        try:
            cv2.imwrite(str(DATA_DIR / "autocal_fail.png"), shot())
        except Exception:
            pass
        print(f"\n✘ 自動校正失敗：{e}")
        print("  失敗當下的畫面存在 data/autocal_fail.png。可以把這個訊息和圖傳給 Claude，")
        print("  或改用 calibrate.bat 手動校正那一項（例如 py calibrate.py redo miaomiao）。")
        # 盡量把畫面恢復
        for n in ("dialog_cancel", "shop_leave"):
            try:
                m = locate(n)
                if m:
                    click_box(m[1])
                    time.sleep(0.6)
            except Exception:
                pass
        sys.exit(1)
    miss = CAL.missing([b["template"] for b in CFG["buy"].values()])
    if miss:
        print("\n還缺：\n  " + "\n  ".join(miss))
    else:
        print("\n✅ 自動校正完成！接下來執行 dryrun.bat 試跑一次完整流程。")


if __name__ == "__main__":
    main()
