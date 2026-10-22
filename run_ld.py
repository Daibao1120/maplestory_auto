"""雷電模擬器（手機版）角色的自動補水。

  python run_ld.py                 開始監控（需要時自動補貨）
  python run_ld.py --dry-run       試跑：流程全部走一遍，但最後一律按「取消」，不會真的買賣
  python run_ld.py --refill-now    啟動後馬上補一次貨，之後繼續監控
  python run_ld.py --once          只補一次貨就結束（搭配 --dry-run 測試很方便）
  python run_ld.py --status        只讀一次畫面，印出 HP/MP
  python run_ld.py --shot          存一張模擬器截圖到 data_ld/shot.png（檢查用）

跟電腦版（run.py）是分開的程式，可以同時跑：
  * 設定檔 config_ld.yaml、校正和模板在 ld/、紀錄在 data_ld/
  * 用 ADB 截圖和點擊，不會動你的滑鼠，模擬器被擋住、在背景也可以
緊急停止：Ctrl+F12 或 Ctrl+C。
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import autopot.calib as calib  # noqa: E402

# 模擬器用自己的校正、模板、紀錄資料夾（不要動到電腦版的）
calib.DATA_DIR = ROOT / "data_ld"
calib.CALIB_FILE = ROOT / "ld" / "calibration.json"
calib.TEMPLATE_DIR = ROOT / "ld" / "templates"


def setup_logging() -> None:
    calib.DATA_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [模擬器] %(message)s", "%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = RotatingFileHandler(calib.DATA_DIR / "autopot_ld.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(sh)
    root.addHandler(fh)


def main() -> None:
    ap = argparse.ArgumentParser(description="雷電模擬器自動補水")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refill-now", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--shot", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    setup_logging()
    cfg = yaml.safe_load((ROOT / "config_ld.yaml").read_text(encoding="utf-8"))

    from autopot.adbwin import AdbError, AdbWindow
    one_shot = a.status or a.once or a.shot
    warned = False
    while True:
        try:
            win = AdbWindow(cfg.get("adb_serial", "emulator-5554"), cfg.get("adb_path"))
            win.connect()
            img = win.capture()
            break
        except AdbError as e:
            if one_shot:
                raise SystemExit(f"沒辦法連到雷電模擬器：{e}")
            if not warned:
                logging.warning("沒辦法連到雷電模擬器：%s（每 60 秒再試一次；模擬器開好就會自動開始）", e)
                warned = True
            time.sleep(60)
    logging.info("已連上模擬器 %s（畫面 %dx%d）", win.serial, img.shape[1], img.shape[0])
    if a.shot:
        import cv2
        cv2.imwrite(str(calib.DATA_DIR / "shot.png"), img)
        print("已存 data_ld/shot.png")
        return

    from autopot.app import NotVisible, Runner
    r = Runner(cfg, win, dry_run=a.dry_run)
    if a.status or a.once:
        state = None
        for attempt in range(5):
            try:
                r.read_state()
                time.sleep(1)
                state = r.read_state()
                break
            except NotVisible as e:
                print(f"（{e}，重試 {attempt + 1}/5）")
                time.sleep(1.5)
        if state is None:
            raise SystemExit("看不到遊戲畫面：模擬器裡的楓之谷是不是在別的頁面（例如商城、設定）？")
        _, hp, mp, counts = state
        print(r.status_line(hp, mp, counts))
        if a.once:
            r.refill(counts, "手動執行 --once")
        return
    r.run(refill_on_start=a.refill_now)
    if r.restart:
        sys.exit(RESTART_CODE)       # start.bat（run.py）看到這個代碼會馬上重新啟動模擬器的補水


RESTART_CODE = 3


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已結束")
