"""校正資料：模板圖與區域座標的存取。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from .vision import load_template

if getattr(sys, "frozen", False):
    # 打包成 exe（PyInstaller）：設定檔、校正資料、模板放在 exe 旁邊；程式內建的參考圖在暫存的解壓資料夾
    ROOT = Path(sys.executable).resolve().parent
    BUNDLE = Path(getattr(sys, "_MEIPASS", ROOT))
else:
    ROOT = Path(__file__).resolve().parent.parent
    BUNDLE = ROOT
ASSETS = BUNDLE / "assets"
TEMPLATE_DIR = ROOT / "templates"
DATA_DIR = ROOT / "data"
CALIB_FILE = DATA_DIR / "calibration.json"

# 必要的模板（名稱: 說明）
REQUIRED_TEMPLATES = {
    "inv_button": "畫面下方工具列的「道具欄」按鈕（背包圖示）",
    "inv_open": "道具欄視窗標題「ITEM INVENTORY」",
    "inv_tab_cash": "道具欄裡的「現金」分頁",
    "miaomiao": "現金欄裡的妙妙圖示",
    "shop_leave": "商店的「離開商店」按鈕",
    "shop_buy": "商店的「購買物品」按鈕",
    "shop_sell": "商店的「販售物品」按鈕",
    "shop_tab_use": "商店右邊（你的背包）的「消耗」分頁",
    "list_up": "清單捲軸最上面的小箭頭 ▲",
    "list_down": "清單捲軸最下面的小箭頭 ▼",
    "qty_dialog": "購買數量視窗的標題「要購買幾個？」",
    "dialog_ok": "對話框的「確認」按鈕",
    "dialog_cancel": "對話框的「取消」按鈕",
}
REQUIRED_REGIONS = {
    "hp_bar": "HP 紅色血條（只框條本身，不要框到文字）",
    "mp_bar": "MP 藍色魔力條（只框條本身）",
    "qs_hp": "快捷欄中 HP 補品（馴鹿奶）那一格，整格框起來",
    "qs_mp": "快捷欄中 MP 補品（刨冰）那一格，整格框起來",
    "shop_list": "商店左邊的商品清單（不含捲軸）",
    "inv_list": "商店右邊的背包清單（不含捲軸）",
    "qty_input": "購買數量視窗裡的數字輸入框",
}


class Calibration:
    def __init__(self, data: dict):
        self.data = data
        self._tpl_cache: dict[str, np.ndarray] = {}

    @classmethod
    def load(cls) -> "Calibration":
        if CALIB_FILE.exists():
            return cls(json.loads(CALIB_FILE.read_text(encoding="utf-8")))
        return cls({"regions": {}, "window_size": None, "anchors": {}})

    def save(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        CALIB_FILE.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 區域 ----
    def region(self, name: str):
        r = self.data.get("regions", {}).get(name)
        if r is None:
            raise KeyError(f"還沒校正區域「{name}」，請先執行 calibrate.py")
        return r

    def has_region(self, name: str) -> bool:
        return name in self.data.get("regions", {})

    def set_region(self, name: str, rect) -> None:
        self.data.setdefault("regions", {})[name] = [int(v) for v in rect]

    # 記錄某區域相對於某模板位置的偏移（例如輸入框相對於對話框標題）
    def set_anchor(self, name: str, tpl_name: str, dx: int, dy: int) -> None:
        self.data.setdefault("anchors", {})[name] = {"tpl": tpl_name, "dx": int(dx), "dy": int(dy)}

    def anchor(self, name: str):
        return self.data.get("anchors", {}).get(name)

    # ---- 模板 ----
    def template(self, name: str) -> np.ndarray:
        if name not in self._tpl_cache:
            self._tpl_cache[name] = load_template(TEMPLATE_DIR / f"{name}.png")
        return self._tpl_cache[name]

    def has_template(self, name: str) -> bool:
        return (TEMPLATE_DIR / f"{name}.png").exists()

    def missing(self, extra_templates: list[str] = ()) -> list[str]:
        miss = [f"模板 {n}：{d}" for n, d in REQUIRED_TEMPLATES.items() if not self.has_template(n)]
        miss += [f"模板 {n}" for n in extra_templates if not self.has_template(n)]
        miss += [f"區域 {n}：{d}" for n, d in REQUIRED_REGIONS.items() if not self.has_region(n)]
        return miss
