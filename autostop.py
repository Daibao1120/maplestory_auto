"""快升等時自動按「自動狩獵」停止。

用法：
    python autostop.py              # EXP 到 98% 停
    python autostop.py --at 99.2    # EXP 到 99.2% 停
    python autostop.py --pick 2     # 開了兩個遊戲視窗時，指定第 2 個
    python autostop.py --dry-run    # 只看不按（測試用）
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from autopot.autostop import AutoStop  # noqa: E402

log = logging.getLogger("autopot.autostop")


def beep(n: int = 3) -> None:
    try:
        import winsound
        for _ in range(n):
            winsound.Beep(1500, 250)
            time.sleep(0.1)
    except Exception:
        print("\a")


def save_debug(img, st, r) -> None:
    """第一次讀到 EXP 時，把畫面和框到的 EXP 條存起來（讀錯時拿來檢查）。"""
    try:
        import cv2
        dbg = img.copy()
        y, bars = st._bars
        for (x0, x1), c in zip(bars, [(0, 0, 255), (255, 0, 0), (0, 200, 255)]):
            cv2.rectangle(dbg, (x0, y - 8), (x1, y + 8), c, 2)
        cv2.putText(dbg, f"EXP {r * 100:.1f}%", (bars[2][0], y - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        cv2.imwrite(str(ROOT / "data" / "exp_debug.png"), dbg)
        log.info("第一次讀到 EXP %.1f%%（讀的位置存在 data/exp_debug.png，數字不對的話把這張圖給我）", r * 100)
    except Exception:
        pass


def pick_window(keyword: str, pick: int | None):
    from autopot import window as W
    import ctypes
    cands = [(h, t) for h, t in W.list_windows() if keyword in t]
    if not cands:
        raise SystemExit(f"找不到標題包含「{keyword}」的遊戲視窗")
    sizes = [W.GameWindow._client_size(h) for h, _ in cands]
    if len(cands) == 1:
        idx = 0
    elif pick:
        idx = pick - 1
    else:
        print("找到好幾個遊戲視窗：")
        for i, ((h, t), (w, hh)) in enumerate(zip(cands, sizes), 1):
            print(f"  {i}. {t}  （畫面 {w}x{hh}）")
        idx = int(input("要監看第幾個？輸入數字後按 Enter：").strip()) - 1
    gw = W.GameWindow(keyword)
    gw.hwnd = cands[idx][0]
    log.info("監看視窗：%s（畫面 %dx%d）", cands[idx][1], *sizes[idx])
    return gw


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", type=float, default=98.0, help="EXP 到幾 %% 就停（預設 98）")
    ap.add_argument("--pick", type=int, default=None, help="有多個遊戲視窗時選第幾個")
    ap.add_argument("--dry-run", action="store_true", help="只看不按")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--no-topmost", action="store_true", help="不要把遊戲視窗固定在最上層")
    args = ap.parse_args()
    (ROOT / "data").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(ROOT / "data" / "autostop.log", encoding="utf-8")])
    title = "MapleStory Worlds"
    try:
        import yaml
        title = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")).get("window_title", title)
    except Exception:
        pass
    win = pick_window(title, args.pick)
    st = AutoStop(at=args.at / 100)
    log.info("開始監看：EXP 到 %.1f%% 時按「自動狩獵」停止%s。結束請按 Ctrl+C",
             args.at, "（試跑：只看不按）" if args.dry_run else "")
    if not args.no_topmost:
        try:
            if win.is_minimized():
                import ctypes
                ctypes.windll.user32.ShowWindow(win.hwnd, 9)      # 縮小了就還原
            win.set_topmost(True)
            log.info("已把楓之谷視窗固定在最上層（程式結束時會恢復）")
        except Exception as e:
            log.warning("沒辦法把遊戲視窗固定在最上層：%s", e)
    try:
        loop(win, st, args)
    finally:
        if not args.no_topmost:
            try:
                win.set_topmost(False)
            except Exception:
                pass


def do_click(win, st, img, r, args, retry: bool = False) -> bool:
    btn = st.button(img, fresh=True)        # 每次都重新找（視窗可能移動過）
    what = "再按一次" if retry else "按"
    if btn is None:
        log.error("EXP 到了 %.1f%%，但畫面上找不到「自動狩獵」按鈕！請自己按停止", r * 100)
        beep(5)
        return False
    if args.dry_run:
        log.info("[試跑] EXP %.1f%%，這時候會%s「自動狩獵」(%d, %d)", r * 100, what, *btn.center)
        return True
    prev = win.foreground()
    try:
        import pyautogui
        mx, my = pyautogui.position()
    except Exception:
        pyautogui = None
        mx = my = None
    win.activate()
    win.click(*btn.center)
    if retry:
        log.warning("按了停止之後 EXP 還在漲（%.1f%%），可能沒按到，已再按一次「自動狩獵」", r * 100)
    else:
        log.info("EXP %.1f%%：已按「自動狩獵」停止", r * 100)
    if pyautogui is not None and mx is not None:
        pyautogui.moveTo(mx, my)
    win.restore_foreground(prev)
    beep(2)
    return True


def loop(win, st, args) -> str:
    """監看迴圈。回傳結束原因：'stopped' / 'leveled' / 'no_button'。"""
    last_status = 0.0
    warned_above = False
    while True:
        now = time.time()
        try:
            img = win.capture()
        except Exception as e:
            log.warning("截圖失敗：%s", e)
            time.sleep(5)
            continue
        act = st.step(img, now)
        r = st.history[-1][1] if st.history else None
        if r is not None and not getattr(st, "_dumped", False):
            st._dumped = True
            save_debug(img, st, r)
        if act == "unknown" and now - last_status > 30:
            log.info("讀不到 EXP 條（視窗被擋住、縮小，或滑鼠停在 EXP 條上）")
            last_status = now
        elif r is not None and now - last_status > 60:
            rate = st.rate_per_min(now)
            eta = ""
            thr = st.threshold(now)
            if rate and rate > 0 and r < thr:
                eta = f"，大約 {(thr - r) / rate:.0f} 分鐘後到 {thr * 100:.1f}%"
            log.info("EXP %.1f%%%s", r * 100, eta)
            last_status = now
        if r is not None and not st.seen_below and r >= st.threshold(now) and st.clicked_at is None and not warned_above:
            warned_above = True
            log.warning("一開始 EXP 就已經超過 %.1f%%：沒看到它在漲，不確定自動狩獵是不是開著，所以不會自己按。"
                        "要停的話請手動按一下「自動狩獵」", st.threshold(now) * 100)
        if act in ("click", "retry_click"):
            ok = do_click(win, st, img, r, args, retry=act == "retry_click")
            if not ok and act == "click":
                return "no_button"
            if args.dry_run:
                log.info("[試跑] 結束（真正執行時會在這時候按下停止）")
                return "dry"
        elif act == "leveled":
            if st.leveled_after_click:
                log.error("按了停止之後還是升級了！可能沒有按到，請檢查遊戲")
                beep(5)
                return "leveled"
            log.info("升級了")
        if st.clicked_at is not None and st.stopped_for(now) >= 180:
            log.info("按了停止後 EXP 已經 3 分鐘沒有再漲，確定停下來了，程式結束")
            return "stopped"
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("結束")
