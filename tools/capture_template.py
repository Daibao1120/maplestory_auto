"""模板擷取小工具。

用滑鼠在遊戲畫面上框選一塊區域，存成 PNG 模板素材（供 vision 模板匹配）。
經典版為高解析重繪，模板務必以「你自己的實際遊戲畫面」擷取。

用法：
    python tools/capture_template.py --name slime --out assets/templates
    # 執行後：在彈出視窗用滑鼠拖曳框選 → Enter 確認存檔；Esc / 未框選則取消。
"""
from __future__ import annotations

import argparse
import os
import sys


def _require(mod_name):
    """延遲載入外部套件；缺套件時給友善提示並結束。"""
    try:
        return __import__(mod_name)
    except ImportError:
        print(f"[錯誤] 尚未安裝 {mod_name}。請執行： pip install -r requirements.txt")
        sys.exit(1)


def capture_fullscreen():
    """用 mss 擷取主螢幕，回傳 BGR ndarray。"""
    mss = _require("mss")
    np = _require("numpy")
    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[1])
    return np.asarray(raw)[:, :, :3].copy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="框選截圖存成模板素材")
    parser.add_argument("--name", "-n", required=True, help="模板名稱（存檔檔名，不含副檔名）")
    parser.add_argument("--out", "-o", default="assets/templates", help="輸出資料夾")
    args = parser.parse_args(argv)

    cv2 = _require("cv2")
    frame = capture_fullscreen()

    print("[提示] 用滑鼠拖曳框選模板區域，按 Enter 確認、Esc 取消。")
    roi = cv2.selectROI("capture_template", frame, showCrosshair=True)
    cv2.destroyAllWindows()

    x, y, w, h = (int(v) for v in roi)
    if w == 0 or h == 0:
        print("[取消] 未框選任何區域。")
        return 1

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, f"{args.name}.png")
    cv2.imwrite(out_path, frame[y:y + h, x:x + w])
    print(f"[完成] 已儲存模板：{out_path}（{w}x{h}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
