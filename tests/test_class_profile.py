"""全職業支援測試：攻擊模式（按住／連點／技能輪替）、buff 輪替、MP 管理。

不同職業的打法差很多：弓箭手按住平射最大輸出、法師要輪替技能且吃 MP、
所有職業都要定時補 buff。這些行為全部由 ClassProfile 驅動並在此釘住。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.overnight import ClassProfile, NightWatchCore, WorldState  # noqa: E402


def W(now, **kw):
    return WorldState(now=now, **kw)


def SPAN(width=20.0, dl=10.0, dr=10.0):
    return {"width": width, "dist_left": dl, "dist_right": dr}


def verbs(acts):
    return [a.verb for a in acts]


def taps(acts):
    return [a.arg for a in acts if a.verb == "tap"]


def farming(profile, **kw):
    c = NightWatchCore(heal_mode="external", profile=profile, **kw)
    c.tick(W(0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    c.tick(W(1.0, hp=0.9, pos=(50, 90), span=SPAN(20)))
    assert c.state == "FARM"
    return c


# ---------------- 設定解析 ----------------

def test_profile_from_config_full():
    p = ClassProfile.from_config({
        "name": "mage",
        "attack": {"mode": "rotate", "key": "ctrl", "interval": 0.2,
                   "rotation": [{"key": "shift", "cooldown": 8},
                                {"key": "ctrl", "cooldown": 0.6}]},
        "buffs": [{"key": "f1", "every": 180}, {"key": "f2", "every": 300}],
        "mp": {"potion_key": "end", "threshold": 0.35, "cooldown": 3},
    })
    assert p.name == "mage" and p.attack_mode == "rotate"
    assert p.rotation == (("shift", 8.0), ("ctrl", 0.6))
    assert p.buffs == (("f1", 180.0), ("f2", 300.0))
    assert p.mp_key == "end" and p.mp_threshold == 0.35


def test_profile_defaults_and_empty_config():
    p = ClassProfile.from_config(None)
    assert p.attack_mode == "hold" and p.attack_key == "ctrl"
    assert p.rotation == () and p.buffs == () and p.mp_key is None
    # 缺 key 的項目要被忽略，不能炸
    p2 = ClassProfile.from_config({"buffs": [{"every": 60}],
                                   "attack": {"rotation": [{"cooldown": 5}]}})
    assert p2.buffs == () and p2.rotation == ()


# ---------------- 攻擊模式 ----------------

def test_hold_mode_holds_and_represses():
    c = farming(ClassProfile(attack_mode="hold"))
    acts = c.tick(W(2.0, hp=0.9, pos=(50, 90), span=SPAN()))
    assert "hold_attack" in verbs(acts)
    acts = c.tick(W(30.0, hp=0.9, pos=(50, 90), span=SPAN()))
    assert "repress_attack" in verbs(acts)


def test_tap_mode_taps_at_interval_and_never_holds():
    c = farming(ClassProfile(attack_mode="tap", attack_key="ctrl",
                             attack_interval=0.2))
    seen = []
    t = 2.0
    for _ in range(20):
        acts = c.tick(W(t, hp=0.9, pos=(50, 90), span=SPAN()))
        assert "hold_attack" not in verbs(acts)      # 連點模式不可按住
        seen += [a for a in taps(acts) if a == "ctrl"]
        t += 0.1
    assert 5 <= len(seen) <= 12                      # 約每 0.2s 一次（含抖動）


def test_rotate_mode_respects_cooldowns():
    c = farming(ClassProfile(attack_mode="rotate",
                             rotation=(("shift", 8.0), ("ctrl", 0.5))))
    a1 = taps(c.tick(W(2.0, hp=0.9, pos=(50, 90), span=SPAN())))
    assert a1 == ["shift"]                           # 先放大招
    a2 = taps(c.tick(W(2.6, hp=0.9, pos=(50, 90), span=SPAN())))
    assert a2 == ["ctrl"]                            # 大招冷卻中 → 用平砍
    a3 = taps(c.tick(W(2.7, hp=0.9, pos=(50, 90), span=SPAN())))
    assert a3 == []                                  # 兩顆都在冷卻 → 不亂按
    a4 = taps(c.tick(W(10.5, hp=0.9, pos=(50, 90), span=SPAN())))
    assert a4 == ["shift"]                           # 大招好了 → 再放


def test_switching_to_tap_releases_held_attack():
    c = farming(ClassProfile(attack_mode="hold"))
    c.tick(W(2.0, hp=0.9, pos=(50, 90), span=SPAN()))   # 先按住
    c.profile = ClassProfile(attack_mode="tap", attack_interval=0.1)
    acts = c.tick(W(3.0, hp=0.9, pos=(50, 90), span=SPAN()))
    assert "release_attack" in verbs(acts)


# ---------------- buff ----------------

def test_buffs_cast_on_start_and_recast_when_due():
    c = farming(ClassProfile(buffs=(("f1", 180.0), ("f2", 300.0))))
    t1 = taps(c.tick(W(2.0, hp=0.9, pos=(50, 90), span=SPAN())))
    t2 = taps(c.tick(W(2.4, hp=0.9, pos=(50, 90), span=SPAN())))
    assert t1 == ["f1"] and t2 == ["f2"]             # 一圈補一顆，不連打
    assert taps(c.tick(W(3.0, hp=0.9, pos=(50, 90), span=SPAN()))) == []
    # f1 到期 → 只補 f1
    assert taps(c.tick(W(185.0, hp=0.9, pos=(50, 90), span=SPAN()))) == ["f1"]
    assert c.stats.buffs_cast == 3


def test_no_buffs_configured_means_no_taps():
    c = farming(ClassProfile())
    for t in (2.0, 100.0, 400.0):
        assert taps(c.tick(W(t, hp=0.9, pos=(50, 90), span=SPAN()))) == []


# ---------------- MP ----------------

def test_mp_potion_on_low_mp_with_cooldown():
    c = farming(ClassProfile(mp_key="end", mp_threshold=0.3, mp_cooldown=3.0))
    assert taps(c.tick(W(2.0, hp=0.9, mp=0.9, pos=(50, 90), span=SPAN()))) == []
    assert taps(c.tick(W(3.0, hp=0.9, mp=0.2, pos=(50, 90), span=SPAN()))) == ["end"]
    assert taps(c.tick(W(4.0, hp=0.9, mp=0.2, pos=(50, 90), span=SPAN()))) == []
    assert taps(c.tick(W(6.5, hp=0.9, mp=0.2, pos=(50, 90), span=SPAN()))) == ["end"]
    assert c.stats.mp_potions == 2


def test_mp_ignored_without_key_or_reading():
    c = farming(ClassProfile(mp_key=None))
    assert taps(c.tick(W(2.0, hp=0.9, mp=0.05, pos=(50, 90), span=SPAN()))) == []
    c2 = farming(ClassProfile(mp_key="end", mp_threshold=0.3))
    assert taps(c2.tick(W(2.0, hp=0.9, mp=None, pos=(50, 90), span=SPAN()))) == []


def test_class_actions_only_while_farming():
    # 閒置/危險狀態不可施放技能或補 MP
    c = farming(ClassProfile(attack_mode="tap", buffs=(("f1", 60.0),),
                             mp_key="end", mp_threshold=0.9))
    c.tick(W(2.0, hp=0.9, mp=0.1, pos=(50, 90), span=SPAN(), cmd="idle"))
    assert c.state == "IDLE_SAFE"
    for t in (3.0, 10.0, 70.0):
        assert taps(c.tick(W(t, hp=0.9, mp=0.1, pos=(50, 90), span=SPAN()))) == []
