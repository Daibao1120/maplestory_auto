"""楓之谷自動補水 主程式。

  python run.py                    開始監控（需要時自動補貨）
  python run.py --dry-run          試跑：流程全部走一遍，但最後一律按「取消」，不會真的買賣
  python run.py --refill-now       啟動後馬上補一次貨，之後繼續監控
  python run.py --once             只補一次貨就結束（搭配 --dry-run 測試很方便）
  python run.py --status           只讀一次畫面，印出 HP/MP 與補品數量

緊急停止：把滑鼠甩到螢幕「左上角」，或按 F12 / Ctrl+C。
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler

import yaml

from autopot.calib import DATA_DIR, ROOT

RESTART_CODE = 3          # 收到 restart 指令時的結束代碼（start.bat 看到就重新執行）


def setup_logging() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = RotatingFileHandler(DATA_DIR / "autopot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(sh)
    root.addHandler(fh)


def main() -> None:
    ap = argparse.ArgumentParser(description="楓之谷自動補水")
    ap.add_argument("--dry-run", action="store_true", help="試跑，不真的買賣")
    ap.add_argument("--refill-now", action="store_true", help="啟動後立刻補一次")
    ap.add_argument("--once", action="store_true", help="補一次就結束")
    ap.add_argument("--status", action="store_true", help="只印出目前狀態")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    setup_logging()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

    from autopot.app import Runner
    from autopot.window import GameWindow

    win = GameWindow(cfg["window_title"], cfg.get("window_exclude"))
    if not win.locate():
        raise SystemExit(f"找不到遊戲視窗（標題包含「{cfg['window_title']}」），請先開遊戲")
    # 遊戲視窗大小跟校正時不一樣 → 先自動調回去（不用自己拖到剛好那個大小）
    try:
        from autopot.calib import Calibration
        ws = Calibration.load().data.get("window_size")
        if ws:
            now_size = win.client_rect()[2:]
            if tuple(now_size) != tuple(ws):
                got = win.resize_client(int(ws[0]), int(ws[1]))
                print(f"遊戲視窗 {now_size[0]}x{now_size[1]} → 已調成 {got[0]}x{got[1]}（校正時 {ws[0]}x{ws[1]}）")
    except Exception as e:
        print(f"（自動調整視窗大小失敗：{e}）")
    if cfg.get("arrange_windows", True):
        try:
            win.arrange_side_by_side()
        except Exception as e:
            print(f"（排列視窗失敗，不影響執行：{e}）")
    r = Runner(cfg, win, dry_run=a.dry_run)

    if a.status or a.once:
        from autopot.app import NotVisible
        # 這個命令視窗可能擋住遊戲 → 先把遊戲叫到前面再讀
        state = None
        for attempt in range(5):
            try:
                win.activate()
                time.sleep(1.0)
                r.read_state()
                time.sleep(1)
                state = r.read_state()
                break
            except NotVisible as e:
                print(f"（{e}，重試 {attempt + 1}/5）")
                time.sleep(1.5)
        if state is None:
            raise SystemExit("看不到遊戲畫面：請確認遊戲視窗沒有被縮小，而且整個視窗都在螢幕內")
        _, hp, mp, counts = state
        r.ocr_ok = r.counts_reliable(time.time())
        print(r.status_line(hp, mp, counts), "｜數字辨識", "正常" if r.ocr_ok else "不可用")
        if a.once:
            r.refill(counts, "手動執行 --once")
        return
    child = start_emulator(cfg, a)
    sup = EmulatorSupervisor(child, lambda: start_emulator(cfg, a))
    sup.start()
    try:
        r.run(refill_on_start=a.refill_now)
    finally:
        sup.stop()
    if r.restart:
        # 收到 restart 指令：結束代碼 3 → start.bat 看到就馬上重新執行（載入新版程式）。
        # 不用 os.execv：Windows 上 execv 會讓 start.bat 以為程式結束了、跑到 pause，
        # 你按任意鍵關掉視窗時，新開的程式也會跟著被關掉
        print("重新啟動中…")
        sys.exit(RESTART_CODE)


class EmulatorSupervisor:
    """模擬器的補水（子程式）結束了就重新啟動它：
      * 結束代碼 3 = 收到 restart 指令 → 馬上重開（載入新版程式）
      * 其他（當掉）→ 60 秒後重開
    電腦版結束時一起關掉。"""

    def __init__(self, child, spawn, sleep=None, delay=60.0):
        import threading
        self.child = child
        self.spawn = spawn
        self.delay = delay
        self._sleep = sleep or time.sleep
        self._stop = threading.Event()
        self._t = None
        self.restarts = 0

    def start(self) -> None:
        import threading
        if self.child is None:
            return
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def step(self) -> bool:
        """子程式結束了就重開一次。回傳是否重開了。"""
        if self.child is None or self._stop.is_set():
            return False
        code = self.child.poll()
        if code is None:
            return False
        if code != 3:
            print(f"（模擬器的補水結束了（代碼 {code}），{self.delay:.0f} 秒後重新啟動）")
            self._sleep(self.delay)
            if self._stop.is_set():
                return False
        self.child = self.spawn()
        self.restarts += 1
        return self.child is not None

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.step()
            self._stop.wait(2.0)

    def stop(self) -> None:
        self._stop.set()
        stop_emulator(self.child)


def start_emulator(cfg, a, popen=None):
    """config.yaml 的 emulator.enabled 是 true → 同時在背景跑雷電模擬器的補水（run_ld.py）。
    兩個程式的訊息都印在同一個視窗（模擬器的前面有 [模擬器]），結束時一起關掉。"""
    em = cfg.get("emulator") or {}
    if not em.get("enabled") or not (ROOT / "run_ld.py").exists():
        return None
    import subprocess
    args = [sys.executable, str(ROOT / "run_ld.py")]
    if a.dry_run:
        args.append("--dry-run")
    try:
        p = (popen or subprocess.Popen)(args, cwd=str(ROOT))
    except Exception as e:
        print(f"（模擬器的補水沒辦法啟動：{e}）")
        return None
    print("也開始幫雷電模擬器的角色補水（設定在 config_ld.yaml；不要的話把 config.yaml 的 emulator.enabled 改成 false）")
    return p


def stop_emulator(p) -> None:
    if p is None:
        return
    try:
        if p.poll() is None:
            p.terminate()
            p.wait(timeout=5)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已結束")
    except Exception as e:
        if type(e).__name__ == "FailSafeException":
            print("\n🛑 偵測到滑鼠移到螢幕左上角，已緊急停止。（遊戲裡的商店若還開著請手動關閉）")
        else:
            raise
