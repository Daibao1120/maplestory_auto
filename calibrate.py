"""校正工具：教程式認識你的遊戲畫面。

用法（在這個資料夾打開命令提示字元）：
  python calibrate.py              完整校正精靈（第一次用這個）
  python calibrate.py check        檢查目前畫面認得哪些東西，輸出 data/check.png
  python calibrate.py digits       測試／學習快捷欄數字辨識
  python calibrate.py redo <名稱>  只重做某一項（例如 redo miaomiao、redo hp_bar）
  python calibrate.py buyitem <模板名>   新增要買的商品圖（例如 buyitem buy_milk）
  python calibrate.py sell <模板名>      新增要賣的道具圖（例如 sell sell_red_potion）
"""
from __future__ import annotations

import json
import sys
import time

import cv2
import numpy as np
import yaml

from autopot.calib import (DATA_DIR, REQUIRED_REGIONS, REQUIRED_TEMPLATES, ROOT,
                           TEMPLATE_DIR, Calibration)
from autopot.window import GameWindow

CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
WIN = GameWindow(CFG["window_title"], CFG.get("window_exclude"))
CAL = Calibration.load()
SEL = "select (ENTER=ok, C=cancel)"


def grab() -> np.ndarray:
    WIN.activate()
    time.sleep(0.6)
    img = WIN.capture()
    CAL.data["window_size"] = [img.shape[1], img.shape[0]]
    return img


def _select(img: np.ndarray, max_w=1600, max_h=900):
    k = min(1.0, max_w / img.shape[1], max_h / img.shape[0])
    view = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k < 1 else img
    cv2.namedWindow(SEL, cv2.WINDOW_AUTOSIZE)
    cv2.setWindowProperty(SEL, cv2.WND_PROP_TOPMOST, 1)
    x, y, w, h = cv2.selectROI(SEL, view, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(SEL)
    if w == 0 or h == 0:
        return None
    return int(x / k), int(y / k), int(w / k), int(h / k)


def select(img: np.ndarray, label: str):
    """兩段式：先在縮小圖大概框，再在放大圖精確框。"""
    print(f"\n▶ 請框選：{label}")
    print("   第 1 步：在整張畫面上「大概」框住它附近（框大一點沒關係），按 Enter")
    rough = _select(img)
    if not rough:
        print("   已取消")
        return None
    x, y, w, h = rough
    pad = 20
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(img.shape[1], x + w + pad), min(img.shape[0], y + h + pad)
    sub = img[y0:y1, x0:x1]
    zoom = max(1.0, min(6.0, 1200 / sub.shape[1], 700 / sub.shape[0]))
    big = cv2.resize(sub, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
    print("   第 2 步：在放大圖上「精確」框住它，按 Enter")
    fine = _select(big, 10000, 10000)
    if not fine:
        return x, y, w, h
    fx, fy, fw, fh = fine
    return (x0 + round(fx / zoom), y0 + round(fy / zoom),
            max(1, round(fw / zoom)), max(1, round(fh / zoom)))


def save_template(img, name, label) -> bool:
    r = select(img, label)
    if not r:
        return False
    x, y, w, h = r
    TEMPLATE_DIR.mkdir(exist_ok=True)
    cv2.imwrite(str(TEMPLATE_DIR / f"{name}.png"), img[y:y + h, x:x + w])
    print(f"   ✔ 已存模板 {name}（{w}x{h}）")
    return True


def save_region(img, name, label) -> bool:
    r = select(img, label)
    if not r:
        return False
    CAL.set_region(name, r)
    CAL.save()
    print(f"   ✔ 已存區域 {name} = {list(r)}")
    return True


def anchor_tab(img, tab: str, base: str) -> None:
    """記錄分頁中心點相對於 base 模板左上角的位置。"""
    from autopot.vision import find
    t = find(img, CAL.template(tab), 0.8)
    b = find(img, CAL.template(base), 0.8)
    if t and b:
        cx, cy = t.center
        CAL.set_anchor(tab, base, cx - b.x, cy - b.y)
        CAL.save()


def pause(msg: str) -> None:
    input(f"\n========================================\n{msg}\n準備好之後，回到這個視窗按 Enter（程式會自動切到遊戲截圖）...")


def wizard() -> None:
    buy = CFG["buy"]
    print("楓之谷自動補水 校正精靈\n每一步都會先截圖遊戲，再跳出框選視窗。框錯可以之後用 redo 重做。")

    pause("【第 1 階段】請讓遊戲在一般掛機畫面，道具欄先關閉。")
    img = grab()
    save_region(img, "hp_bar", REQUIRED_REGIONS["hp_bar"])
    save_region(img, "mp_bar", REQUIRED_REGIONS["mp_bar"])
    save_region(img, "qs_hp", f"快捷欄中「{buy['hp']['name']}」那一格（整格框起來，包含下面的數字）")
    save_region(img, "qs_mp", f"快捷欄中「{buy['mp']['name']}」那一格（整格框起來，包含下面的數字）")
    save_template(img, "inv_button", REQUIRED_TEMPLATES["inv_button"])

    pause("【第 2 階段】請打開道具欄，並切到「現金」分頁。")
    img = grab()
    save_template(img, "inv_open", REQUIRED_TEMPLATES["inv_open"])
    save_template(img, "inv_tab_cash", REQUIRED_TEMPLATES["inv_tab_cash"])
    save_template(img, "miaomiao", "妙妙圖示（只框圖示上半部，不要框到左下角的數量）")
    anchor_tab(img, "inv_tab_cash", "inv_open")

    pause(f"【第 3 階段】請雙擊妙妙打開商店。\n左邊清單捲到看得到「{buy['hp']['name']}」；右邊點「消耗」分頁。")
    img = grab()
    for n in ("shop_leave", "shop_buy", "shop_sell", "shop_tab_use", "list_up", "list_down"):
        save_template(img, n, REQUIRED_TEMPLATES[n])
    anchor_tab(img, "shop_tab_use", "shop_leave")
    save_region(img, "shop_list", REQUIRED_REGIONS["shop_list"])
    save_region(img, "inv_list", REQUIRED_REGIONS["inv_list"])
    save_template(img, buy["hp"]["template"],
                  f"左邊清單裡「{buy['hp']['name']}」那一列：框住圖示和名稱（不要框到價格以外的空白太多）")

    pause(f"【第 4 階段】左邊清單捲到看得到「{buy['mp']['name']}」。")
    img = grab()
    save_template(img, buy["mp"]["template"], f"左邊清單裡「{buy['mp']['name']}」那一列：框住圖示和名稱")

    pause(f"【第 5 階段】點一下「{buy['mp']['name']}」，再按「購買物品」，讓數量視窗跳出來。\n"
          "⚠ 不要按確認！等校正完再按取消。")
    img = grab()
    save_template(img, "qty_dialog", REQUIRED_TEMPLATES["qty_dialog"])
    save_region(img, "qty_input", REQUIRED_REGIONS["qty_input"])
    save_template(img, "dialog_ok", REQUIRED_TEMPLATES["dialog_ok"])
    save_template(img, "dialog_cancel", REQUIRED_TEMPLATES["dialog_cancel"])
    # 記錄輸入框相對於標題的位置（對話框位置不同也找得到）
    from autopot.vision import find
    m = find(img, CAL.template("qty_dialog"), 0.8)
    if m:
        r = CAL.region("qty_input")
        CAL.set_anchor("qty_input", "qty_dialog", r[0] - m.x, r[1] - m.y)
    CAL.save()
    print("\n請回遊戲按「取消」關掉數量視窗，再按「離開商店」。")

    print("\n接下來測試快捷欄數字辨識。")
    digits()
    print("\n✅ 校正完成！建議先跑：python run.py --dry-run --refill-now 看看整個流程。")


def digits() -> None:
    from autopot.app import load_digit_reader
    from autopot.digits import DigitTemplates, learn
    from autopot.vision import crop

    img = grab()
    reader = load_digit_reader()
    names = {"qs_hp": CFG["buy"]["hp"]["name"], "qs_mp": CFG["buy"]["mp"]["name"]}
    crops = {k: crop(img, CAL.region(k)) for k in names}
    reads = {k: reader.read(c) for k, c in crops.items()}
    for k, v in reads.items():
        print(f"  {names[k]}：辨識結果 = {v if v is not None else '讀不到'}")
    ans = input("看一下遊戲快捷欄，正確請直接按 Enter；不正確請輸入正確數字（例：4360 525）：").strip()
    sample_dir = DATA_DIR / "digit_samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    labels_file = sample_dir / "labels.json"
    labels = json.loads(labels_file.read_text(encoding="utf-8")) if labels_file.exists() else {}
    if ans:
        vals = ans.replace(",", " ").split()
        if len(vals) != 2 or not all(v.isdigit() for v in vals):
            print("格式不對，請輸入兩個數字，中間用空白隔開")
            return
        truth = dict(zip(names, vals))
    elif all(v is not None for v in reads.values()):
        truth = {k: str(v) for k, v in reads.items()}
    else:
        print("有讀不到的數字，請重新執行並輸入正確數字")
        return
    # 記錄每格數字第一位的位置，之後辨識時用來防止漏讀第一位
    ts = int(time.time())
    for k, c in crops.items():
        fn = f"{k}_{ts}.png"
        cv2.imwrite(str(sample_dir / fn), c)
        labels[fn] = truth[k]
    labels_file.write_text(json.dumps(labels, indent=1), encoding="utf-8")
    if not ans:
        _save_start(reader, crops, truth)
        print("✔ 辨識正確，已存成樣本")
        return
    samples = [(cv2.imread(str(sample_dir / f)), v) for f, v in labels.items() if (sample_dir / f).exists()]
    base = DigitTemplates.load(ROOT / "assets" / "digits_base.npz")
    learned, report = learn(samples, base)
    learned.save(DATA_DIR / "digits_learned.npz")
    print("學習結果：", report)
    from autopot.digits import DigitReader
    r2 = DigitReader(learned)
    for k, c in crops.items():
        print(f"  {names[k]}：重新辨識 = {r2.read(c)}（正確 {truth[k]}）")
    _save_start(r2, crops, truth)
    print("沒學過的數字可能還是會讀錯，之後數字變了可以再執行幾次 python calibrate.py digits。\n"
          "讀錯的數字會被程式過濾掉，最壞情況是改用定時補貨，不會因此亂買。")


def _save_start(reader, crops, truth) -> None:
    for k, c in crops.items():
        txt, _, pos = reader.read_detail(c)
        if txt == truth[k] and pos:
            CAL.data.setdefault("digit_start", {})[k] = int(pos[0][0])
    CAL.save()


def check() -> None:
    from autopot.app import load_digit_reader
    from autopot.vision import bar_ratio, crop, find
    img = grab()
    out = img.copy()
    print("目前畫面：")
    for n in list(REQUIRED_TEMPLATES) + [b["template"] for b in CFG["buy"].values()] + \
            [s["template"] for s in CFG.get("sell") or []]:
        if not CAL.has_template(n):
            print(f"  ✘ {n}：還沒校正")
            continue
        m = find(img, CAL.template(n), CFG.get("match_threshold", 0.85))
        print(f"  {'✔' if m else '·'} {n}" + (f"（相似度 {m.score:.2f}）" if m else "（目前畫面上沒看到）"))
        if m:
            cv2.rectangle(out, (m.x, m.y), (m.x + m.w, m.y + m.h), (0, 255, 0), 2)
            cv2.putText(out, n, (m.x, m.y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    for n, (x, y, w, h) in CAL.data.get("regions", {}).items():
        cv2.rectangle(out, (x, y), (x + w, y + h), (0, 200, 255), 2)
        cv2.putText(out, n, (x, y + h + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
    if CAL.has_region("hp_bar"):
        print(f"  HP 條：{bar_ratio(crop(img, CAL.region('hp_bar')), 'hp'):.0%}")
        print(f"  MP 條：{bar_ratio(crop(img, CAL.region('mp_bar')), 'mp'):.0%}")
    if CAL.has_region("qs_hp") and CFG.get("ocr_enabled", False):
        r = load_digit_reader()
        print(f"  {CFG['buy']['hp']['name']} 數量：{r.read(crop(img, CAL.region('qs_hp')))}")
        print(f"  {CFG['buy']['mp']['name']} 數量：{r.read(crop(img, CAL.region('qs_mp')))}")
    if not CFG.get("ocr_enabled", False):
        print("  （快捷欄數字辨識目前關閉，改用定時補貨＋血條保護）")
    DATA_DIR.mkdir(exist_ok=True)
    p = DATA_DIR / "check.png"
    cv2.imwrite(str(p), out)
    print(f"標示圖已存到 {p}")
    miss = CAL.missing([b["template"] for b in CFG["buy"].values()])
    if miss:
        print("還缺：\n  " + "\n  ".join(miss))


def redo(name: str) -> None:
    img = grab()
    if name in REQUIRED_REGIONS:
        save_region(img, name, REQUIRED_REGIONS[name])
    else:
        save_template(img, name, REQUIRED_TEMPLATES.get(name, name))
        if name == "qty_dialog" and CAL.has_region("qty_input"):
            from autopot.vision import find
            m = find(img, CAL.template("qty_dialog"), 0.8)
            if m:
                r = CAL.region("qty_input")
                CAL.set_anchor("qty_input", "qty_dialog", r[0] - m.x, r[1] - m.y)
    CAL.save()


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        wizard()
    elif a[0] == "check":
        check()
    elif a[0] == "digits":
        digits()
    elif a[0] == "redo" and len(a) == 2:
        redo(a[1])
    elif a[0] in ("buyitem", "sell") and len(a) == 2:
        pause("請打開妙妙商店，讓要框的道具出現在清單上" + ("（右邊消耗分頁）" if a[0] == "sell" else "（左邊商品清單）"))
        lbl = (f"「{a[1]}」那一列：只框「道具名稱文字」，不要框到圖示（圖示上的數量會變）" if a[0] == "sell"
               else f"「{a[1]}」那一列：框住圖示和名稱")
        save_template(grab(), a[1], lbl)
        if a[0] == "sell":
            print(f"記得把它加到 config.yaml 的 sell: 清單，例如：\n  - template: {a[1]}\n    name: 道具名稱\n    keep_stacks: 0")
    else:
        print(__doc__)
