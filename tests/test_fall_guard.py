"""防掉落（左右移動掉出平台）的迴歸測試。

涵蓋三個歷史成因：
1. 小地圖讀不到玩家時，巡邏沿上次方向盲走 → 走出平台。
2. 小地圖玩家點被其他黃色標記干擾、座標亂跳 → 朝錯的方向走。
3. 怪站在平台外（水裡）且在射程外時，走近邏輯無邊界 → 一路追出平台。

全部不需 OpenCV／遊戲環境。
"""
import os
import sys
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.vision.minimap import PlayerTracker  # noqa: E402
from src.engine import BotEngine  # noqa: E402


# ============================================================
#  PlayerTracker：小地圖玩家點跨圈追蹤
# ============================================================

def test_tracker_first_fix_takes_largest_blob():
    t = PlayerTracker()
    assert t.update([(40, 38, 9), (10, 10, 3)]) == (40, 38)


def test_tracker_prefers_nearest_to_last_over_largest():
    # 第二圈出現「更大但很遠」的黃塊（別的標記）→ 仍應跟住原本的玩家點
    t = PlayerTracker(max_jump_px=20)
    t.update([(40, 38, 9)])
    assert t.update([(120, 12, 30), (42, 38, 8)]) == (42, 38)


def test_tracker_rejects_teleport_misread():
    # 玩家點沒抓到、只剩一個遠處誤判（如 x=59→x=120 亂跳）→ 回 None（原地等待）
    t = PlayerTracker(max_jump_px=20)
    t.update([(40, 38, 9)])
    assert t.update([(120, 12, 30)]) is None
    assert t.last == (40, 38)          # 不吃誤判、保留上次可信位置


def test_tracker_reacquires_after_many_misses():
    # 連續丟棄滿 reacquire_misses 圈 → 當作真的被移動（死亡/傳送），重新定位
    t = PlayerTracker(max_jump_px=20, reacquire_misses=3)
    t.update([(40, 38, 9)])
    assert t.update([(120, 12, 30)]) is None
    assert t.update([(120, 12, 30)]) is None
    assert t.update([(120, 12, 30)]) == (120, 12)


def test_tracker_none_candidates_counts_as_miss():
    t = PlayerTracker(reacquire_misses=2)
    t.update([(40, 38, 9)])
    assert t.update([]) is None
    assert t.update([]) is None
    assert t.last is None              # 太久沒看到 → 放棄舊位置
    assert t.update([(70, 40, 5)]) == (70, 40)   # 之後重新定位


def test_tracker_first_fix_and_reacquire_pick_largest_even_unsorted():
    # 不信任呼叫端排序：亂序清單也要挑「面積最大」的塊
    t = PlayerTracker()
    assert t.update([(10, 10, 3), (40, 38, 9), (20, 20, 5)]) == (40, 38)
    t2 = PlayerTracker(max_jump_px=5, reacquire_misses=1)
    t2.update([(40, 38, 9)])
    # 只剩遠處候選 → 立即重新定位，取最大塊（在清單中間）
    assert t2.update([(80, 10, 2), (90, 12, 30), (85, 11, 4)]) == (90, 12)


def test_tracker_reset_forgets_lock():
    # 靜止看門狗靠 reset 脫離「鎖到靜止標記」：忘掉舊位置、重新取最大塊
    t = PlayerTracker()
    t.update([(40, 38, 9)])
    t.reset()
    assert t.last is None
    assert t.update([(70, 40, 3), (90, 12, 30)]) == (90, 12)


# ============================================================
#  引擎層：巡邏不盲走、追怪不出界
# ============================================================

@dataclass
class FakeDet:
    cx: int
    cy: int
    score: float = 1.0
    name: str = "speed_boost"

    @property
    def center(self):
        return (self.cx, self.cy)


class RecordingController:
    """記錄被呼叫的按鍵/滑鼠動作（取代 dry-run 印字，方便斷言）。"""

    def __init__(self):
        self.holds = []
        self.taps = []
        self.right_clicks = []

    def hold(self, key, seconds):
        self.holds.append((key, seconds))

    def tap(self, key, duration=None):
        self.taps.append((key, duration))

    def right_click(self, x, y, restore=True):
        self.right_clicks.append((x, y))

    def key_down(self, key):
        pass

    def key_up(self, key):
        pass


PLATFORMS = [
    {"name": "左平台", "x_range": [30, 48], "minimap_y": 38, "jump_x": None},
    {"name": "右平台", "x_range": [56, 75], "minimap_y": 40, "jump_x": None},
]


def make_engine(with_platforms=True):
    cfg = {
        "capture": {"backend": "mss"},
        "keys": {"move_left": "left", "move_right": "right", "attack": "ctrl"},
        "combat": {
            "attack_key": "ctrl", "combo": 2, "attack_range_px": 100,
            "patrol_left_x": 30, "patrol_right_x": 48, "patrol_edge_margin": 2,
            **({"platforms": PLATFORMS,
                "platform_switch": {"empty_loops": 3, "y_tolerance": 1,
                                    "x_tolerance": 2, "max_move_loops": 5}}
               if with_platforms else {}),
        },
        "routine": {},
    }
    eng = BotEngine(cfg, dry_run=True)
    eng.setup()
    eng.controller = RecordingController()
    return eng


def test_patrol_holds_still_when_player_unknown():
    eng = make_engine(with_platforms=False)
    eng._patrol(None)                      # 修正前：沿上次方向盲走
    assert eng.controller.holds == []


def test_patrol_turns_before_platform_edge():
    eng = make_engine(with_platforms=False)
    eng._patrol_dir = "right"
    eng._patrol((46, 38))                  # 48-2=46：提前折返
    assert [k for k, _ in eng.controller.holds] == ["left"]


def test_combat_does_not_chase_monster_beyond_edge():
    # 站在左平台右緣，怪在射程外的更右側（水裡）→ 不追（修正前會 approach 出去）
    eng = make_engine()
    monsters = [FakeDet(1200, 430)]        # 角色畫面 x=640，dx=560 > 射程 100
    acted = eng._combat((640, 400), monsters, player_mm=(46, 38))
    assert acted is False
    assert eng.controller.holds == []
    assert eng.controller.taps == []


def test_combat_still_approaches_within_bounds():
    # 站在平台中間、怪同平台但在射程外 → 照常走近
    eng = make_engine()
    monsters = [FakeDet(900, 430)]
    acted = eng._combat((640, 400), monsters, player_mm=(38, 38))
    assert acted is True
    assert [k for k, _ in eng.controller.holds] == ["right"]


def test_combat_never_approaches_blind():
    # 讀不到自己的位置 → 射程外的怪不追
    eng = make_engine()
    acted = eng._combat((640, 400), [FakeDet(1200, 430)], player_mm=None)
    assert acted is False
    assert eng.controller.holds == []


def test_combat_in_range_still_attacks_when_player_unknown():
    # 讀不到位置只是不「走」，射程內的怪仍要原地放技能（否則站著挨打）
    eng = make_engine()
    acted = eng._combat((640, 400), [FakeDet(700, 430)], player_mm=None)
    assert acted is True
    assert "ctrl" in [k for k, _ in eng.controller.taps]
    assert eng.controller.holds == []


def test_combat_does_not_chase_during_platform_switch():
    # 換平台途中（不在任何平台上）沒有邊界可依據 → 射程外的怪不追，
    # 否則 acted=True 會把導航蓋掉、原 bug 從這裡鑽回來
    eng = make_engine()
    acted = eng._combat((640, 400), [FakeDet(1200, 430)], player_mm=(52, 43))
    assert acted is False
    assert eng.controller.holds == []
    assert eng._combat_blocked is True   # 讓換平台計數把牠當「打不到」


def test_combat_blocked_side_is_sticky_until_reachable():
    eng = make_engine()
    far = [FakeDet(1200, 430)]
    # 站在右緣 → 擋下並記住「right 被擋」
    assert eng._combat((640, 400), far, player_mm=(46, 38)) is False
    assert eng._combat_blocked is True
    eng._combat_blocked = False
    # 走回平台中間，同側射程外的怪仍忽略（避免走近→被擋→巡邏→再走近的乒乓）
    assert eng._combat((640, 400), far, player_mm=(38, 38)) is False
    assert eng._combat_blocked is True
    assert eng.controller.holds == []
    # 有怪進射程 → 解除、照常攻擊
    assert eng._combat((640, 400), [FakeDet(700, 430)], player_mm=(38, 38)) is True
    # 解除後，射程外的怪又可以走近（在邊界內）
    eng._combat_blocked = False
    assert eng._combat((640, 400), far, player_mm=(38, 38)) is True
    assert [k for k, _ in eng.controller.holds] == ["right"]


def test_combat_attack_turn_tap_short_and_only_on_facing_change():
    # 轉向必須極短按（只轉身不走路），且同方向連續攻擊不重複按
    #（重複按會累積位移，貼邊打怪慢慢滑出平台）
    eng = make_engine()
    assert eng._combat((640, 400), [FakeDet(700, 430)], player_mm=(38, 38)) is True
    turn_key, turn_dur = eng.controller.taps[0]
    assert turn_key == "right"
    assert turn_dur is not None and turn_dur <= 0.05
    assert [k for k, _ in eng.controller.taps[1:]] == ["ctrl", "ctrl"]
    # 第二輪同方向 → 不再按方向鍵
    eng.controller.taps.clear()
    assert eng._combat((640, 400), [FakeDet(700, 430)], player_mm=(38, 38)) is True
    assert [k for k, _ in eng.controller.taps] == ["ctrl", "ctrl"]
    # 面向改變 → 才重新極短按轉向
    eng.controller.taps.clear()
    assert eng._combat((640, 400), [FakeDet(580, 430)], player_mm=(38, 38)) is True
    assert eng.controller.taps[0][0] == "left"


def test_combat_reposition_micro_moves_and_swaps_side():
    # 防攻擊失效：時間到 → 往平台中心小步移動＋那一輪換邊打
    eng = make_engine()
    eng._next_reposition_ts = 1.0            # 強迫「已到時間」
    assert eng._combat((640, 400), [FakeDet(700, 430)], player_mm=(38, 38)) is True
    # 平台 [30,48] 中心 39，38 在中心偏左 → 往右小步挪、步伐要小
    assert len(eng.controller.holds) == 1
    key, seconds = eng.controller.holds[0]
    assert key == "right" and seconds <= 0.2
    # 怪在右邊（正常面向 right），換邊後這一輪朝 left 打
    assert eng.controller.taps[0][0] == "left"
    assert eng._next_reposition_ts > 1.0     # 已重新排程


def test_engine_loop_routes_player_through_tracker():
    # 主迴圈必須經過 PlayerTracker：座標瞬間亂跳要被丟棄（原地等待），
    # 而不是照舊拿「最大黃塊」直接走位
    eng = make_engine(with_platforms=False)
    eng.capture.scene_provider = None        # 黑畫面：不讓合成怪干擾
    seq = [[(40, 38, 9)], [(120, 12, 30)]]   # 第二圈：疑似誤判的瞬移大黃塊
    eng.minimap.locate_player_candidates = lambda frame: seq.pop(0) if seq else []
    eng.start(max_loops=2)
    assert len(eng.controller.holds) == 1    # 只有第一圈巡邏移動，第二圈原地等待
    assert eng._tracker.last == (40, 38)     # 誤判被丟棄、保留可信位置


def test_engine_stall_watchdog_reacquires_lock():
    # 靜止看門狗：連續多圈送了移動鍵但座標完全不動（可能鎖到靜止黃點）
    # → 呼叫 tracker.reset() 重新定位
    eng = make_engine(with_platforms=False)
    eng.capture.scene_provider = None
    eng._stall_limit = 3
    eng.minimap.locate_player_candidates = lambda frame: [(40, 38, 9)]
    resets = []
    orig_reset = eng._tracker.reset
    eng._tracker.reset = lambda: (resets.append(1), orig_reset())
    eng.start(max_loops=6)
    assert resets, "座標多圈未變應觸發重新定位"


# ============================================================
#  不想要的 buff（如速度激發）自動右鍵點掉
# ============================================================

class StubBuffDetector:
    def __init__(self, dets):
        self._dets = dets
        self.template_names = ["speed_boost"]
        self.roi = None

    def detect(self, frame):
        return self._dets


def test_dispel_buff_right_clicks_detected_icon():
    eng = make_engine()
    eng.buff_detector = StubBuffDetector([FakeDet(2500, 60)])
    eng._buff_roi_given = True        # 跳過用 frame 推算 ROI（stub 不需要）
    eng._next_buff_check = 0.0
    eng._dispel_buffs(object())       # frame 只要非 None 即可
    assert eng.controller.right_clicks == [(2500, 60)]
    # 間隔內不重複點
    eng._dispel_buffs(object())
    assert len(eng.controller.right_clicks) == 1


def test_dispel_buff_silent_without_templates():
    eng = make_engine()               # 真 detector、資料夾沒有模板
    eng._next_buff_check = 0.0
    eng._dispel_buffs(object())
    assert eng.controller.right_clicks == []


# ============================================================
#  畫面邊緣探測（第二道防線：小地圖解析度太低）
# ============================================================

def _probe_frame():
    import pytest
    np = pytest.importorskip("numpy")
    # 200x300 均勻灰底：角色錨點取畫面中心 (150,100)，腳下取樣 y=140
    frame = np.full((200, 300, 3), 120, dtype=np.uint8)
    return np, frame


def test_edge_probe_same_ground_is_safe():
    from src.vision.edge_probe import probe_ahead_safe
    _np, frame = _probe_frame()
    assert probe_ahead_safe(frame, (150, 100), "left") is True
    assert probe_ahead_safe(frame, (150, 100), "right") is True


def test_edge_probe_detects_water_ahead():
    from src.vision.edge_probe import probe_ahead_safe
    np, frame = _probe_frame()
    frame[120:160, 210:280] = np.array([255, 255, 255], dtype=np.uint8)  # 右前方變色（水面）
    assert probe_ahead_safe(frame, (150, 100), "right") is False
    assert probe_ahead_safe(frame, (150, 100), "left") is True


def test_edge_probe_unsafe_when_ahead_off_screen():
    from src.vision.edge_probe import probe_ahead_safe
    _np, frame = _probe_frame()
    # 角色貼近畫面右緣 → 前方取樣超出畫面 → 不安全
    assert probe_ahead_safe(frame, (290, 100), "right") is False


def test_edge_probe_no_veto_without_frame():
    from src.vision.edge_probe import probe_ahead_safe
    assert probe_ahead_safe(None, (0, 0), "right") is True


def test_patrol_visual_veto_turns_back():
    import pytest
    np = pytest.importorskip("numpy")
    eng = make_engine(with_platforms=False)
    frame = np.full((200, 300, 3), 120, dtype=np.uint8)
    frame[120:160, 210:280] = np.array([255, 255, 255], dtype=np.uint8)  # 右前方是水
    eng._last_frame = frame           # 角色錨點 = 畫面中心 (150,100)
    eng._patrol_dir = "right"
    eng._patrol((38, 38))             # 小地圖仍在邊界內，但畫面說右邊不是地面
    assert [k for k, _ in eng.controller.holds] == ["left"]
