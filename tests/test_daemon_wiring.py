"""守夜 daemon 的感知接線測試。

daemon 曾經自己寫了一份 sense()，只設了一半的 WorldState 欄位，漏掉的其中
一項是 modal——也就是測謊彈窗偵測在唯一的無人值守工具裡完全是死的。
單元測試沒抓到，因為沒有任何測試檢查「daemon 有把哪些欄位餵給核心」。
"""
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tools.overnight as ov  # noqa: E402


def daemon_src():
    return inspect.getsource(ov.run_daemon)


def test_daemon_gets_its_world_state_from_perception():
    """daemon 必須走 Perception，不可以再長出第二份私有感知實作。

    有兩份實作時，selftest 驗的是 Perception、實跑的是另一份，
    「檢查通過」完全不代表實跑會動。
    """
    src = daemon_src()
    assert "Perception(cfg, ROOT)" in src
    assert "per.snapshot(" in src


def test_daemon_does_not_reintroduce_hardcoded_ui_coordinates():
    """寫死的 ROI 是解析度一改就全錯的老問題（實測 2736→3840 全毀）。"""
    src = daemon_src()
    for bad in ("1520, 1556, 1020, 1280", "frame[1540:1596, 560:1300]",
                "def locate_hp", "def red_mask"):
        assert bad not in src, f"daemon 又出現寫死的 UI 座標／私有讀值：{bad}"


def test_perception_reports_that_modal_detection_ran():
    """modal=None 同時代表「沒彈窗」和「沒接線」；必須有 modal_ok 分開兩者。"""
    w = ov.WorldState(now=0.0)
    assert w.modal_ok is False, "預設要是「不知道」，也就是最保守的那一側"
    src = inspect.getsource(ov.Perception.snapshot)
    assert "w.modal_ok = True" in src


def test_in_game_does_not_require_the_mp_bar():
    """MP 條靠藍色填充段辨識，快見底時找不到。

    要求 HP+MP 兩條都在才算「在遊戲中」，等於 MP 一低就整夜停手；
    對耗魔職業幾乎必中，而使用者要的是全職業支援。
    """
    src = inspect.getsource(ov.Perception.snapshot)
    assert "if (_hp_b and _mp_b)" not in src
    assert "0 if _hp_b else" in src


def test_daemon_records_why_it_is_not_farming():
    """整夜零經驗時要查得出原因：平台多寬、看到幾隻怪、有沒有彈窗。"""
    src = daemon_src()
    for key in ("span_width", "wide_enough", "mon_left", "modal", "in_game",
                "exp_per_hour"):
        assert f'"{key}"' in src, f"狀態檔沒有記錄 {key}，事後無從排查"


def test_daemon_logs_when_it_loses_foreground():
    """失去前景會靜默停擺。半夜一個通知彈窗就報銷整夜，且 log 一片空白。"""
    src = daemon_src()
    assert "fg_lost" in src and "視窗失去前景" in src


# ---- 三個工具必須建出設定相同的核心 ----

CFG = {
    "keys": {"hp_potion": "delete"},
    "survival": {"heal_mode": "external", "last_resort_hp": 0.2},
    "vision": {"others": {"on_others": "idle"}},
    "combat": {"descend_enabled": False,
               "patrol": {"enabled": True, "min_seconds": 11,
                          "max_seconds": 22, "step_seconds": 0.5}},
    "class_profile": {"name": "archer", "attack": {"mode": "hold", "key": "ctrl"}},
}


def test_build_core_applies_every_setting():
    c = ov.build_core(CFG)
    assert c.descend_enabled is False
    assert c.patrol_enabled is True
    assert c.patrol_every == (11.0, 22.0)
    assert c.patrol_step == 0.5
    assert c.on_others == "idle"
    assert c.heal_mode == "external"


def test_all_three_tools_build_the_core_the_same_way():
    """selftest 檢查的、控制台跑的、守夜實跑的，必須是同一種核心。

    這三處原本各拼一份，只有守夜是完整的：selftest 的整備度報告因此在講
    實跑用不到的門檻，而控制台在動作模式下會走下平台。
    """
    import inspect
    import tools.control_panel as cp
    import tools.selftest as st
    assert "build_core(" in inspect.getsource(ov.run_daemon)
    for mod, name in ((cp, "control_panel"), (st, "selftest")):
        src = inspect.getsource(mod)
        assert "build_core(" in src, f"{name} 沒用共用接線"
        # 只有 build_core 可以直接呼叫建構式；其他地方一律走它，
        # 否則接線又會各自漂走。
        assert "NightWatchCore(" not in src, (
            f"{name} 又自己拼了一個核心 → 設定會和實跑不一致")
