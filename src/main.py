"""進入點：載入設定 → 建立引擎 → 啟動主迴圈。

即使未安裝 mss / opencv-python / pydirectinput，本檔仍可被 import
（各層採延遲載入，且 engine 於 build_engine() 內才載入）；
只有實際執行 main() 並開始擷取畫面時，才需要完整環境。

用法：
    python -m src.main --config config/settings.yaml
    python -m src.main --config config/settings.yaml --dry-run   # 不實際送鍵
"""
from __future__ import annotations

import argparse
import sys


def load_config(path):
    """從 YAML 檔載入設定 dict。"""
    try:
        import yaml  # 延遲載入
    except ImportError:
        raise RuntimeError("尚未安裝 PyYAML。請執行： pip install PyYAML")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_engine(config, dry_run=False):
    """依設定建立並初始化 BotEngine。"""
    from src.engine import BotEngine  # 延遲載入，確保 import src.main 本身很輕量
    engine = BotEngine(config, dry_run=dry_run)
    engine.setup()
    return engine


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="楓之谷經典版 圖色辨識自動化腳本")
    parser.add_argument("--config", "-c", default="config/settings.yaml", help="設定檔路徑")
    parser.add_argument("--dry-run", action="store_true", help="不實際送出按鍵，只印出動作（測試用）")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    print("=" * 56)
    print(" 楓之谷經典版自動化腳本   僅供學習研究，使用風險自負")
    print("=" * 56)

    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"[錯誤] 找不到設定檔：{args.config}")
        print("       請先複製 config/settings.example.yaml 為 settings.yaml 再修改。")
        return 1
    except RuntimeError as e:
        print(f"[錯誤] {e}")
        return 1

    engine = build_engine(config, dry_run=args.dry_run)
    print(f"[資訊] 已載入路線：{engine.routine.name if engine.routine else '（無）'}")
    print("[資訊] 開始執行，按 Ctrl+C 可中止。")

    try:
        engine.start()
    except RuntimeError as e:
        print(f"[錯誤] 執行期錯誤：{e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
