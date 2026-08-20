"""平台內巡邏測試：提高遇怪率，但寧可不走也不掉下去。

站著不動只能打「走過來的怪」，沿平台移動可明顯提高每小時經驗。過去移動
老是掉平台是因為量不到邊界；現在 platform_span 可靠了才敢做，且每一步都
要先確認該方向有足夠餘裕。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.overnight import ClassProfile, NightWatchCore, WorldState  # noqa: E402


def W(now, **kw):
    return WorldState(now=now, **kw)


def SPAN(width=30.0, dl=15.0, dr=15.0):
    return {"width": width, "dist_left": dl, "dist_right": dr}


def steps(acts):
    return [a.arg for a in acts if a.verb == "step"]


def patrolled(c, t, span):
    """跑一圈，回傳「這一圈是否為巡邏」與方向。

    不能直接數 step——防失效小碎步與平台警衛也會走路；用 stats.patrols
    的增量才問得準。
    """
    before = c.stats.patrols
    acts = c.tick(W(t, hp=0.9, pos=(50, 90), span=span))
    if c.stats.patrols == before:
        return None
    d = [a.arg[0] for a in acts if a.verb == "step"]
    return d[0] if d else "?"


def farming(patrol=True, every=(25.0, 40.0)):
    c = NightWatchCore(heal_mode="external", profile=ClassProfile())
    c.patrol_enabled = patrol
    c.patrol_every = every
    c.tick(W(0, hp=0.9, pos=(50, 90), span=SPAN()))
    c.tick(W(1.0, hp=0.9, pos=(50, 90), span=SPAN()))
    assert c.state == "FARM"
    return c


def test_patrol_disabled_by_default():
    c = NightWatchCore(heal_mode="external")
    assert c.patrol_enabled is False


def test_patrol_walks_on_wide_platform_after_interval():
    c = farming()
    c.tick(W(2.0, hp=0.9, pos=(50, 90), span=SPAN()))       # 先排程
    assert patrolled(c, 60.0, SPAN()) in ("left", "right")
    assert c.stats.patrols == 1


def test_patrol_never_moves_on_narrow_platform():
    c = farming()
    narrow = SPAN(width=8.0, dl=4.0, dr=4.0)
    c.tick(W(2.0, hp=0.9, pos=(50, 90), span=narrow))
    for t in (60.0, 120.0, 180.0):
        assert patrolled(c, t, narrow) is None
    assert c.stats.patrols == 0


def test_patrol_avoids_the_side_without_room():
    c = farming()
    c._patrol_dir = "right"
    tight_right = SPAN(width=30.0, dl=20.0, dr=1.0)          # 右邊快到邊了
    c.tick(W(2.0, hp=0.9, pos=(50, 90), span=tight_right))
    assert patrolled(c, 60.0, tight_right) == "left"         # 改走左邊


def test_patrol_stays_put_when_both_sides_tight():
    c = farming()
    tight = SPAN(width=30.0, dl=2.0, dr=2.0)                 # 兩邊都沒空間
    c.tick(W(2.0, hp=0.9, pos=(50, 90), span=tight))
    assert patrolled(c, 60.0, tight) is None
    assert c.stats.patrols == 0


def test_patrol_needs_platform_reading():
    c = farming()
    c.tick(W(2.0, hp=0.9, pos=(50, 90), span=SPAN()))
    assert patrolled(c, 60.0, None) is None


def test_patrol_releases_attack_before_walking():
    c = farming()
    c.tick(W(2.0, hp=0.9, pos=(50, 90), span=SPAN()))        # 攻擊按住中
    acts = c.tick(W(60.0, hp=0.9, pos=(50, 90), span=SPAN()))
    verbs = [a.verb for a in acts]
    assert "release_attack" in verbs and "release_moves" in verbs


def test_patrol_only_while_farming():
    c = farming()
    c.tick(W(2.0, hp=0.9, pos=(50, 90), span=SPAN(), cmd="idle"))
    assert c.state == "IDLE_SAFE"
    for t in (60.0, 120.0):
        assert patrolled(c, t, SPAN()) is None
