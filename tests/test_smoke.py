"""煙霧測試：確認專案骨架可正常 import，且基本資料模型／指令運作正常。

設計為「不需安裝 mss / opencv / pydirectinput 也能通過」——各層對外部套件採延遲載入。
可用 `python -m pytest` 或直接 `python tests/test_smoke.py` 執行。
"""
import os
import sys

# 把專案根目錄加入 sys.path，讓 `import src.xxx` 可運作
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_import_all_modules():
    """所有主要模組都應可安全 import（即使缺外部套件）。"""
    import src
    import src.main
    import src.capture
    import src.vision
    import src.input
    import src.engine
    import src.routine
    import src.commands
    import src.rune
    assert src.__version__


def test_command_book_registry():
    """command book 應註冊預期的動作，並可建立指令實例。"""
    from src.commands import CommandBook
    book = CommandBook()
    for action in ("move", "attack", "buff", "potion", "change_channel"):
        assert action in book.actions
    cmd = book.create("attack", {"facing": "left", "repeat": 2})
    assert cmd.args["repeat"] == 2


def test_unknown_command_raises():
    """未知動作應丟出 KeyError。"""
    from src.commands import CommandBook
    book = CommandBook()
    try:
        book.create("no_such_action")
    except KeyError:
        pass
    else:
        raise AssertionError("未知動作應丟出 KeyError")


def test_routine_parse():
    """parse_routine 應能把 dict 轉成 Routine（不需 PyYAML）。"""
    from src.routine.routine import parse_routine
    data = {
        "name": "測試路線",
        "points": [
            {"label": "A", "x": 1, "y": 2, "commands": [{"action": "buff"}]},
            {"label": "B", "x": 3, "y": 4, "commands": []},
        ],
    }
    routine = parse_routine(data)
    assert routine.name == "測試路線"
    assert len(routine.points) == 2
    assert routine.points[0].label == "A"
    assert routine.label_index("B") == 1


def test_engine_constructs_in_dry_run():
    """引擎應可在 dry_run 下建立並 setup（不需遊戲、不送按鍵）。"""
    from src.engine import BotEngine
    config = {
        "capture": {"backend": "mss"},
        "keys": {"attack": "ctrl"},
        "routine": {},  # 不指定路線檔，避免依賴檔案
    }
    engine = BotEngine(config, dry_run=True)
    engine.setup()
    assert engine.controller is not None
    assert engine.command_book is not None


def test_input_controller_dry_run():
    """dry_run 的 InputController 應可在無 pydirectinput 時 tap 不出錯。"""
    from src.input import InputController
    ctrl = InputController(
        humanize={"action_delay_min": 0.0, "action_delay_max": 0.0},
        dry_run=True,
    )
    ctrl.tap("ctrl", duration=0.0)  # duration=0 避免測試等待


if __name__ == "__main__":
    # 直接執行時，手動跑所有 test_ 函式（不依賴 pytest）
    import traceback

    failures = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"PASS  {_name}")
            except Exception:
                failures += 1
                print(f"FAIL  {_name}")
                traceback.print_exc()
    print("-" * 40)
    print("全部通過" if failures == 0 else f"{failures} 個測試失敗")
    sys.exit(1 if failures else 0)
