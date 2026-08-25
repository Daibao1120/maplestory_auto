r"""重截角色頭盔模板。

腳本靠「頭盔長什麼樣」在畫面上找到你的角色——同層判定和「這個傷害數字是不是
我打的」都要用到。換帽子（或換角色）之後就得重截一張，否則定位失效，腳本會
退回「照原節奏攻擊」，也就是又會朝空氣射。

用法：把角色站在空曠處、不要有其他玩家擋住，然後

    .venv\Scripts\python.exe tools\grab_helmet.py

它會截一張圖存到 logs/helmet_pick.png，你用小畫家看一下頭盔中心在哪，再跑

    .venv\Scripts\python.exe tools\grab_helmet.py --at X Y

X Y 是頭盔中心在那張圖上的座標。存成 assets/templates/player/helmet_01.png。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 模板尺寸（原始解析度）。實測 2736 寬的畫面用 119x50 效果最好。
TW, TH = 119, 50


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--at", nargs=2, type=int, metavar=("X", "Y"),
                   help="頭盔中心在 logs/helmet_pick.png 上的座標")
    p.add_argument("--slot", type=int, default=1,
                   help="存成 helmet_0N.png（預設 1）。想保留舊的就換個號碼")
    args = p.parse_args(argv)

    import cv2
    import yaml
    from src.capture import ScreenCapture

    with open(os.path.join(ROOT, "config", "settings.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cap = ScreenCapture(backend="mss", window_title=cfg["window"]["title"])
    raw = cap.grab()
    cap.close()
    if raw is None:
        print("[錯誤] 抓不到畫面。遊戲開著嗎？")
        return 1
    h, w = raw.shape[:2]

    if not args.at:
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        out = os.path.join(ROOT, "logs", "helmet_pick.png")
        cv2.imwrite(out, raw)
        print(f"已存 {out}（{w}x{h}）")
        print("用小畫家打開，把游標移到你角色的頭盔中心，記下左下角顯示的座標，再跑：")
        print(r"  .venv\Scripts\python.exe tools\grab_helmet.py --at X Y")
        return 0

    cx, cy = args.at
    x, y = cx - TW // 2, cy - TH // 2
    if not (0 <= x and 0 <= y and x + TW <= w and y + TH <= h):
        print(f"[錯誤] 座標 ({cx},{cy}) 太靠邊，截不出 {TW}x{TH} 的模板")
        return 1
    dst = os.path.join(ROOT, "assets", "templates", "player",
                       f"helmet_{args.slot:02d}.png")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    cv2.imwrite(dst, raw[y:y + TH, x:x + TW])
    print(f"已存 {dst}")
    print("模板會在載入時自動鏡像，所以只要截「朝左」或「朝右」其中一個方向即可。")
    print("多截幾張不同動作（走路、攻擊、被打閃白）放不同 slot，鎖定會更穩。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
