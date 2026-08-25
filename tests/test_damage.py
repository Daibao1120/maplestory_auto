"""傷害數字偵測與角色定位——用 27 幀實機連續畫面驗證，不用合成圖。

這批 fixture 是使用者實際在戰火之地打鱷魚時擷取的（2026-08-25）：畫面上有
開著的 UI 視窗、聊天粉紅文字、血條、右側通知，以及**另一個玩家在上層平台
打怪**。最後那一項是關鍵——他的傷害數字佔了畫面上橘字的多數，把它們當成
自己的，腳本就會一直以為打得到而朝空氣開火。
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

cv2 = pytest.importorskip("cv2")

from src.vision import DamageWatcher, PlayerLocator, find_damage  # noqa: E402

FRAMES = sorted(glob.glob(os.path.join(ROOT, "tests", "fixtures", "burst", "*.jpg")))
# 實測（見上方說明）：這些幀裡自己確實打到了東西
MINE = {6, 13, 14, 15, 23, 24, 25, 26}
# 這些幀有橘字，但屬於上層平台的另一個玩家
OTHERS_ONLY = {1, 2, 20, 21, 22}


@pytest.fixture(scope="module")
def frames():
    if not FRAMES:
        pytest.skip("沒有實機 fixture")
    return [cv2.imread(f) for f in FRAMES]


# ---- 角色定位 ----

def test_helmet_locator_tracks_the_player_every_frame(frames):
    """同層判定與傷害歸屬都建立在這上面。角色不在畫面中央——實測腳底 y≈677，
    畫面中央 y=404，差 273px。把中央當角色位置，整個判斷都是錯的。"""
    loc = PlayerLocator(root=ROOT)
    assert loc.available, "頭盔模板不見了"
    feet = [loc.find(f) for f in frames]
    locked = [p for p in feet if p]
    assert len(locked) >= len(frames) - 2, f"只鎖定 {len(locked)}/{len(frames)} 幀"
    ys = [p[1] for p in locked]
    assert max(ys) - min(ys) <= 20, f"腳底高度跳動 {max(ys)-min(ys)}px → 鎖錯東西"
    assert 600 < sum(ys) / len(ys) < 720, "腳底位置離實測值太遠"


def test_locator_reports_facing(frames):
    loc = PlayerLocator(root=ROOT)
    faces = set()
    for f in frames:
        loc.find(f)
        if loc.facing:
            faces.add(loc.facing)
    assert faces <= {"left", "right"} and faces


# ---- 傷害數字 ----

def test_finds_damage_numbers_without_flooding_on_ui(frames):
    """舊做法用桃紅色帶，27 幀掃出 3035 個色塊、0 個是真傷害（那是其他玩家的
    粉紅技能特效）。字元幾何過濾之後應該是個位數等級。"""
    total = sum(len(find_damage(f)) for f in frames)
    assert 10 <= total <= 60, f"27 幀找到 {total} 組 → 太少或又在抓 UI"


def test_separates_damage_dealt_from_damage_taken(frames):
    kinds = {g.kind for f in frames for g in find_damage(f)}
    assert kinds == {"dealt", "taken"}, f"只認得 {kinds}"


def test_other_players_damage_is_not_counted_as_mine(frames):
    """這是整件事的關鍵。這張圖上另一個玩家在上層平台打怪，他的傷害數字
    如果被算成自己的，腳本就會一直以為打得到。"""
    loc, w = PlayerLocator(root=ROOT), DamageWatcher()
    got = set()
    for i, f in enumerate(frames):
        if w.update(f, i * 0.28, loc.find(f)):
            got.add(i)
    leaked = got & OTHERS_ONLY
    assert not leaked, f"第 {sorted(leaked)} 幀把別人的傷害當成自己的"


def test_recognises_the_frames_where_the_player_really_hit(frames):
    loc, w = PlayerLocator(root=ROOT), DamageWatcher()
    got = set()
    for i, f in enumerate(frames):
        if w.update(f, i * 0.28, loc.find(f)):
            got.add(i)
    assert len(got & MINE) >= len(MINE) - 2, f"實際打到的幀只認出 {sorted(got & MINE)}"


def test_without_a_player_position_nothing_is_attributed(frames):
    """定位不到就無法歸屬。認錯成「打得到」會讓腳本對空氣開火——寧可當沒打到。"""
    w = DamageWatcher()
    assert all(w.update(f, i * 0.28, None) == [] for i, f in enumerate(frames))


def test_hitting_has_a_memory_window():
    w = DamageWatcher()
    w.last_dealt = 100.0
    assert w.hitting(100.5, memory=1.2)
    assert not w.hitting(102.0, memory=1.2)


def test_is_fast_enough_for_the_control_loop(frames):
    """定位＋傷害要在 0.25 秒的節流裡跑完，目標 < 110 ms。"""
    import time
    loc, w = PlayerLocator(root=ROOT), DamageWatcher()
    for i, f in enumerate(frames[:5]):
        w.update(f, i, loc.find(f))
    t0 = time.perf_counter()
    for i, f in enumerate(frames[5:]):
        w.update(f, i, loc.find(f))
    ms = (time.perf_counter() - t0) * 1000 / max(1, len(frames) - 5)
    assert ms < 110, f"每幀 {ms:.0f} ms，會把攻擊迴圈拖垮"
